"""Explicit local image benchmark or camera acceptance recorder; never uses a cloud model."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.memory import MemoryStore  # noqa: E402
from visual_ai_agent.vision import (  # noqa: E402
    CameraSource,
    CupScaleRecheckDetector,
    VisionWorker,
    YoloOnnxDetector,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--camera", type=int)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("data/acceptance"))
    args = parser.parse_args()
    if args.samples < 1 or args.duration <= 0:
        parser.error("samples and duration must be positive")
    config = Config.from_env()
    destination = args.output / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:6])
    destination.mkdir(parents=True)
    detector = YoloOnnxDetector(
        config.model_path, confidence=config.confidence, expected_sha256=config.model_sha256 or None
    )
    if config.cup_scale_recheck:
        detector = CupScaleRecheckDetector(detector)
    process = psutil.Process()
    process.cpu_percent()
    rows = []
    started = time.monotonic()
    worker = None
    interrupted = False
    try:
        if args.image:
            frame = cv2.imread(str(args.image))
            if frame is None:
                parser.error("Image could not be read")
            detector.detect(frame)  # Explicit warm-up, outside recorded samples.
            for index in range(args.samples):
                before = time.perf_counter()
                detections = detector.detect(frame)
                rows.append(
                    {
                        "sample": index,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "status": "image",
                        "fresh": True,
                        "inference_ms": (time.perf_counter() - before) * 1000,
                        "cpu_percent_one_core_100": process.cpu_percent(),
                        "rss_mb": process.memory_info().rss / 1024**2,
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                        "detections": len(detections),
                    }
                )
        else:
            store = MemoryStore(
                destination / "memory", max_gap_seconds=config.sample_interval * 2.5
            )
            worker = VisionWorker(
                detector,
                CameraSource(
                    args.camera,
                    width=config.camera_width,
                    height=config.camera_height,
                    observation_region=config.observation_region,
                ),
                store.ingest,
                inference_interval=config.sample_interval,
                stale_after=config.sample_interval * 2.5,
            )
            worker.start()
            while time.monotonic() - started < args.duration:
                time.sleep(1)
                observation, _ = worker.snapshot()
                if observation:
                    rows.append(
                        {
                            "sample": len(rows),
                            "timestamp": observation.observed_at.isoformat(),
                            "status": observation.status,
                            "fresh": observation.fresh,
                            "inference_ms": observation.inference_ms,
                            "cpu_percent_one_core_100": process.cpu_percent(),
                            "rss_mb": process.memory_info().rss / 1024**2,
                            "width": observation.width,
                            "height": observation.height,
                            "detections": len(observation.detections),
                        }
                    )
                if observation and observation.status in ("error", "disconnected"):
                    break
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if worker:
            worker.stop()
    elapsed = time.monotonic() - started
    if rows:
        with (destination / "samples.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    timings = [row["inference_ms"] for row in rows if row["inference_ms"] is not None]
    summary = {
        "source": "public_or_user_image" if args.image else "camera",
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "elapsed_seconds": elapsed,
        "requested_duration_seconds": args.duration if args.camera is not None else None,
        "samples": len(rows),
        "interrupted": interrupted,
        "p50_inference_ms": statistics.median(timings) if timings else None,
        "p95_inference_ms": sorted(timings)[min(len(timings) - 1, int(0.95 * len(timings)))]
        if timings
        else None,
        "peak_rss_mb": max((row["rss_mb"] for row in rows), default=None),
        "mean_cpu_percent_one_core_100": statistics.mean(
            row["cpu_percent_one_core_100"] for row in rows
        )
        if rows
        else None,
        "invalid_samples": sum(not row["fresh"] for row in rows),
        "model_requests": 0,
        "recognition_quality_accepted": False,
        "requested_camera_size": [config.camera_width, config.camera_height]
        if args.camera is not None
        else None,
        "cup_scale_recheck": config.cup_scale_recheck,
        "observation_region": config.observation_region if args.camera is not None else None,
        "note": (
            "Performance recording only; camera scene accuracy and alert latency require scoring."
        ),
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Local evidence directory: {destination}")
    if not rows or interrupted or (args.camera is not None and elapsed < args.duration):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
