#!/usr/bin/env python3
"""Compare posture classifiers on explicitly reviewed development videos."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from scripts.evaluate_behavior_models import ModelPair, accepted_label, load_manifest
from scripts.prepare_behavior_data import label_at, validate_intervals
from scripts.training_source_policy import verify_development_sources

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(
    annotations_path: Path,
    registry_path: Path,
    model_paths: dict[str, Path],
    output: Path,
    sample_fps: float = 2.0,
    source_prefixes: tuple[str, ...] = (),
) -> dict:
    if output.exists():
        raise ValueError("Output must not exist")
    if not 0 < sample_fps <= 10 or not math.isfinite(sample_fps):
        raise ValueError("sample_fps must be finite and in (0,10]")
    annotations_path = annotations_path.resolve()
    registry_path = registry_path.resolve()
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    sources = [
        source
        for source in annotations["videos"]
        if not source_prefixes
        or any(source["sha256"].startswith(prefix) for prefix in source_prefixes)
    ]
    if not sources:
        raise ValueError("No annotation source matches --source-prefix")
    resolved_sources = []
    for source in sources:
        path = Path(source["path"])
        path = path if path.is_absolute() else REPOSITORY_ROOT / path
        path = path.resolve()
        if sha256(path) != source["sha256"]:
            raise ValueError(f"Source SHA mismatch: {path}")
        validate_intervals(source["posture"], "posture", source["duration_seconds"])
        resolved_sources.append(path)
    verified = verify_development_sources(resolved_sources, registry_path)

    records = {name: load_manifest(path, "posture") for name, path in model_paths.items()}
    models = {name: ModelPair(record) for name, record in records.items()}
    matrices = {name: Counter() for name in models}
    gates = {name: Counter() for name in models}
    per_source = []
    max_differences = {name: 0.0 for name in models}
    critical = {name: Counter() for name in models}
    output.mkdir(parents=True, exist_ok=False)
    prediction_path = output / "predictions.jsonl"
    with prediction_path.open("w", encoding="utf-8") as stream:
        for source, path in zip(sources, resolved_sources, strict=True):
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                raise ValueError(f"Cannot decode {path}")
            fps = cap.get(cv2.CAP_PROP_FPS)
            sampled = 0
            frame_index = sample_index = 0
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    timestamp = frame_index / fps
                    if timestamp + 1e-8 >= sample_index / sample_fps:
                        sample_index += 1
                        truth = label_at(source["posture"], timestamp)
                        if truth is not None:
                            sampled += 1
                            row = {
                                "source_sha256": source["sha256"],
                                "frame_index": frame_index,
                                "timestamp": timestamp,
                                "truth": truth,
                                "models": {},
                            }
                            for name, model in models.items():
                                probabilities, difference = model.predict(frame)
                                max_differences[name] = max(max_differences[name], difference)
                                argmax = model.names[int(probabilities.argmax())]
                                accepted, top1, margin = accepted_label(probabilities, model.names)
                                matrices[name][(truth, argmax)] += 1
                                outcome = (
                                    "unknown"
                                    if accepted == "unknown"
                                    else "accepted_correct"
                                    if accepted == truth
                                    else "accepted_wrong"
                                )
                                gates[name][(truth, outcome)] += 1
                                if source["sha256"].startswith("f9e748") and 84 <= timestamp < 96:
                                    critical[name][accepted] += 1
                                row["models"][name] = {
                                    "argmax": argmax,
                                    "accepted": accepted,
                                    "top1": top1,
                                    "margin": margin,
                                    "probabilities": {
                                        model.names[index]: float(value)
                                        for index, value in enumerate(probabilities)
                                    },
                                    "pt_onnx_max_abs": difference,
                                }
                            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    frame_index += 1
            finally:
                cap.release()
            per_source.append(
                {
                    "source_sha256": source["sha256"],
                    "sample_count": sampled,
                }
            )

    labels = ["empty", "seated", "standing"]
    model_summaries = {}
    for name, record in records.items():
        total = sum(matrices[name].values())
        model_summaries[name] = {
            "manifest": str(record["_path"]),
            "checkpoint_sha256": record["selected_checkpoint_sha256"],
            "onnx_sha256": record["onnx_sha256"],
            "sample_count": total,
            "argmax_correct": sum(matrices[name][(label, label)] for label in labels),
            "confusion_matrix": {
                truth: {predicted: matrices[name][(truth, predicted)] for predicted in labels}
                for truth in labels
            },
            "fixed_gate": {
                truth: {
                    outcome: gates[name][(truth, outcome)]
                    for outcome in ("accepted_correct", "accepted_wrong", "unknown")
                }
                for truth in labels
            },
            "critical_23_05_02_84_96": dict(critical[name]),
            "pt_onnx_max_abs": max_differences[name],
        }
    result = {
        "status": "completed",
        "completed_at": datetime.now(UTC).isoformat(),
        "interpretation": "Development/training-source comparison; not independent acceptance.",
        "annotations": {"path": str(annotations_path), "sha256": sha256(annotations_path)},
        "source_registry": {
            "path": str(registry_path),
            "sha256": sha256(registry_path),
            "verified_development_sources": verified,
        },
        "sample_fps": sample_fps,
        "source_prefixes": list(source_prefixes),
        "temporal_replay": (
            "not performed; 2 fps is classification-only and cannot satisfy the production "
            "0.25 s max-gap contract"
        ),
        "fixed_gate": {"confidence": 0.75, "margin": 0.2},
        "models": model_summaries,
        "per_source": per_source,
    }
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument(
        "--model", action="append", nargs=2, metavar=("NAME", "MANIFEST"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument("--source-prefix", action="append", default=[])
    args = parser.parse_args()
    result = evaluate(
        args.annotations,
        args.source_registry,
        {name: Path(path) for name, path in args.model},
        args.output,
        args.sample_fps,
        tuple(args.source_prefix),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
