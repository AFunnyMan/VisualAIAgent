"""Bounded slicing and class-threshold experiments; no production configuration is loaded."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.detector_comparison import annotate, run_video, save  # noqa: E402
from scripts.score_detections import CATEGORIES, overlap, score_dataset  # noqa: E402
from visual_ai_agent.vision import YoloOnnxDetector, region_for_box  # noqa: E402


def class_nms(detections, threshold=0.5):
    kept = []
    for d in sorted(detections, key=lambda d: -d.confidence):
        if not any(d.category == k.category and overlap(d.bbox, k.bbox) >= threshold for k in kept):
            kept.append(d)
    return kept


def tile_boxes(width, height):
    tile_width, tile_height = math.ceil(width / 1.8), math.ceil(height / 1.8)
    return [
        (x, y, x + tile_width, y + tile_height)
        for y in sorted({0, height - tile_height})
        for x in sorted({0, width - tile_width})
    ]


class SlicedDetector:
    def __init__(self, detector):
        self.detector = detector
        self.calls = 0

    def detect(self, frame):
        height, width = frame.shape[:2]
        detections = self.detector.detect(frame)
        self.calls += 1
        for x1, y1, x2, y2 in tile_boxes(width, height):
            for d in self.detector.detect(frame[y1:y2, x1:x2]):
                box = (d.bbox[0] + x1, d.bbox[1] + y1, d.bbox[2] + x1, d.bbox[3] + y1)
                detections.append(
                    d.model_copy(update={"bbox": box, "region": region_for_box(box, width)})
                )
            self.calls += 1
        return class_nms(detections)


def filtered_rows(rows, thresholds):
    return [
        {
            "id": row["id"],
            "detections": [
                d for d in row["detections"] if d["confidence"] >= thresholds[d["category"]]
            ],
        }
        for row in rows
    ]


def choose_threshold(curve, budget):
    eligible = [r for r in curve if r["fp"] <= budget]
    if not eligible:
        raise ValueError("No threshold meets the development FP budget")
    return max(eligible, key=lambda r: (r["tp"], r["threshold"]))["threshold"]


def image_predictions(detector, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    rows = []
    for sample in manifest["samples"]:
        path = manifest_path.parent / sample["image_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample["sha256"]:
            raise ValueError(f"Image checksum mismatch: {path}")
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError(f"Cannot decode {path}")
        rows.append(
            {"id": sample["id"], "detections": [d.model_dump() for d in detector.detect(frame)]}
        )
    return rows


def load_detector(profiles, name, confidence):
    profile = next(p for p in profiles["profiles"] if p["id"] == name)
    return YoloOnnxDetector(
        profile["model"],
        confidence=confidence,
        verify_manifest=False,
        expected_sha256=profile["sha256"],
        intra_op_threads=2,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["calibrate", "images", "review", "videos"])
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--variant", choices=["s-sliced", "n-calibrated", "s-calibrated"])
    args = parser.parse_args()
    if args.manifest is None:
        parser.error("--manifest is required")
    if args.mode in {"review", "videos"} and args.variant is None:
        parser.error("--variant is required for review/videos")
    if args.mode == "images" or (args.mode in {"review", "videos"} and args.variant != "s-sliced"):
        if args.calibration is None:
            parser.error("--calibration is required for class-threshold experiments")
    if args.output.exists():
        parser.error("Choose a new output directory to preserve prior evidence")
    args.output.mkdir(parents=True)
    profiles = json.loads(args.profiles.read_text())
    if args.mode == "calibrate":
        manifest = json.loads(args.manifest.read_text())
        raw = {}
        for name in ("n-e2e", "s-traditional"):
            raw[name] = image_predictions(load_detector(profiles, name, 0.05), args.manifest)
            save(args.output / f"{name}-raw.json", {"images": raw[name]})
        baseline = score_dataset(manifest, raw["n-e2e"], 0.35)["categories"]
        output = {
            "selection": "per class: max TP with FP <= baseline budget; tie higher threshold",
            "development_manifest": str(args.manifest),
            "development_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            "fp_budgets": {c: baseline[c]["fp"] for c in CATEGORIES},
            "profiles": {},
        }
        for name, rows in raw.items():
            curves = {c: [] for c in CATEGORIES}
            for step in range(2, 19):
                threshold = step / 20
                scores = score_dataset(manifest, rows, threshold)["categories"]
                for c in CATEGORIES:
                    curves[c].append({"threshold": threshold, **scores[c]})
            thresholds = {c: choose_threshold(curves[c], baseline[c]["fp"]) for c in CATEGORIES}
            accepted = filtered_rows(rows, thresholds)
            output["profiles"][name] = {
                "thresholds": thresholds,
                "curves": curves,
                "categories": score_dataset(manifest, accepted, 0.05)["categories"],
            }
        save(args.output / "frozen.json", output)
        print(
            json.dumps(
                {
                    n: {k: v for k, v in r.items() if k != "curves"}
                    for n, r in output["profiles"].items()
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return
    if args.mode == "images":
        manifest = json.loads(args.manifest.read_text())
        frozen = json.loads(args.calibration.read_text())
        for name in ("n-e2e", "s-traditional"):
            detector = load_detector(profiles, name, 0.05)
            rows = image_predictions(detector, args.manifest)
            save(args.output / f"{name}-raw.json", {"images": rows})
            for tag, thresholds in [
                ("baseline", dict.fromkeys(CATEGORIES, 0.35)),
                ("calibrated", frozen["profiles"][name]["thresholds"]),
            ]:
                accepted = filtered_rows(rows, thresholds)
                scores = score_dataset(manifest, accepted, 0.05)
                save(
                    args.output / f"{name}-{tag}.json",
                    {"thresholds": thresholds, "images": accepted, "score": scores},
                )
                print(name, tag, scores["categories"], flush=True)
            if name == "s-traditional":
                detector.confidence = 0.35
                sliced = SlicedDetector(detector)
                rows = image_predictions(sliced, args.manifest)
                scores = score_dataset(manifest, rows)
                save(
                    args.output / "s-sliced.json",
                    {"images": rows, "score": scores, "model_calls": sliced.calls},
                )
                print("s-sliced", scores["categories"], flush=True)
        return
    if args.variant == "s-sliced":
        detector = SlicedDetector(load_detector(profiles, "s-traditional", 0.35))
    else:
        name = "n-e2e" if args.variant == "n-calibrated" else "s-traditional"
        thresholds = json.loads(args.calibration.read_text())["profiles"][name]["thresholds"]
        base_detector = load_detector(profiles, name, min(thresholds.values()))

        class CalibratedDetector:
            def detect(self, frame):
                return [
                    d for d in base_detector.detect(frame) if d.confidence >= thresholds[d.category]
                ]

        detector = CalibratedDetector()
    output = {"variant": args.variant, "videos": []}
    for video in json.loads(args.manifest.read_text())["videos"]:
        if args.mode == "videos":
            output["videos"].append(run_video(detector, video, args.output, 0.05))
        else:
            settings = video["measurement"]
            if (
                hashlib.sha256(Path(settings["video"]).read_bytes()).hexdigest()
                != settings["sha256"]
            ):
                raise ValueError("Video checksum mismatch")
            cap = cv2.VideoCapture(settings["video"])
            if not cap.isOpened():
                cap.release()
                raise ValueError("Unable to open video")
            try:
                fps, index = cap.get(cv2.CAP_PROP_FPS), 0
                panels, rows = [], []
                for t in settings["review_times"]:
                    target = round(t * fps)
                    while index <= target:
                        if not cap.grab():
                            raise RuntimeError("Sequential decode failed")
                        index += 1
                    ok, frame = cap.retrieve()
                    if not ok:
                        raise RuntimeError("Sequential retrieve failed")
                    detections = detector.detect(frame)
                    rows.append({"t": t, "detections": [d.model_dump() for d in detections]})
                    panels.append(annotate(frame, detections, f"{video['id']} t={t}"))
            finally:
                cap.release()
            import numpy as np

            if len(panels) % 2:
                panels.append(np.zeros_like(panels[0]))
            cv2.imwrite(
                str(args.output / f"{video['id']}.jpg"),
                np.vstack([np.hstack(panels[i : i + 2]) for i in range(0, len(panels), 2)]),
            )
            output["videos"].append({"id": video["id"], "samples": rows})
        print(args.variant, video["id"], "complete", flush=True)
    save(args.output / "summary.json", output)


if __name__ == "__main__":
    main()
