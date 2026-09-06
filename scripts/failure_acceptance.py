"""Exercise local event fallbacks through ApplicationRuntime and the real Agent SDK.

No ``.env`` is loaded, no physical camera is opened, and no request can leave loopback.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.runtime import ApplicationRuntime  # noqa: E402
from visual_ai_agent.vision import ReplaySource, VisionWorker, YoloOnnxDetector  # noqa: E402


def _wait(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting for local failure-path result")


def _run_case(video: Path, root: Path, *, daily_limit: int, port: int) -> dict:
    name = "connection_refused" if daily_limit else "daily_limit_zero"
    config = replace(
        Config(),
        data_dir=root / name,
        api_key="test-only",
        api_base_url=f"http://127.0.0.1:{port}/v1",
        agent_model="test-only",
        api_mode="chat_completions",
        api_timeout_seconds=3.0,
        daily_auto_limit=daily_limit,
        sample_interval=1.0,
    )
    runtime = ApplicationRuntime(config)
    fresh_count = 0

    def ingest(observation, jpeg):
        nonlocal fresh_count
        if observation.fresh:
            fresh_count += 1
        runtime.ingest(observation, jpeg)

    watch = runtime.watches.create_watch(
        "bottle", "appeared", duration_minutes=30, request_id=f"failure-{name}"
    ).data["watch"]
    detector = YoloOnnxDetector(config.model_path, confidence=config.confidence)
    worker = VisionWorker(
        detector,
        ReplaySource(video, loop=True, realtime=True),
        ingest,
        inference_interval=1.0,
        stale_after=2.5,
    )
    runtime._vision = worker
    try:
        worker.start()
        notification = _wait(
            lambda: next(iter(runtime.watches.list_notifications().data["notifications"]), None)
        )
        _wait(lambda: runtime._jobs.unfinished_tasks == 0)
        fresh_at_notice = fresh_count
        _wait(lambda: fresh_count > fresh_at_notice)
        runs = [
            row
            for row in runtime.memory.list_agent_runs()
            if row["request_id"].startswith(f"event:{watch['watch_id']}:")
        ]
        expected_attempts = 1 if daily_limit else 0
        if daily_limit and len(runs) != 1:
            raise AssertionError("expected exactly one persisted event Agent run")
        if not daily_limit and runs:
            raise AssertionError("quota short-circuit unexpectedly reserved an Agent run")
        usage_attempts = sum(row["request_attempts"] for row in runtime.memory.list_agent_runs())
        if notification["source"] != "fallback":
            raise AssertionError("event failure did not produce a fallback notification")
        if usage_attempts != expected_attempts:
            raise AssertionError(
                f"expected {expected_attempts} request attempts, got {usage_attempts}"
            )
        if not runtime.memory.get_current_scene().data["current"]:
            raise AssertionError("visual replay stopped during Agent failure handling")
        return {
            "case": name,
            "injected_condition": (
                "actual local TCP connection refusal" if daily_limit else "local daily limit zero"
            ),
            "notification_source": notification["source"],
            "run_status": runs[0]["status"] if runs else "not_reserved_limit_reached",
            "request_attempts": usage_attempts,
            "visual_fresh_before_and_after_fallback": True,
            "latest_scene_current": True,
        }
    finally:
        runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")

    output = Path("harness/artifacts") / ("failure-acceptance-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    # Bound but deliberately never listening: local connect receives ECONNREFUSED and
    # another process cannot claim the selected port during the acceptance case.
    started_at = datetime.now(UTC)
    try:
        cases = [
            _run_case(args.video, output, daily_limit=20, port=port),
            _run_case(args.video, output, daily_limit=0, port=port),
        ]
    finally:
        reservation.close()
    summary = {
        "started_at": started_at.isoformat(),
        "source": "real licensed video + real ONNX + ApplicationRuntime + real Agent SDK",
        "physical_camera": False,
        "cloud_requests": 0,
        "qualification": (
            "local connection-refusal and quota fault injection; not a Bailian outage "
            "or operating-system network-disconnect test"
        ),
        "cases": cases,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Local evidence: {output}")


if __name__ == "__main__":
    main()
