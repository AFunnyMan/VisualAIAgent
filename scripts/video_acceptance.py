"""Validate real ONNX inference and visual-memory events over a licensed video replay.

This script never loads ``.env``, starts an Agent, or opens a physical camera.
It is intended for downloaded acceptance material under ``harness/artifacts``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_ai_agent.memory import MemoryStore  # noqa: E402
from visual_ai_agent.models import CATEGORIES, SceneObservation  # noqa: E402
from visual_ai_agent.vision import ReplaySource, VisionWorker, YoloOnnxDetector  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Pinned hash from the source/acceptance record",
    )
    parser.add_argument("--category", choices=CATEGORIES, required=True)
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n-e2e.onnx"))
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--post-eof-seconds",
        type=float,
        default=6.0,
        help="Observe invalid EOF state beyond the five-second missing threshold",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    if args.interval <= 0 or args.post_eof_seconds < 5 or args.timeout <= 0:
        parser.error("interval/timeout must be positive and post-eof-seconds must be at least 5")
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")
    actual_hash = _sha256(args.video)
    if actual_hash.lower() != args.expected_sha256.lower():
        parser.error(
            f"video SHA-256 mismatch: expected {args.expected_sha256.lower()}, got {actual_hash}"
        )

    output = Path("harness/artifacts") / ("video-acceptance-" + uuid4().hex[:8])
    store = MemoryStore(output, max_gap_seconds=args.interval * 2.5)
    detector = YoloOnnxDetector(args.model, confidence=args.confidence)
    source = ReplaySource(args.video, loop=False, realtime=True)
    observations: list[tuple[float, SceneObservation]] = []
    events = []
    callback_lock = threading.Lock()

    def ingest(observation: SceneObservation, jpeg: bytes | None) -> None:
        persisted = store.ingest(observation, jpeg)
        with callback_lock:
            observations.append((time.monotonic(), observation))
            events.extend((time.monotonic(), event) for event in persisted)

    worker = VisionWorker(
        detector,
        source,
        ingest,
        inference_interval=args.interval,
        stale_after=args.interval * 2.5,
    )
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    eof_at: float | None = None
    worker.start()
    try:
        deadline = started_monotonic + args.timeout
        while time.monotonic() < deadline:
            time.sleep(0.05)
            with callback_lock:
                saw_fresh = any(item.fresh for _, item in observations)
            if saw_fresh and source.status == "stopped":
                eof_at = time.monotonic()
                break
        if eof_at is None:
            raise RuntimeError("video did not reach EOF before timeout")
        while time.monotonic() - eof_at < args.post_eof_seconds:
            time.sleep(0.05)
    finally:
        worker.stop()

    elapsed = time.monotonic() - started_monotonic
    with callback_lock:
        captured_observations = list(observations)
        captured_events = list(events)
    fresh = [item for _, item in captured_observations if item.fresh]
    invalid_after_eof = [
        item
        for received_at, item in captured_observations
        if eof_at is not None
        and received_at >= eof_at
        and (not item.fresh or item.status != "running")
    ]
    category_hits = [
        detection
        for item in fresh
        for detection in item.detections
        if detection.category == args.category
    ]
    event_counts = Counter(f"{event.category}:{event.kind}" for _, event in captured_events)
    missing_during_video = [
        event
        for received_at, event in captured_events
        if event.kind == "missing" and (eof_at is None or received_at < eof_at)
    ]
    missing_after_eof = [
        event
        for received_at, event in captured_events
        if event.kind == "missing" and eof_at is not None and received_at >= eof_at
    ]
    usage = store.get_usage_summary(started_at.date())
    summary = {
        "source": "licensed real-video replay; real YOLO26 ONNX CPU; no camera or Agent",
        "video": str(args.video),
        "video_sha256": actual_hash,
        "model": str(args.model),
        "category": args.category,
        "started_at": started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "inference_interval_seconds": args.interval,
        "post_eof_seconds": args.post_eof_seconds,
        "fresh_observations": len(fresh),
        "invalid_observations_after_eof": len(invalid_after_eof),
        "category_observations": sum(
            any(detection.category == args.category for detection in item.detections)
            for item in fresh
        ),
        "category_candidates": len(category_hits),
        "category_max_confidence": max(
            (detection.confidence for detection in category_hits), default=None
        ),
        "event_counts": dict(sorted(event_counts.items())),
        "missing_events_during_video": len(missing_during_video),
        "missing_events_after_eof": len(missing_after_eof),
        "latest_scene_current": store.get_current_scene().data.get("current"),
        "agent_request_attempts": usage["total_request_attempts"],
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Local evidence: {output}")

    appeared_key = f"{args.category}:appeared"
    failures = []
    if not fresh:
        failures.append("no fresh observations")
    if not category_hits:
        failures.append(f"no {args.category} detections")
    if event_counts[appeared_key] != 1:
        failures.append(f"expected exactly one {appeared_key} event")
    if missing_during_video:
        failures.append("complete source clip unexpectedly produced a missing event")
    if missing_after_eof:
        failures.append("EOF produced a missing event")
    if not invalid_after_eof:
        failures.append("EOF did not produce an invalid observation")
    if summary["latest_scene_current"]:
        failures.append("scene remained current after EOF")
    if summary["agent_request_attempts"]:
        failures.append("unexpected Agent request accounting")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
