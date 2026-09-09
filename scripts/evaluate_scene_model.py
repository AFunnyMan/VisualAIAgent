"""Evaluate an isolated three-class ONNX candidate without changing production models."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "harness/artifacts/finetune-20260909/settings"))
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("YOLO_OFFLINE", "true")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from scripts.score_detections import overlap, score_dataset  # noqa: E402
from visual_ai_agent.models import Detection  # noqa: E402
from visual_ai_agent.vision import (  # noqa: E402
    CupScaleRecheckDetector,
    YoloOnnxDetector,
    _parse_names,
    inverse_letterbox,
    letterbox,
    region_for_box,
)

NAMES = {0: "bottle", 1: "cup", 2: "cell phone"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode(raw, transform, confidence, names=None):
    names = NAMES if names is None else names
    raw = np.asarray(raw)
    if raw.shape != (1, 300, 6) or not np.isfinite(raw).all():
        raise ValueError("Expected finite static [1,300,6] candidate predictions")
    selected = raw[0][raw[0, :, 4] >= confidence]
    boxes = inverse_letterbox(selected[:, :4], transform)
    detections = []
    for row, box in zip(selected, boxes, strict=True):
        class_id = int(round(float(row[5])))
        if class_id not in names or abs(class_id - float(row[5])) > 0.001:
            raise ValueError("Unexpected experimental class ID")
        if names[class_id] not in NAMES.values():
            continue
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        detections.append(
            Detection(
                category=names[class_id],
                confidence=float(row[4]),
                bbox=tuple(float(x) for x in box),
                region=region_for_box(tuple(box), transform.original_width),
            )
        )
    return sorted(detections, key=lambda d: d.confidence, reverse=True)


def expected_names(preserve_coco_head=False):
    if not preserve_coco_head:
        return NAMES
    manifest = json.loads((ROOT / "model_manifests/yolo26n-e2e.onnx.json").read_text())
    names = _parse_names(manifest["model_metadata"]["names"])
    if names is None or len(names) != 80:
        raise ValueError("Official manifest must contain 80 class names")
    return names


class ExperimentalDetector:
    def __init__(self, path, expected_sha, confidence=0.35, preserve_coco_head=False):
        if sha256(path) != expected_sha:
            raise ValueError("Candidate checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, 640, 640]:
            raise ValueError("Candidate input must be static [1,3,640,640]")
        if len(outputs) != 1 or outputs[0].shape != [1, 300, 6]:
            raise ValueError("Candidate output must be static [1,300,6]")
        names = _parse_names(self.session.get_modelmeta().custom_metadata_map.get("names"))
        if names != expected_names(preserve_coco_head):
            raise ValueError(f"Unexpected candidate classes: {names}")
        self.names = names
        self.input_name = inputs[0].name
        self.confidence = confidence

    def detect(self, frame):
        tensor, transform = letterbox(frame, 640)
        raw = self.session.run(None, {self.input_name: tensor})[0]
        return decode(raw, transform, self.confidence, self.names)


def compare_export(reference, candidate):
    unused = set(range(len(candidate)))
    pairs = []
    for original in reference:
        options = [i for i in unused if candidate[i].category == original.category]
        if not options:
            return {"passed": False, "reason": "count_or_class_mismatch"}
        index = max(options, key=lambda i: overlap(original.bbox, candidate[i].bbox))
        unused.remove(index)
        other = candidate[index]
        pairs.append(
            {
                "iou": overlap(original.bbox, other.bbox),
                "confidence_difference": abs(original.confidence - other.confidence),
                "max_box_difference_px": max(
                    abs(a - b) for a, b in zip(original.bbox, other.bbox, strict=True)
                ),
            }
        )
    passed = not unused and all(
        p["iou"] >= 0.999
        and p["confidence_difference"] <= 0.001
        and p["max_box_difference_px"] <= 0.1
        for p in pairs
    )
    return {"passed": passed, "pairs": pairs, "empty_pair": not reference and not candidate}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--dataset", action="append", required=True, help="NAME=MANIFEST_JSON")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--preserve-coco-head", action="store_true")
    args = p.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an evaluation")
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(2)
    pt = YOLO(str(args.checkpoint)).model.cpu().float().eval()
    names = expected_names(args.preserve_coco_head)
    if pt.names != names:
        raise ValueError(f"Checkpoint classes differ: {pt.names}")
    # Ultralytics exporter selects this branch for nms=False. A saved checkpoint
    # defaults to one-to-many inference, although both heads were trained.
    pt.end2end = True
    if not pt.end2end:
        raise ValueError("Checkpoint has no end-to-end branch to compare with the export")
    candidate = ExperimentalDetector(
        args.candidate, args.candidate_sha, preserve_coco_head=args.preserve_coco_head
    )
    baseline = YoloOnnxDetector(ROOT / "models/yolo26n-e2e.onnx", confidence=0.35)
    detectors = {
        "official_single": baseline,
        "production_two_pass": CupScaleRecheckDetector(baseline),
        "finetuned_single": candidate,
    }
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "candidate_sha256": args.candidate_sha,
        "checkpoint_sha256": sha256(args.checkpoint),
        "reference_head": "one2one end2end=True, matching exporter nms=False",
        "preserve_coco_head": args.preserve_coco_head,
        "confidence": 0.35,
        "iou": 0.5,
        "limits": "Offline still-image evidence, not live event or independent-instance acceptance",
        "datasets": {},
        "export_comparisons": [],
    }
    for specification in args.dataset:
        name, filename = specification.split("=", 1)
        if name in result["datasets"]:
            raise ValueError("Duplicate dataset name")
        manifest_path = Path(filename).resolve()
        manifest = json.loads(manifest_path.read_text())
        rows = {key: [] for key in detectors}
        timings = {key: [] for key in detectors}
        if not manifest["samples"]:
            raise ValueError("Empty dataset")
        for sample in manifest["samples"]:
            path = Path(sample["image_path"])
            if not path.is_absolute():
                path = manifest_path.parent / path
            if sample.get("sha256") and sha256(path) != sample["sha256"]:
                raise ValueError(f"Image checksum mismatch: {sample['id']}")
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"Unreadable image: {sample['id']}")
            predictions = {}
            for key, detector in detectors.items():
                start = time.perf_counter()
                predictions[key] = detector.detect(frame)
                timings[key].append(1000 * (time.perf_counter() - start))
                rows[key].append(
                    {
                        "id": sample["id"],
                        "detections": [d.model_dump(mode="json") for d in predictions[key]],
                    }
                )
            # Compare every evaluated image, using the exact same input tensor.
            tensor, transform = letterbox(frame, 640)
            with torch.inference_mode():
                raw = pt(torch.from_numpy(tensor))
            if isinstance(raw, tuple):
                raw = raw[0]
            comparison = compare_export(
                decode(raw.detach().numpy(), transform, 0.35, names),
                predictions["finetuned_single"],
            )
            comparison.update(dataset=name, id=sample["id"])
            result["export_comparisons"].append(comparison)
        result["datasets"][name] = {
            "manifest_sha256": sha256(manifest_path),
            "count": len(manifest["samples"]),
            "scores": {key: score_dataset(manifest, value) for key, value in rows.items()},
            "predictions": rows,
            "cpu_ms": {
                key: {"median": float(np.median(t)), "p95": float(np.percentile(t, 95))}
                for key, t in timings.items()
            },
        }
        print(
            json.dumps(
                {
                    "dataset": name,
                    "counts": {
                        key: value["categories"]
                        for key, value in result["datasets"][name]["scores"].items()
                    },
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    result["export_comparison_passed"] = all(
        c["passed"] for c in result["export_comparisons"]
    ) and any(not c.get("empty_pair", False) for c in result["export_comparisons"])
    result["export_compared_image_count"] = len(result["export_comparisons"])
    result["finished_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["export_comparison_passed"]:
        raise SystemExit("Candidate reference/ONNX comparison failed; see saved results")


if __name__ == "__main__":
    main()
