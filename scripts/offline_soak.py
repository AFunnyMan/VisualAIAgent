"""Run real local ONNX inference over a still image while proving unrelated watches stay idle."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.runtime import ApplicationRuntime  # noqa: E402
from visual_ai_agent.vision import VisionWorker, YoloOnnxDetector  # noqa: E402


class StillImageSource:
    """Explicit replay source, never a physical camera."""

    source_name = "replay"
    status = "stopped"
    last_error = None

    def __init__(self, image):
        self.image = image

    def open(self):
        self.status = "running"

    def read(self):
        time.sleep(0.05)
        return self.image.copy() if self.status == "running" else None

    def close(self):
        self.status = "stopped"


class ForbiddenAgent:
    calls = 0

    async def run_event(self, *args):
        self.calls += 1
        raise AssertionError("Unrelated event unexpectedly woke Agent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument(
        "--watch-category", choices=["cell phone", "cup", "bottle"], default="cell phone"
    )
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("duration must be positive")
    frame = cv2.imread(str(args.image))
    if frame is None:
        parser.error("image cannot be read")
    output = Path("harness/artifacts") / ("soak-" + uuid4().hex[:8])
    config = replace(
        Config.from_env(), data_dir=output, api_key="", api_base_url="", agent_model=""
    )
    detector = YoloOnnxDetector(config.model_path, expected_sha256=config.model_sha256 or None)
    if any(d.category == args.watch_category for d in detector.detect(frame)):
        parser.error("watch category must be absent from the replay image")
    runtime = ApplicationRuntime(config)
    sentinel = ForbiddenAgent()
    runtime.agent = sentinel
    runtime.watches.create_watch(
        args.watch_category, "appeared", duration_minutes=1440, request_id="unrelated-soak-watch"
    )
    worker = VisionWorker(detector, StillImageSource(frame), runtime.ingest)
    runtime._vision = worker
    process = psutil.Process()
    process.cpu_percent()
    samples = []
    started_at = datetime.now(UTC)
    start = time.monotonic()
    worker.start()
    interrupted = False
    try:
        while time.monotonic() - start < args.duration:
            time.sleep(min(5, args.duration))
            current = runtime.memory.get_current_scene().data
            samples.append(
                {
                    "elapsed": time.monotonic() - start,
                    "current": current.get("current"),
                    "rss_mb": process.memory_info().rss / 1024**2,
                    "cpu_percent_one_core_100": process.cpu_percent(),
                }
            )
    except KeyboardInterrupt:
        interrupted = True
    finally:
        runtime.close()
    elapsed = time.monotonic() - start
    summary = {
        "source": "still-image replay, real ONNX CPU; no real camera or API",
        "started_at": started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "requested_seconds": args.duration,
        "agent_calls": sentinel.calls,
        "model_requests": runtime.memory.get_usage_summary(started_at.date())[
            "total_request_attempts"
        ],
        "checks": len(samples),
        "invalid_checks": sum(not x["current"] for x in samples),
        "peak_rss_mb": max((x["rss_mb"] for x in samples), default=0),
        "mean_cpu_percent_one_core_100": sum(x["cpu_percent_one_core_100"] for x in samples)
        / max(1, len(samples)),
        "runtime_error": runtime.last_error,
        "interrupted": interrupted,
    }
    (output / "soak-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Local evidence: {output}")
    if (
        interrupted
        or elapsed < args.duration
        or sentinel.calls
        or not samples
        or summary["invalid_checks"]
        or runtime.last_error
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
