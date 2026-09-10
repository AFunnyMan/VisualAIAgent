#!/usr/bin/env python3
"""Offline evaluation for isolated posture and drinking classifiers."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.behavior_events import BehaviorTimeline  # noqa: E402
from scripts.behavior_preprocess import preprocess_bgr  # noqa: E402
from scripts.train_behavior_model import IMAGE_SUFFIXES, inspect_dataset  # noqa: E402

os.environ.setdefault(
    "YOLO_CONFIG_DIR", str(REPOSITORY_ROOT / "harness/artifacts/behavior-settings")
)
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("YOLO_OFFLINE", "true")
os.environ.setdefault("ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS", "true")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_recorded_path(manifest_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else manifest_path.parent / path).resolve()


def accepted_label(probabilities: np.ndarray, names: dict[int, str]) -> tuple[str, float, float]:
    """Apply the fixed confidence and margin gate used by temporal replay."""
    values = np.asarray(probabilities, dtype=np.float32).reshape(-1)
    if (
        len(values) != len(names)
        or not np.isfinite(values).all()
        or np.any(values < -1e-4)
        or np.any(values > 1.0 + 1e-4)
        or abs(float(values.sum()) - 1.0) > 1e-4
    ):
        raise ValueError("Invalid classifier probabilities")
    order = np.argsort(values)[::-1]
    top1 = float(values[order[0]])
    top2 = float(values[order[1]]) if len(order) > 1 else 0.0
    label = names[int(order[0])] if top1 >= 0.75 and top1 - top2 >= 0.2 else "unknown"
    return label, top1, top1 - top2


def score_expected_events(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]], tolerance: float = 1.0
) -> dict[str, Any]:
    """Pair events one-to-one; default delay budget is sampling .5s + confirmation .5s."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Event tolerance must be finite and non-negative")
    normalized = []
    for item in expected:
        start, end = float(item["start"]), float(item["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start > end:
            raise ValueError("Invalid expected event window")
        normalized.append({"kind": str(item["kind"]), "start": start, "end": end})
    matched_expected: set[int] = set()
    matched_actual: set[int] = set()
    for actual_index, event in enumerate(actual):
        timestamp, kind = float(event["timestamp"]), str(event["kind"])
        candidates = [
            index
            for index, item in enumerate(normalized)
            if index not in matched_expected
            and item["kind"] == kind
            and item["start"] - tolerance <= timestamp <= item["end"] + tolerance
        ]
        if candidates:
            expected_index = min(
                candidates,
                key=lambda index: abs(
                    timestamp - (normalized[index]["start"] + normalized[index]["end"]) / 2
                ),
            )
            matched_expected.add(expected_index)
            matched_actual.add(actual_index)
    kinds = sorted({item["kind"] for item in normalized} | {str(item["kind"]) for item in actual})
    per_kind = {}
    for kind in kinds:
        expected_indexes = {i for i, item in enumerate(normalized) if item["kind"] == kind}
        actual_indexes = {i for i, item in enumerate(actual) if str(item["kind"]) == kind}
        true_positive = len(expected_indexes & matched_expected)
        per_kind[kind] = {
            "tp": true_positive,
            "fp": len(actual_indexes - matched_actual),
            "fn": len(expected_indexes - matched_expected),
        }
    return {
        "annotated": True,
        "tolerance_seconds": tolerance,
        "per_kind": per_kind,
        "tp": len(matched_expected),
        "fp": len(actual) - len(matched_actual),
        "fn": len(normalized) - len(matched_expected),
    }


def verified_source_groups(manifest: dict[str, Any], *, context: str) -> dict[str, str]:
    """Derive source splits from samples and cross-check the summary mapping."""
    samples, recorded = manifest.get("samples"), manifest.get("source_groups")
    if not isinstance(samples, list) or not isinstance(recorded, dict):
        raise ValueError(f"{context} has no sample-level source isolation record")
    derived: dict[str, str] = {}
    for sample in samples:
        digest, split = sample.get("source_sha256"), sample.get("split")
        if not isinstance(digest, str) or split not in {"train", "val", "test"}:
            raise ValueError(f"{context} has an invalid sample source record")
        previous = derived.setdefault(digest, split)
        if previous != split:
            raise ValueError(f"{context} source crosses splits: {digest}")
    normalized = {str(digest): str(split) for digest, split in recorded.items()}
    if derived != normalized:
        raise ValueError(f"{context} source_groups disagree with sample records")
    return derived


def model_source_groups(record: dict[str, Any]) -> dict[str, str]:
    """Recover sources from the exact samples covered by the recorded training fingerprint."""
    root = Path(record.get("dataset", {}).get("root", "")).expanduser()
    if not root.is_absolute():
        root = REPOSITORY_ROOT / root
    root = root.resolve()
    independent_val = record.get("validation", {}).get("independent_validation_performed") is True
    inspected = inspect_dataset(root, train_only=not independent_val)
    recorded_dataset = record.get("dataset", {})
    recorded_classes = {
        int(key): str(value) for key, value in recorded_dataset.get("classes", {}).items()
    }
    if (
        inspected["sha256"] != recorded_dataset.get("sha256")
        or inspected["counts"] != recorded_dataset.get("counts")
        or inspected["classes"] != recorded_classes
    ):
        raise ValueError(f"Model dataset fingerprint differs from training manifest: {root}")
    candidates = [root / "manifest.json", root.parent / "manifest.json"]
    for candidate in candidates:
        if candidate.is_file():
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
            samples = manifest.get("samples")
            if not isinstance(samples, list):
                raise ValueError(f"Model dataset has no sample records: {candidate}")
            task = root.name
            splits = {"train", "val"} if independent_val else {"train"}
            selected: dict[Path, dict[str, Any]] = {}
            groups: dict[str, str] = {}
            for sample in samples:
                if sample.get("task") != task or sample.get("split") not in splits:
                    continue
                relative = Path(str(sample.get("path", "")))
                path = (candidate.parent / relative).resolve()
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"Model sample escapes task dataset root: {path}") from exc
                if path in selected:
                    raise ValueError(f"Duplicate model sample record: {path}")
                digest, split = sample.get("source_sha256"), sample.get("split")
                if not isinstance(digest, str):
                    raise ValueError(f"Model sample has no source SHA: {path}")
                previous = groups.setdefault(digest, split)
                if previous != split:
                    raise ValueError(f"Model source crosses splits: {digest}")
                selected[path] = sample
            actual = {
                path.resolve()
                for split in splits
                for path in (root / split).rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            }
            if actual != set(selected):
                raise ValueError("Model dataset files differ from its sample manifest")
            for path, sample in selected.items():
                if sha256(path) != sample.get("sha256"):
                    raise ValueError(f"Model sample SHA mismatch: {path}")
            recorded_groups = manifest.get("source_groups")
            if not isinstance(recorded_groups, dict) or any(
                recorded_groups.get(digest) != split for digest, split in groups.items()
            ):
                raise ValueError("Model sample sources disagree with dataset source_groups")
            embedded = recorded_dataset.get("source_groups")
            if (
                embedded is not None
                and {str(digest): str(split) for digest, split in embedded.items()} != groups
            ):
                raise ValueError(
                    "Embedded model source_groups disagree with actual training samples"
                )
            return groups
    raise ValueError(f"Cannot recover training source groups for {record['_path']}")


def training_source_hashes(record: dict[str, Any]) -> set[str]:
    return {digest for digest, split in model_source_groups(record).items() if split == "train"}


def load_manifest(path: Path, expected_task: str) -> dict[str, Any]:
    path = path.resolve()
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "completed":
        raise ValueError(f"{expected_task} training is not completed")
    preprocessing = record.get("preprocessing", {})
    if (
        preprocessing.get("layout") != "NCHW"
        or preprocessing.get("dtype") != "float32"
        or preprocessing.get("color") != "RGB"
        or preprocessing.get("resize") != "aspect-ratio-preserving letterbox"
        or preprocessing.get("range") != [0.0, 1.0]
        or preprocessing.get("fill_rgb") != [114, 114, 114]
    ):
        raise ValueError(f"{expected_task} manifest has incompatible preprocessing")
    names = record.get("dataset", {}).get("classes")
    if not isinstance(names, dict):
        raise ValueError(f"{expected_task} manifest has no class mapping")
    names = {int(key): str(value) for key, value in names.items()}
    expected = {
        "posture": {"seated", "standing", "empty"},
        "drinking": {"drinking", "not_drinking"},
    }
    if set(names.values()) != expected[expected_task] or set(names) != set(range(len(names))):
        raise ValueError(f"{expected_task} class mapping is unexpected")
    checkpoint = resolve_recorded_path(path, record["selected_checkpoint"])
    onnx = resolve_recorded_path(path, record["onnx"])
    for model_path, field in (
        (checkpoint, "selected_checkpoint_sha256"),
        (onnx, "onnx_sha256"),
    ):
        if not model_path.is_file() or sha256(model_path) != record[field]:
            raise ValueError(f"{expected_task} model SHA mismatch: {model_path}")
    return {**record, "_path": path, "_checkpoint": checkpoint, "_onnx": onnx, "_names": names}


class ModelPair:
    def __init__(self, record: dict[str, Any]):
        import onnxruntime as ort
        import torch
        from ultralytics import YOLO

        self.torch = torch
        self.names = record["_names"]
        self.size = int(record["preprocessing"]["imgsz"])
        self.pt = YOLO(str(record["_checkpoint"])).model.float().cpu().eval()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(
            str(record["_onnx"]), sess_options=options, providers=["CPUExecutionProvider"]
        )
        model_names = {int(k): str(v) for k, v in self.pt.names.items()}
        if model_names != self.names:
            raise ValueError("PT class names differ from training manifest")
        input_meta = self.session.get_inputs()
        if len(input_meta) != 1 or input_meta[0].type != "tensor(float)":
            raise ValueError("ONNX must have one FP32 input")
        shape = input_meta[0].shape
        if shape != [1, 3, self.size, self.size]:
            raise ValueError(f"ONNX input must be static NCHW, got {shape}")
        output_meta = self.session.get_outputs()
        if (
            len(output_meta) != 1
            or output_meta[0].type != "tensor(float)"
            or output_meta[0].shape != [1, len(self.names)]
        ):
            raise ValueError("ONNX output must be static FP32 [1, number_of_classes]")
        metadata_names = self.session.get_modelmeta().custom_metadata_map.get("names")
        if metadata_names is None:
            raise ValueError("ONNX metadata has no class names")
        try:
            parsed_names = ast.literal_eval(metadata_names)
            parsed_names = {int(key): str(value) for key, value in parsed_names.items()}
        except (SyntaxError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError("ONNX metadata class names are invalid") from exc
        if parsed_names != self.names:
            raise ValueError("ONNX metadata class names differ from training manifest")
        self.input_name = input_meta[0].name

    def predict(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        tensor = preprocess_bgr(frame, self.size)
        with self.torch.inference_mode():
            raw = self.pt(self.torch.from_numpy(tensor))
        pt = raw[0] if isinstance(raw, tuple) else raw
        if isinstance(pt, (tuple, list)):
            pt = pt[0]
        pt_values = np.asarray(pt.detach().cpu(), dtype=np.float32).reshape(-1)
        ort_values = np.asarray(
            self.session.run(None, {self.input_name: tensor})[0], dtype=np.float32
        ).reshape(-1)
        difference = float(np.max(np.abs(pt_values - ort_values)))
        if difference > 1e-4 or int(np.argmax(pt_values)) != int(np.argmax(ort_values)):
            raise RuntimeError(f"PT/ONNX disagreement: max_abs={difference:.8g}")
        return ort_values, difference


def evaluate_samples(
    root: Path, manifest: dict[str, Any], models: dict[str, ModelPair], split: str
) -> tuple[dict[str, Any], float]:
    matrices: dict[str, Counter] = {task: Counter() for task in models}
    gated: dict[str, Counter] = {task: Counter() for task in models}
    maximum_difference = 0.0
    selected = [sample for sample in manifest["samples"] if sample.get("split") == split]
    if not selected:
        raise ValueError(f"Data manifest contains no samples for split={split}")
    for sample in selected:
        task = sample["task"]
        if task not in models:
            raise ValueError(f"Unknown sample task: {task}")
        path = (root / sample["path"]).resolve()
        if not path.is_file() or sha256(path) != sample["sha256"]:
            raise ValueError(f"Sample SHA mismatch: {path}")
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError(f"Unreadable sample: {path}")
        probabilities, difference = models[task].predict(frame)
        maximum_difference = max(maximum_difference, difference)
        predicted = models[task].names[int(np.argmax(probabilities))]
        matrices[task][(sample["label"], predicted)] += 1
        accepted, _, _ = accepted_label(probabilities, models[task].names)
        outcome = (
            "unknown"
            if accepted == "unknown"
            else "accepted_correct"
            if accepted == sample["label"]
            else "accepted_wrong"
        )
        gated[task][(sample["label"], outcome)] += 1
    result = {}
    for task, matrix in matrices.items():
        labels = list(models[task].names.values())
        total = sum(matrix.values())
        correct = sum(matrix[(label, label)] for label in labels)
        result[task] = {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total else None,
            "per_class_count": {label: sum(matrix[(label, p)] for p in labels) for label in labels},
            "confusion_matrix": {
                actual: {predicted: matrix[(actual, predicted)] for predicted in labels}
                for actual in labels
            },
            "fixed_gate_by_class": {
                label: {
                    "total": sum(
                        gated[task][(label, outcome)]
                        for outcome in ("accepted_correct", "accepted_wrong", "unknown")
                    ),
                    "accepted_correct": gated[task][(label, "accepted_correct")],
                    "accepted_wrong": gated[task][(label, "accepted_wrong")],
                    "unknown": gated[task][(label, "unknown")],
                }
                for label in labels
            },
        }
    return result, maximum_difference


def replay_videos(
    annotations: dict[str, Any], models: dict[str, ModelPair], output: Path, split: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    predictions_path = output / "video-predictions.jsonl"
    evidence = output / "event-evidence"
    events: list[dict[str, Any]] = []
    evidence.mkdir()
    source_summaries = []
    selected_sources = [
        source for source in annotations["videos"] if source.get("split", "train") == split
    ]
    if not selected_sources:
        raise ValueError(f"Annotations contain no source videos for split={split}")
    with predictions_path.open("w", encoding="utf-8") as stream:
        for source in selected_sources:
            timeline = BehaviorTimeline()
            source_events: list[dict[str, Any]] = []
            path = Path(source["path"]).expanduser()
            if not path.is_absolute():
                path = REPOSITORY_ROOT / path
            path = path.resolve()
            if not path.is_file() or sha256(path) != source["sha256"]:
                raise ValueError(f"Source video SHA mismatch: {path}")
            cap = cv2.VideoCapture(str(path))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
                    raise ValueError(f"Unreadable source video: {path}")
                frame_index = sample_index = 0
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    timestamp = frame_index / fps
                    if timestamp + 1e-8 >= sample_index / 2.0:
                        sample_index += 1
                        row: dict[str, Any] = {
                            "source_sha256": source["sha256"],
                            "frame_index": frame_index,
                            "timestamp": timestamp,
                        }
                        accepted = {}
                        for task, model in models.items():
                            probs, difference = model.predict(frame)
                            label, confidence, margin = accepted_label(probs, model.names)
                            accepted[task] = label
                            row[task] = {
                                "label": label,
                                "probabilities": {
                                    model.names[i]: float(v) for i, v in enumerate(probs)
                                },
                                "top1": confidence,
                                "margin": margin,
                                "pt_onnx_max_abs": difference,
                            }
                        interpreted = timeline.observe(
                            timestamp,
                            accepted["posture"],
                            accepted["drinking"],
                        )
                        row["timeline"] = interpreted
                        for event in interpreted["events"]:
                            event_row = {
                                **event,
                                "source_sha256": source["sha256"],
                                "frame_index": frame_index,
                            }
                            if len(events) < 12:
                                target = evidence / f"{len(events) + 1:02d}-{event['kind']}.jpg"
                                if not cv2.imwrite(str(target), frame):
                                    raise RuntimeError("Failed to write event evidence")
                                event_row["evidence"] = str(target)
                            events.append(event_row)
                            source_events.append(event_row)
                        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    frame_index += 1
                decoded_duration = frame_index / fps
                expected_duration = float(source["duration_seconds"])
                if abs(decoded_duration - expected_duration) > max(0.1, 2 / fps):
                    raise ValueError(
                        f"Decoded duration differs from reviewed source: {path} "
                        f"({decoded_duration:.6f}s vs {expected_duration:.6f}s)"
                    )
            finally:
                cap.release()
            synthetic = timeline.observe(
                float(source["duration_seconds"]) + 0.5, "empty", "drinking", fresh=False
            )
            if synthetic["events"]:
                raise RuntimeError("Synthetic stale observation produced an event")
            scoring = (
                score_expected_events(source["expected_events"], source_events)
                if "expected_events" in source
                else {
                    "annotated": False,
                    "reason": "expected_events absent; no ground-truth zero assumed",
                }
            )
            source_summaries.append(
                {
                    "source_sha256": source["sha256"],
                    "event_count": len(source_events),
                    "events": dict(Counter(event["kind"] for event in source_events)),
                    "event_scoring": scoring,
                }
            )
    return events, {
        "sample_fps": 2.0,
        "confidence_threshold": 0.75,
        "margin_threshold": 0.2,
        "events": dict(Counter(event["kind"] for event in events)),
        "event_count": len(events),
        "evidence_count": min(12, len(events)),
        "synthetic_fresh_false_events": 0,
        "sources": source_summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posture-manifest", type=Path, required=True)
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--data-manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "val", "test"), default="train")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    posture = load_manifest(args.posture_manifest, "posture")
    drinking = load_manifest(args.drinking_manifest, "drinking")
    data_path = args.data_manifest.resolve()
    annotations_path = args.annotations.resolve()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    if data.get("annotations_sha256") != sha256(annotations_path):
        raise ValueError("Annotations SHA differs from data manifest")
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    selected_source_hashes = {
        str(source["sha256"])
        for source in annotations["videos"]
        if source.get("split", "train") == args.split
    }
    if not selected_source_hashes:
        raise ValueError(f"Annotations contain no sources for split={args.split}")
    recorded_groups = verified_source_groups(data, context="Evaluation data manifest")
    recorded_selected_sources = {
        str(digest)
        for digest, source_split in recorded_groups.items()
        if source_split == args.split
    }
    if selected_source_hashes != recorded_selected_sources:
        raise ValueError("Annotations and data manifest disagree on selected split sources")
    data_train_sources = {
        str(digest) for digest, source_split in recorded_groups.items() if source_split == "train"
    }
    groups_by_task = {
        "posture": model_source_groups(posture),
        "drinking": model_source_groups(drinking),
    }
    model_train_sources = {
        digest
        for groups in groups_by_task.values()
        for digest, source_split in groups.items()
        if source_split == "train"
    }
    if data_train_sources and model_train_sources != data_train_sources:
        raise ValueError(
            "Training model and evaluation data manifests disagree on training sources"
        )
    overlap = selected_source_hashes & model_train_sources
    if args.split != "train" and overlap:
        raise ValueError(f"Selected {args.split} source overlaps model training sources")
    held_out = args.split != "train" and not overlap
    model_val_sources = {
        digest
        for groups in groups_by_task.values()
        for digest, source_split in groups.items()
        if source_split == "val"
    }
    val_overlap = selected_source_hashes & model_val_sources
    if args.split == "test" and val_overlap:
        raise ValueError("Selected test source overlaps model validation/model-selection sources")
    used_for_model_selection = args.split == "val" and bool(val_overlap)
    import torch

    torch.set_num_threads(2)
    models = {"posture": ModelPair(posture), "drinking": ModelPair(drinking)}
    samples, maximum_difference = evaluate_samples(data_path.parent, data, models, args.split)
    events, replay = replay_videos(annotations, models, output, args.split)
    for task_result in samples.values():
        task_result["independent_accuracy"] = args.split == "test" and held_out and not val_overlap
        task_result["interpretation"] = (
            "training-set resubstitution; not an independent accuracy estimate"
            if args.split == "train"
            else "development validation; not final test"
            if args.split == "val"
            else "held out from training and model selection"
        )
    summary = {
        "status": "completed",
        "completed_at": datetime.now(UTC).isoformat(),
        "offline_only": True,
        "evaluation_split": args.split,
        "data_manifest_sha256": sha256(data_path),
        "annotations_sha256": sha256(annotations_path),
        "temporal_code_sha256": sha256(Path(__file__).with_name("behavior_events.py")),
        "evaluator_code_sha256": sha256(Path(__file__)),
        "held_out_from_training": held_out,
        "used_for_model_selection": used_for_model_selection,
        "models": {
            task: {
                "manifest": str(record["_path"]),
                "checkpoint_sha256": record["selected_checkpoint_sha256"],
                "onnx_sha256": record["onnx_sha256"],
                "classes": record["_names"],
            }
            for task, record in (("posture", posture), ("drinking", drinking))
        },
        "pt_onnx": {"maximum_absolute_difference": maximum_difference, "tolerance": 1e-4},
        "sample_evaluation": samples,
        "video_replay": replay,
        "events": events,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
