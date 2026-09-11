#!/usr/bin/env python3
"""Evaluate an isolated laptop-lid ONNX classifier on reviewed development splits."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from visual_ai_agent.behavior import OnnxClassifier, load_behavior_manifest


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(
    model: Path,
    training_manifest: Path,
    dataset_manifest: Path,
    output: Path,
) -> dict:
    if output.exists():
        raise FileExistsError(f"Output already exists; refusing to overwrite: {output}")
    training = load_behavior_manifest(training_manifest, "laptop")
    dataset = json.loads(dataset_manifest.read_text(encoding="utf-8"))
    if model.resolve() != training["_onnx"] or sha256(model) != training.get("onnx_sha256"):
        raise ValueError("ONNX does not match completed training manifest")
    classifier = OnnxClassifier(training)
    rows = []
    timings = []
    confusion = Counter()
    for sample in dataset["samples"]:
        image_path = dataset_manifest.parent / sample["path"]
        if not image_path.is_file():
            image_path = Path(training["dataset"]["root"]).parent / sample["path"]
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or sha256(image_path) != sample["sha256"]:
            raise ValueError(f"Dataset image missing or changed: {sample['path']}")
        started = time.perf_counter()
        result = classifier.predict(image)
        timings.append((time.perf_counter() - started) * 1000)
        prediction = result["label"]
        confusion[(sample["split"], sample["label"], prediction)] += 1
        rows.append(
            {
                "path": sample["path"],
                "split": sample["split"],
                "expected": sample["label"],
                "predicted": prediction,
                "confidence": result["confidence"],
                "margin": result["margin"],
            }
        )
    splits = {}
    for split in ("train", "val"):
        subset = [row for row in rows if row["split"] == split]
        accepted = [row for row in subset if row["predicted"] != "unknown"]
        correct = sum(row["predicted"] == row["expected"] for row in subset)
        splits[split] = {
            "total": len(subset),
            "correct": correct,
            "unknown": len(subset) - len(accepted),
            "accuracy_all": correct / len(subset) if subset else None,
            "accuracy_accepted": (
                sum(row["predicted"] == row["expected"] for row in accepted) / len(accepted)
                if accepted
                else None
            ),
        }
    ordered = np.asarray(sorted(timings), dtype=np.float64)
    result = {
        "schema_version": 1,
        "model_sha256": sha256(model),
        "dataset_manifest_sha256": sha256(dataset_manifest),
        "gate": {
            "confidence": 0.75,
            "margin": 0.2,
            "implementation": "visual_ai_agent.behavior.OnnxClassifier",
        },
        "splits": splits,
        "confusion": [
            {"split": key[0], "expected": key[1], "predicted": key[2], "count": count}
            for key, count in sorted(confusion.items())
        ],
        "cpu_inference_ms": {
            "count": len(timings),
            "p50": float(np.percentile(ordered, 50)),
            "p95": float(np.percentile(ordered, 95)),
            "max": float(ordered[-1]),
        },
        "predictions": rows,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = evaluate(
        args.model,
        args.training_manifest,
        args.dataset_manifest,
        args.output,
    )
    print(json.dumps(summary["splits"], ensure_ascii=False))
