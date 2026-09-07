"""Offline comparison of explicit experimental ONNX profiles; never loads application config.

Profiles must supply a SHA-256, a processed [1,300,6] output and COCO class metadata.
The production manifest remains untouched. Videos are decoded sequentially, assuming CFR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.events import EventStateMachine  # noqa: E402
from visual_ai_agent.models import CATEGORIES, SceneObservation  # noqa: E402
from visual_ai_agent.vision import YoloOnnxDetector  # noqa: E402


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def annotate(frame, detections, title):
    scale = min(640 / frame.shape[1], 420 / frame.shape[0])
    view = cv2.resize(frame, None, fx=scale, fy=scale)
    for d in detections:
        x1, y1, x2, y2 = [round(v * scale) for v in d.bbox]
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            view,
            f"{d.category} {d.confidence:.2f}",
            (max(0, x1), max(15, y1)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
        )
    panel = np.full((460, 660, 3), 30, dtype=np.uint8)
    cv2.putText(panel, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    panel[35 : 35 + view.shape[0], 10 : 10 + view.shape[1]] = view
    return panel


def sample_row(detector, frame, threshold):
    start = time.perf_counter()
    candidates = detector.detect(frame)
    ms = (time.perf_counter() - start) * 1000
    detections = [d for d in candidates if d.confidence >= threshold]
    return detections, {
        "detections": [d.model_dump(mode="json") for d in detections],
        "candidates": [
            d.model_dump(mode="json")
            for c in CATEGORIES
            for d in [d for d in candidates if d.category == c][:10]
        ],
        "inference_ms": ms,
    }


def run_video(detector, spec, output, threshold):
    settings = spec["measurement"]
    path = Path(settings["video"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != settings["sha256"]:
        raise ValueError(f"Video checksum mismatch: {path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Unable to open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    machine = EventStateMachine(max_gap_seconds=settings["interval"] * 2.5)
    origin = datetime.now(UTC)
    next_index = 0
    rows, events, panels = [], [], []
    try:
        for t in np.arange(
            settings["window_start"], settings["window_end"] + 0.001, settings["interval"]
        ):
            target = round(t * fps)
            while next_index <= target:
                if not cap.grab():
                    raise RuntimeError(f"Decode failed: {path} at frame {next_index}")
                next_index += 1
            ok, frame = cap.retrieve()
            if not ok:
                raise RuntimeError(f"Retrieve failed: {path} at frame {target}")
            detections, row = sample_row(detector, frame, threshold)
            row.update(t=float(t), frame_index=target)
            rows.append(row)
            observation = SceneObservation(
                observed_at=origin + timedelta(seconds=float(t)),
                monotonic_at=float(t),
                status="running",
                fresh=True,
                source="replay",
                width=frame.shape[1],
                height=frame.shape[0],
                detections=detections,
            )
            events.extend(
                {"t": float(t), "category": e.category, "kind": e.kind}
                for e in machine.process(observation)
            )
            if float(t) in settings["review_times"]:
                panels.append(annotate(frame, detections, f"{spec['id']} t={t:g}"))
    finally:
        cap.release()
    if len(panels) % 2:
        panels.append(np.zeros_like(panels[0]))
    cv2.imwrite(
        str(output / f"{spec['id']}-review.jpg"),
        np.vstack([np.hstack(panels[i : i + 2]) for i in range(0, len(panels), 2)]),
    )
    result = {
        "id": spec["id"],
        "video": str(path),
        "samples": rows,
        "category_detected_frames": {
            c: sum(any(d["category"] == c for d in r["detections"]) for r in rows)
            for c in CATEGORIES
        },
        "events": events,
        "clock": "accelerated video-relative; not real-time latency",
    }
    save(output / f"{spec['id']}.json", result)
    return {k: v for k, v in result.items() if k != "samples"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profiles", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--only", default=None)
    p.add_argument("--videos", type=Path)
    p.add_argument("--images", type=Path)
    p.add_argument("--benchmark-image", type=Path)
    p.add_argument("--benchmark-samples", type=int, default=30)
    args = p.parse_args()
    if not any([args.videos, args.images, args.benchmark_image]):
        p.error("Select videos, images or a benchmark image")
    profiles = json.loads(args.profiles.read_text())["profiles"]
    profiles = [v for v in profiles if args.only is None or v["id"] == args.only]
    if not profiles:
        p.error("No matching profile")
    for profile in profiles:
        output = args.output / profile["id"]
        if output.exists():
            p.error(f"Output exists: {output}")
        output.mkdir(parents=True)
        detector = YoloOnnxDetector(
            profile["model"],
            confidence=0.01,
            input_size=640,
            intra_op_threads=2,
            verify_manifest=False,
            expected_sha256=profile["sha256"],
        )
        result = {
            "profile": profile,
            "confidence": 0.35,
            "proposal_floor": max(0.01, profile.get("confidence_floor", 0.01)),
            "cloud_requests": 0,
            "performance_note": "Not isolated unless explicitly run alone",
        }
        if args.videos:
            result["videos"] = []
            for spec in json.loads(args.videos.read_text())["videos"]:
                summary = run_video(detector, spec, output, 0.35)
                result["videos"].append(summary)
                print(profile["id"], summary["id"], summary["category_detected_frames"], flush=True)
        if args.images:
            result["images"] = []
            for spec in json.loads(args.images.read_text())["samples"]:
                path = Path(spec["image_path"])
                if not path.is_absolute():
                    path = args.images.parent / path
                if hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
                    raise ValueError(f"Image checksum mismatch: {path}")
                frame = cv2.imread(str(path))
                if frame is None:
                    raise ValueError(f"Cannot decode {path}")
                detections, row = sample_row(detector, frame, 0.35)
                row["id"] = spec["id"]
                result["images"].append(row)
                cv2.imwrite(
                    str(output / f"image-{spec['id']}.jpg"),
                    annotate(frame, detections, str(spec["id"])),
                )
        if args.benchmark_image:
            frame = cv2.imread(str(args.benchmark_image))
            if frame is None or args.benchmark_samples < 1:
                p.error("Invalid benchmark image or sample count")
            detector.confidence = 0.35
            for _ in range(5):
                detector.detect(frame)
            process = psutil.Process()
            cpu0 = sum(process.cpu_times()[:2])
            start = time.perf_counter()
            values, rss = [], []
            for _ in range(args.benchmark_samples):
                t = time.perf_counter()
                detector.detect(frame)
                values.append((time.perf_counter() - t) * 1000)
                rss.append(process.memory_info().rss / 1024**2)
            wall = time.perf_counter() - start
            result["benchmark"] = {
                "warmup": 5,
                "samples": len(values),
                "p50_ms": float(np.percentile(values, 50)),
                "p95_ms": float(np.percentile(values, 95)),
                "max_rss_mib": max(rss),
                "cpu_seconds": sum(process.cpu_times()[:2]) - cpu0,
                "wall_seconds": wall,
                "mode": "back-to-back inference, not 1Hz app steady state",
            }
        save(output / "summary.json", result)
        del detector
        print(profile["id"], "complete", flush=True)


if __name__ == "__main__":
    main()
