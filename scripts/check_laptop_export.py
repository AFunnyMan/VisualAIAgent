#!/usr/bin/env python3
"""Check laptop classifier PT/ONNX output parity on the reviewed development frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import torch

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault(
    "YOLO_CONFIG_DIR", str(REPOSITORY_ROOT / "harness/artifacts/behavior-settings")
)
os.environ.setdefault("YOLO_OFFLINE", "true")

from ultralytics import YOLO  # noqa: E402

from visual_ai_agent.behavior import OnnxClassifier, load_behavior_manifest  # noqa: E402
from visual_ai_agent.behavior_preprocess import preprocess_bgr  # noqa: E402


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(training_manifest: Path, dataset_manifest: Path, output: Path) -> dict:
    record = load_behavior_manifest(training_manifest, "laptop")
    checkpoint = Path(record["selected_checkpoint"]).resolve()
    if not checkpoint.is_file() or sha256(checkpoint) != record["selected_checkpoint_sha256"]:
        raise ValueError("Checkpoint does not match training manifest")
    dataset = json.loads(dataset_manifest.read_text(encoding="utf-8"))
    classifier = OnnxClassifier(record)
    model = YOLO(str(checkpoint)).model.eval().cpu()
    differences = []
    argmax_disagreements = 0
    with torch.inference_mode():
        for sample in dataset["samples"]:
            image_path = dataset_manifest.parent / sample["path"]
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None or sha256(image_path) != sample["sha256"]:
                raise ValueError(f"Dataset image missing or changed: {sample['path']}")
            tensor = preprocess_bgr(image, classifier.size, classifier.roi)
            pt_output = model(torch.from_numpy(tensor))
            if isinstance(pt_output, tuple):
                pt_output = pt_output[0]
            pt_values = pt_output.detach().cpu().numpy().reshape(-1)
            ort_values = np.asarray(
                classifier.session.run([classifier.output_name], {classifier.input_name: tensor})[0]
            ).reshape(-1)
            differences.append(float(np.max(np.abs(pt_values - ort_values))))
            argmax_disagreements += int(np.argmax(pt_values) != np.argmax(ort_values))
    result = {
        "schema_version": 1,
        "checkpoint_sha256": sha256(checkpoint),
        "onnx_sha256": record["onnx_sha256"],
        "dataset_manifest_sha256": sha256(dataset_manifest),
        "samples": len(differences),
        "argmax_disagreements": argmax_disagreements,
        "maximum_probability_difference": max(differences),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.training_manifest, args.dataset_manifest, args.output)))
