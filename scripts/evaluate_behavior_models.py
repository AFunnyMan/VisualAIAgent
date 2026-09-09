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
    root: Path, manifest: dict[str, Any], models: dict[str, ModelPair]
) -> tuple[dict[str, Any], float]:
    matrices: dict[str, Counter] = {task: Counter() for task in models}
    maximum_difference = 0.0
    for sample in manifest["samples"]:
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
            "independent_accuracy": False,
            "interpretation": "train-only resubstitution; not an independent accuracy estimate",
        }
    return result, maximum_difference


def replay_videos(
    annotations: dict[str, Any], models: dict[str, ModelPair], output: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    timeline = BehaviorTimeline()
    predictions_path = output / "video-predictions.jsonl"
    evidence = output / "event-evidence"
    events: list[dict[str, Any]] = []
    evidence.mkdir()
    sequence_time = 0.0
    with predictions_path.open("w", encoding="utf-8") as stream:
        for source in annotations["videos"]:
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
                            sequence_time + timestamp,
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
                        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    frame_index += 1
                sequence_time += float(source["duration_seconds"]) + 2.0
            finally:
                cap.release()
    before = len(events)
    synthetic = timeline.observe(sequence_time, "empty", "drinking", fresh=False)
    if synthetic["events"] or len(events) != before:
        raise RuntimeError("Synthetic stale observation produced an event")
    return events, {
        "sample_fps": 2.0,
        "confidence_threshold": 0.75,
        "margin_threshold": 0.2,
        "events": dict(Counter(event["kind"] for event in events)),
        "event_count": len(events),
        "evidence_count": min(12, len(events)),
        "synthetic_fresh_false_events": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posture-manifest", type=Path, required=True)
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--data-manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    import torch

    torch.set_num_threads(2)
    models = {"posture": ModelPair(posture), "drinking": ModelPair(drinking)}
    samples, maximum_difference = evaluate_samples(data_path.parent, data, models)
    events, replay = replay_videos(annotations, models, output)
    summary = {
        "status": "completed",
        "completed_at": datetime.now(UTC).isoformat(),
        "offline_only": True,
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
