"""Sample local videos with the production detector, without .env, Agent or camera access.

Outputs describe detection presence, not accuracy. Human labels must be recorded separately.
The event state machine uses video-relative timestamps in an accelerated offline replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.events import EventStateMachine  # noqa: E402
from visual_ai_agent.models import CATEGORIES, SceneObservation  # noqa: E402
from visual_ai_agent.vision import YoloOnnxDetector  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--interval", type=float, choices=[1.0, 2.0], default=1)
    args = parser.parse_args()
    if (
        not args.video.is_file()
        or not math.isfinite(args.start)
        or not math.isfinite(args.duration)
        or args.start < 0
        or args.duration <= 0
    ):
        parser.error("Existing video, nonnegative start and positive duration required")
    if (args.output / "samples.json").exists():
        parser.error("Output already contains samples; choose a new directory")
    args.output.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error("Unable to decode video")
    fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    if not math.isfinite(fps) or not math.isfinite(frame_count) or fps <= 0 or frame_count <= 0:
        capture.release()
        parser.error("Video must expose valid FPS and frame count")
    total = frame_count / fps
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    detector = YoloOnnxDetector("models/yolo26n-e2e.onnx", confidence=0.35)
    machine = EventStateMachine(max_gap_seconds=args.interval * 2.5)
    timeline_origin = datetime.now(UTC)
    times = np.arange(args.start, min(total, args.start + args.duration), args.interval)
    review_indices = set(np.linspace(0, len(times) - 1, min(6, len(times)), dtype=int))
    samples, events, panels = [], [], []
    next_frame_index = 0
    try:
        for index, timestamp in enumerate(times):
            # Decode from the beginning, including skipped frames. Some Theora
            # backends report the requested seek time but return different pixels
            # depending on seek history; checking POS_MSEC alone cannot catch it.
            target_frame_index = min(round(timestamp * fps), int(frame_count) - 1)
            while next_frame_index <= target_frame_index:
                if not capture.grab():
                    raise RuntimeError(f"Decode failed at frame {next_frame_index}")
                next_frame_index += 1
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                raise RuntimeError(f"Decode failed at video time {timestamp}")
            start = time.perf_counter()
            detections = detector.detect(frame)
            inference_ms = (time.perf_counter() - start) * 1000
            sample = {
                "t": float(timestamp),
                "frame_index": target_frame_index,
                "decoder_position_ms": capture.get(cv2.CAP_PROP_POS_MSEC),
                "inference_ms": inference_ms,
                "detections": [d.model_dump(mode="json") for d in detections],
            }
            samples.append(sample)
            observation = SceneObservation(
                observed_at=timeline_origin + timedelta(seconds=float(timestamp)),
                monotonic_at=float(timestamp),
                status="running",
                fresh=True,
                detections=detections,
                source="replay",
                width=width,
                height=height,
            )
            for event in machine.process(observation):
                events.append(
                    {"t": float(timestamp), "category": event.category, "kind": event.kind}
                )
            if index in review_indices:
                scale = min(640 / frame.shape[1], 440 / frame.shape[0])
                view = cv2.resize(frame, None, fx=scale, fy=scale)
                for detection in detections:
                    x1, y1, x2, y2 = (int(v * scale) for v in detection.bbox)
                    cv2.rectangle(view, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(
                        view,
                        f"{detection.category} {detection.confidence:.2f}",
                        (max(0, x1), max(15, y1)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (0, 255, 0),
                        1,
                    )
                panel = np.full((480, 660, 3), 30, dtype=np.uint8)
                cv2.putText(
                    panel,
                    f"t={timestamp:.1f}s",
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    1,
                )
                panel[35 : 35 + view.shape[0], 10 : 10 + view.shape[1]] = view
                panels.append(panel)
                cv2.imwrite(str(args.output / f"review-{timestamp:g}.jpg"), frame)
    finally:
        capture.release()
    if not samples:
        raise RuntimeError("Requested window has no samples")
    while len(panels) % 2:
        panels.append(np.zeros_like(panels[0]))
    sheet = np.vstack([np.hstack(panels[i : i + 2]) for i in range(0, len(panels), 2)])
    cv2.imwrite(str(args.output / "review-sheet.jpg"), sheet)
    hits = Counter(
        category for s in samples for category in {d["category"] for d in s["detections"]}
    )
    summary = {
        "video": str(args.video),
        "sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
        "fps": fps,
        "duration_seconds": total,
        "width": width,
        "height": height,
        "window_start": args.start,
        "window_end": float(times[-1]),
        "interval": args.interval,
        "sampling": "sequential decode, nearest CFR frame index; VFR not validated",
        "samples": len(samples),
        "confidence": 0.35,
        "input_size": 640,
        "category_detected_frames": {c: hits[c] for c in CATEGORIES},
        "review_times": [float(times[i]) for i in sorted(review_indices)],
        "events": events,
        "event_clock": "accelerated video-relative seconds; no wall-clock claim",
        "model_requests": 0,
        "accuracy": "not calculated without independent human labels",
    }
    (args.output / "samples.json").write_text(json.dumps(samples, indent=2))
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
