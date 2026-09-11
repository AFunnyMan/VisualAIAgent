"""Run the real combined runtime in an isolated directory; no automatic rules or cloud calls."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import psutil

from visual_ai_agent.config import Config
from visual_ai_agent.models import utcnow
from visual_ai_agent.runtime import ApplicationRuntime


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output directory must be new")
    config = replace(
        Config.from_env(),
        data_dir=output,
        api_key="",
        daily_auto_limit=0,
        behavior_enabled=True,
        behavior_posture_manifest=args.posture_manifest.resolve(),
        behavior_drinking_manifest=args.drinking_manifest.resolve(),
        camera_index=args.camera,
        camera_width=1920,
        camera_height=1080,
        observation_region=None,
    )
    runtime = ApplicationRuntime(config)
    lock = threading.Lock()
    counts = {"objects": Counter(), "behavior": Counter()}
    inference = []
    callbacks = []
    behavior_ages = []
    originals = {"objects": runtime.ingest, "behavior": runtime.ingest_behavior}

    def wrapped(name):
        def observe(observation, jpeg=None):
            began = time.perf_counter()
            originals[name](observation, jpeg)
            with lock:
                counts[name]["fresh" if observation.fresh else observation.status] += 1
                if name == "objects" and observation.inference_ms is not None:
                    inference.append(observation.inference_ms)
                if name == "behavior" and observation.fresh:
                    behavior_ages.append(
                        (utcnow() - observation.observed_at).total_seconds() * 1000
                    )
                callbacks.append((time.perf_counter() - began) * 1000)

        return observe

    runtime.ingest = wrapped("objects")
    runtime.ingest_behavior = wrapped("behavior")
    began_at = utcnow()
    process = psutil.Process()
    rss = []
    try:
        result = runtime.start_camera()
        if not result.ok:
            raise RuntimeError(result.error)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            # Exercise the same read paths as UI while both real inference branches run.
            runtime.snapshot()
            runtime.memory.get_current_scene()
            runtime.behavior.current()
            runtime.behavior.statistics()
            runtime.rules.list_rules()
            rss.append(process.memory_info().rss / 1024**2)
            time.sleep(0.5)
        before_stop = runtime.diagnostics()
        stopped = runtime.stop_camera()
        with lock:
            summary = {
                "started_at": began_at.isoformat(),
                "finished_at": utcnow().isoformat(),
                "source": "real USB camera; no action labels",
                "requested_seconds": args.seconds,
                "counts": {key: dict(value) for key, value in counts.items()},
                "object_inference_ms": metrics(inference),
                "behavior_age_at_callback_end_ms": metrics(behavior_ages),
                "callback_ms": metrics(callbacks),
                "rss_peak_mib": max(rss, default=0),
                "runtime_before_stop": before_stop,
                "stop": stopped.model_dump(mode="json"),
                "statistics": runtime.behavior.statistics().data,
                "agent_runs": runtime.memory.list_agent_runs(),
                "rules": runtime.rules.list_rules().data["rules"],
                "scope": "Integration/load check only, not action accuracy or long-term acceptance",
            }
    finally:
        runtime.close()
    summary["closed"] = runtime.closed
    summary["final_error"] = runtime.last_error
    (output / "integration-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return int(not stopped.ok or not counts["objects"]["fresh"] or not counts["behavior"]["fresh"])


def metrics(values):
    values = sorted(values)
    return {
        "n": len(values),
        "median": statistics.median(values) if values else None,
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))] if values else None,
        "max": max(values) if values else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posture-manifest", type=Path, required=True)
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--seconds", type=int, choices=[30, 60, 300], default=30)
    raise SystemExit(run(parser.parse_args()))
