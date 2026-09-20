"""Local camera soak of the real object/behavior runtime, with bounded timing traces.

Uses an isolated data directory, optional SQLite backup of existing history,
real model files, and UI-equivalent reads. No Agent API calls or video recording.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.runtime import ApplicationRuntime  # noqa: E402


def metrics(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {}
    return {
        name: values[min(len(values) - 1, int(len(values) * fraction))]
        for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1))
    }


def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    data = output / "runtime"
    data.mkdir()
    if args.history:
        with sqlite3.connect(f"file:{Path(args.history).resolve()}?mode=ro", uri=True) as source:
            with sqlite3.connect(data / "memory.sqlite3") as target:
                source.backup(target)
    config = replace(
        Config.from_env(),
        data_dir=data,
        api_key="",
        behavior_enabled=True,
        cup_scale_recheck=True,
        laptop_enabled=False,
    )
    runtime = ApplicationRuntime(config)
    rows = []
    ui_errors = []
    ui_times = []
    stop = threading.Event()

    def ui_reads():
        tick = 0
        while not stop.is_set():
            started = time.monotonic()
            try:
                runtime.memory.get_current_scene()
                runtime.snapshot()
                runtime.behavior.current()
                if tick % 10 == 0:
                    runtime.behavior.statistics()
            except Exception as exc:
                ui_errors.append(type(exc).__name__)
            ui_times.append((time.monotonic() - started) * 1000)
            tick += 1
            stop.wait(max(0, 0.5 - (time.monotonic() - started)))

    reader = None
    start = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    try:
        result = runtime.start_camera(object_model_id=args.model)
        if not result.ok:
            raise RuntimeError(result.error)
        worker = runtime._behavior_worker
        if worker is None:
            raise RuntimeError(runtime.behavior_error)
        identity = runtime.diagnostics()["active_object_model"]
        behavior_model = runtime.diagnostics()["behavior_model_version"]
        reader = threading.Thread(target=ui_reads, daemon=True)
        reader.start()
        sequence = 0
        with (output / "timings.jsonl").open("w") as stream:
            while time.monotonic() - start < args.duration:
                batch = worker.diagnostic_samples(sequence)
                if batch:
                    sequence = batch[-1]["sequence"]
                    rows.extend(batch)
                    for row in batch:
                        stream.write(json.dumps(row) + "\n")
                    stream.flush()
                progress = {
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "samples": len(rows),
                    "statuses": dict(Counter(row["status"] for row in rows)),
                    "postures": dict(Counter(row["posture"] for row in rows)),
                    "inference_ms": metrics(row.get("inference_ms") for row in rows),
                    "callback_ms": metrics(row.get("callback_ms") for row in rows),
                    "saved_age_ms": metrics(row.get("saved_age_ms") for row in rows),
                }
                (output / "progress.json").write_text(json.dumps(progress, indent=2))
                stop.wait(1)
            batch = worker.diagnostic_samples(sequence)
            rows.extend(batch)
            for row in batch:
                stream.write(json.dumps(row) + "\n")
        elapsed = time.monotonic() - start
        stop.set()
        reader.join(10)
        result = runtime.stop_camera()
        steady = [row for row in rows if row.get("cycle_started", 0) - start >= 10]
        summary = {
            "statuses": dict(Counter(row["status"] for row in rows)),
            "postures": dict(Counter(row["posture"] for row in rows)),
            "inference_ms": metrics(row.get("inference_ms") for row in rows),
            "callback_ms": metrics(row.get("callback_ms") for row in rows),
            "saved_age_ms": metrics(row.get("saved_age_ms") for row in rows),
            "started_at": started_at,
            "elapsed_seconds": elapsed,
            "samples": len(rows),
            "object_model": identity,
            "behavior_model": behavior_model,
            "ui_read_ms": metrics(ui_times),
            "ui_errors": ui_errors,
            "stop_ok": result.ok,
            "steady_samples": len(steady),
            "steady_fresh_fraction": sum(row["fresh"] for row in steady) / max(1, len(steady)),
            "steady_gap_over250ms": sum((row.get("sample_gap_ms") or 0) > 250 for row in steady),
            "steady_saved_age_ms": metrics(row["saved_age_ms"] for row in steady),
            "raw_postures": dict(
                Counter((row.get("raw_posture") or {}).get("label") for row in rows)
            ),
            "scope": (
                "Real USB + production runtime + periodic UI-equivalent reads; "
                "no browser render or action ground truth"
            ),
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        stop.set()
        if reader:
            reader.join(10)
        runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--history")
    parser.add_argument("--model", default="r05", choices=("original", "r03", "r04", "r05"))
    parser.add_argument("--duration", type=float, default=600)
    arguments = parser.parse_args()
    if arguments.duration < 10:
        parser.error("duration must be at least 10 seconds")
    run(arguments)
