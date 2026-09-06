#!/usr/bin/env python3
"""Download, export, and validate the official YOLO26n model.

Run this script only from the repository's ``.export-venv``.  It records the
exact checkpoint/export hashes and validates reference PyTorch predictions
against predictions from the exported ONNX file.  Neither binary is tracked.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
import onnxruntime as ort
from ultralytics import YOLO

if hasattr(ort, "disable_telemetry_events"):
    ort.disable_telemetry_events()

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"
SAMPLE_IMAGE_URL = "https://ultralytics.com/images/bus.jpg"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def boxes(result: Any) -> np.ndarray[Any, np.dtype[np.float32]]:
    if result.boxes is None:
        return np.empty((0, 6), dtype=np.float32)
    return np.asarray(result.boxes.data.cpu(), dtype=np.float32)


def box_iou(first: np.ndarray, second: np.ndarray) -> float:
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[2]), float(second[2]))
    bottom = min(float(first[3]), float(second[3]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, float(first[2] - first[0])) * max(0.0, float(first[3] - first[1]))
    second_area = max(0.0, float(second[2] - second[0])) * max(0.0, float(second[3] - second[1]))
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def compare_predictions(reference: np.ndarray, exported: np.ndarray) -> dict[str, Any]:
    if reference.shape != exported.shape:
        raise RuntimeError(
            f"Reference/ONNX detection count differs: {reference.shape} vs {exported.shape}"
        )
    if reference.shape[0] == 0:
        raise RuntimeError("Validation image produced no detections; comparison is inconclusive")
    unmatched = set(range(exported.shape[0]))
    pairs: list[tuple[np.ndarray, np.ndarray, float]] = []
    for reference_box in reference:
        candidates = [index for index in unmatched if exported[index, 5] == reference_box[5]]
        if not candidates:
            raise RuntimeError(f"ONNX is missing reference class {int(reference_box[5])}")
        best = max(candidates, key=lambda index: box_iou(reference_box, exported[index]))
        overlap = box_iou(reference_box, exported[best])
        pairs.append((reference_box, exported[best], overlap))
        unmatched.remove(best)
    min_iou = min(pair[2] for pair in pairs)
    max_box_difference = max(
        float(np.max(np.abs(reference_box[:4] - exported_box[:4])))
        for reference_box, exported_box, _ in pairs
    )
    max_confidence_difference = max(
        abs(float(reference_box[4] - exported_box[4])) for reference_box, exported_box, _ in pairs
    )
    if min_iou < 0.9999 or max_box_difference > 0.01 or max_confidence_difference > 0.0001:
        raise RuntimeError(
            "Reference/ONNX predictions exceeded tolerance: "
            f"min_iou={min_iou:.4f}, max_box_difference={max_box_difference:.4f}, "
            f"max_confidence_difference={max_confidence_difference:.6f}"
        )
    return {
        "image": "ultralytics-bus.jpg",
        "detection_count": int(reference.shape[0]),
        "classes_equal": True,
        "min_iou": min_iou,
        "max_box_difference_px": max_box_difference,
        "max_confidence_difference": max_confidence_difference,
        "minimum_iou": 0.9999,
        "box_tolerance_px": 0.01,
        "confidence_tolerance": 0.0001,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "models/yolo26n-e2e.onnx")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "model_manifests/yolo26n-e2e.onnx.json",
    )
    parser.add_argument(
        "--validation-image",
        type=Path,
        default=REPOSITORY_ROOT / "harness/artifacts/ultralytics-bus.jpg",
    )
    args = parser.parse_args()

    expected_environment = REPOSITORY_ROOT / ".export-venv"
    if Path(sys.prefix).resolve() != expected_environment.resolve():
        raise SystemExit(
            f"Refusing to export outside {expected_environment}; current prefix is {sys.prefix}"
        )

    output = args.output.resolve()
    checkpoint = output.parent / "yolo26n.pt"
    if not checkpoint.exists():
        download(CHECKPOINT_URL, checkpoint)
    if not args.validation_image.exists():
        download(SAMPLE_IMAGE_URL, args.validation_image)

    model = YOLO(str(checkpoint))
    exported_path = Path(
        model.export(
            format="onnx",
            imgsz=640,
            nms=False,
            dynamic=False,
            simplify=True,
            batch=1,
            device="cpu",
        )
    ).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if exported_path != output:
        os.replace(exported_path, output)

    session = ort.InferenceSession(str(output), providers=["CPUExecutionProvider"])
    if len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1:
        raise RuntimeError("Expected one ONNX input and one ONNX output")
    input_shape = session.get_inputs()[0].shape
    output_shape = session.get_outputs()[0].shape
    if input_shape != [1, 3, 640, 640]:
        raise RuntimeError(f"Unexpected ONNX input shape: {input_shape}")
    if output_shape != [1, 300, 6]:
        raise RuntimeError(f"Expected YOLO26 end-to-end [1, 300, 6], got {output_shape}")

    predict_options = {
        "source": str(args.validation_image),
        "imgsz": 640,
        "conf": 0.25,
        "nms": False,
        "rect": False,
        "device": "cpu",
        "verbose": False,
    }
    reference_boxes = boxes(model.predict(**predict_options)[0])
    onnx_boxes = boxes(YOLO(str(output), task="detect").predict(**predict_options)[0])
    comparison = compare_predictions(reference_boxes, onnx_boxes)

    metadata = session.get_modelmeta().custom_metadata_map
    manifest = {
        "schema_version": 1,
        "model": "Ultralytics YOLO26n detect",
        "file": output.name,
        "sha256": sha256(output),
        "source_checkpoint": checkpoint.name,
        "source_checkpoint_sha256": sha256(checkpoint),
        "source_url": CHECKPOINT_URL,
        "license": "AGPL-3.0-only or Ultralytics Enterprise License",
        "license_url": "https://github.com/ultralytics/ultralytics/blob/main/LICENSE",
        "exported_at": datetime.now(UTC).isoformat(),
        "ultralytics_version": importlib.metadata.version("ultralytics"),
        "onnx_version": importlib.metadata.version("onnx"),
        "onnxruntime_version": importlib.metadata.version("onnxruntime"),
        "export": {
            "format": "onnx",
            "imgsz": 640,
            "nms": False,
            "dynamic": False,
            "simplify": True,
            "batch": 1,
            "device": "cpu",
        },
        "input_size": 640,
        "input_shape": input_shape,
        "output_shape": output_shape,
        "output_columns": ["x1", "y1", "x2", "y2", "confidence", "class_id"],
        "end_to_end": True,
        "target_classes": {"39": "bottle", "41": "cup", "67": "cell phone"},
        "model_metadata": metadata,
        "reference_comparison": comparison,
    }
    write_manifest(args.manifest.resolve(), manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
