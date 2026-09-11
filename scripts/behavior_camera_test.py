"""Run the experimental r03 behavior pipeline against one real camera.

This is a manual, local-only test. It stores JSONL, a summary, and a bounded
number of event stills in a new output directory; it never records video or
calls the product Agent.
"""

# The standalone preview is intentionally embedded here.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort  # noqa: F401 -- compatibility patch point
import psutil

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.behavior_diagnostics import TransitionEvidence  # noqa: E402
from scripts.behavior_timeline_v2 import BehaviorTimelineV2  # noqa: E402
from scripts.behavior_visibility import PersonTrackGate  # noqa: E402
from scripts.camera_acceptance import _append_jsonl, _atomic_json  # noqa: E402
from scripts.evaluate_behavior_models import (  # noqa: E402
    load_manifest,
    sha256,
)
from visual_ai_agent.vision import CameraSource, VisionWorker  # noqa: E402

FPS = 10.0
INTERVAL = 1.0 / FPS
STALE_AFTER = 0.25
POSTURE_CONFIRM = 0.3
DRINK_CONFIRM = 0.5
DRINK_END = 0.3
UNKNOWN_BRIDGE = 1.0
MAX_EVENT_IMAGES = 12


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _metrics(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 3) if ordered else None,
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)
        if ordered
        else None,
        "max": round(max(ordered), 3) if ordered else None,
    }


from visual_ai_agent.behavior import (  # noqa: E402
    BehaviorDetector as ProductionBehaviorDetector,
)
from visual_ai_agent.behavior import OnnxClassifier as ProductionOnnxClassifier  # noqa: E402
from visual_ai_agent.behavior_preprocess import preprocess_bgr  # noqa: E402,F401

OnnxClassifier = ProductionOnnxClassifier  # noqa: F811
BehaviorDetector = ProductionBehaviorDetector  # noqa: F811


@dataclass
class TestState:
    output: Path
    duration: float

    def __post_init__(self) -> None:
        self.started_at = _utcnow()
        self.started_mono = time.monotonic()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.first_fresh = threading.Event()
        self.stop_requested = False
        self.latest: dict[str, Any] | None = None
        self.latest_jpeg: bytes | None = None
        self.samples: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.event_images: list[dict[str, Any]] = []
        self.fresh_count = 0
        self.fault_count = 0
        self.startup_count = 0
        self.first_fresh_mono = None
        self.model_info: dict[str, Any] = {}

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "experimental": True,
                "started_at": self.started_at,
                "elapsed_seconds": round(
                    time.monotonic() - (self.first_fresh_mono or time.monotonic()), 3
                ),
                "target_seconds": self.duration,
                "latest": self.latest,
                "fresh_samples": self.fresh_count,
                "fault_samples": self.fault_count,
                "startup_samples": self.startup_count,
                "events": list(self.events[-20:]),
                "event_image_count": len(self.event_images),
                "models": dict(self.model_info),
                "notice": "r03本地实验：person gate仅为弱辅助，不能证明无遮挡或完整动作可见。",
            }

    def write_progress(self) -> None:
        _atomic_json(self.output / "progress.json", self.public())


def _page() -> str:
    return """<!doctype html><meta charset=utf-8><title>行为摄像头测试</title><style>body{font:16px system-ui;max-width:1100px;margin:16px auto;padding:0 16px}img{max-width:100%;background:#222}#s{background:#f4f4f4;padding:12px;line-height:1.7}.warn{color:#9b3d00;font-weight:bold}pre{white-space:pre-wrap}</style><h1>行为摄像头测试</h1><p class=warn>人员跟踪只是弱辅助，不能证明无遮挡或完整动作可见。</p><div id=s>等待新鲜画面…</div><img id=p><p><button onclick=stopRun()>停止测试</button></p><pre id=e></pre><script>async function poll(){try{let x=await(await fetch('/progress.json',{cache:'no-store'})).json(),l=x.latest||{},left=Math.max(0,x.target_seconds-x.elapsed_seconds);document.querySelector('#s').textContent=`剩余 ${Math.ceil(left)} 秒｜新鲜 ${x.fresh_samples}｜故障 ${x.fault_samples}\n姿态 ${({seated:'在座',standing:'站立',empty:'空座',unknown:'不确定'})[l.confirmed_posture]||'不确定'}｜饮水 ${({drinking:'疑似饮水',not_drinking:'未饮水',unknown:'不确定'})[l.confirmed_drinking]||'不确定'}｜${l.status||'等待'}`;document.querySelector('#e').textContent=JSON.stringify(x.events,null,2);document.querySelector('#p').src='/latest.jpg?t='+Date.now()}catch(e){document.querySelector('#s').textContent=e.message}}async function stopRun(){await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});poll()}setInterval(poll,500);poll()</script>"""


def _handler_for(state: TestState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send_body(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self.send_body(200, _page().encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/progress.json"):
                body = json.dumps(state.public(), ensure_ascii=False).encode()
                self.send_body(200, body, "application/json")
            elif self.path.startswith("/latest.jpg"):
                with state.lock:
                    jpeg = state.latest_jpeg
                self.send_body(200 if jpeg else 404, jpeg or b"", "image/jpeg")
            else:
                self.send_body(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.path != "/stop":
                self.send_body(404, b"not found", "text/plain")
                return
            if self.headers.get("Origin") != origin:
                self.send_body(403, b"invalid origin", "text/plain")
                return
            if self.headers.get_content_type() != "application/json":
                self.send_body(415, b"application/json required", "text/plain")
                return
            state.stop_requested = True
            state.stop.set()
            self.send_body(200, b'{"ok":true}', "application/json")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _save_event_image(
    state: TestState, frame: np.ndarray, event: dict[str, Any]
) -> dict[str, Any] | None:
    if len(state.event_images) >= MAX_EVENT_IMAGES:
        return None
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("Event PNG encoding failed")
    payload = encoded.tobytes()
    relative = Path("events") / f"{len(state.event_images) + 1:03d}-{event['kind']}.png"
    path = state.output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".png.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    result = {
        "path": str(relative),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }
    state.event_images.append(result)
    return result


def fresh_pair(observation: Any, pair: dict | None, seen_sequence: int, frame_age: float) -> bool:
    return (
        observation.status == "running"
        and observation.fresh
        and pair is not None
        and pair["sequence"] > seen_sequence
        and math.isfinite(frame_age)
        and 0 <= frame_age <= STALE_AFTER
        and observation.monotonic_at <= pair["completed_monotonic"]
    )


def inference_attempt(observation: Any, pair: dict | None, last_sequence: int) -> dict | None:
    """Expose timings for this completed attempt, including stale failures, never old work."""
    if (
        pair is None
        or observation.inference_ms is None
        or pair["sequence"] <= last_sequence
        or not math.isfinite(pair["completed_monotonic"])
        or pair["completed_monotonic"] < observation.monotonic_at
    ):
        return None
    return {
        key: pair[key]
        for key in (
            "sequence",
            "frame_sha256",
            "completed_monotonic",
            "stage_times_ms",
            "total_inference_ms",
        )
    }


def run(args: argparse.Namespace) -> int:
    code_hashes = {
        name: sha256(ROOT / name)
        for name in (
            "scripts/behavior_camera_test.py",
            "scripts/behavior_seat_gate.py",
            "scripts/behavior_timeline_v2.py",
            "scripts/behavior_diagnostics.py",
            "scripts/behavior_auxiliary.py",
            "visual_ai_agent/vision.py",
            "visual_ai_agent/models.py",
        )
    }
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {output}")
    posture_record = load_manifest(args.posture_manifest, "posture")
    drinking_record = load_manifest(args.drinking_manifest, "drinking")
    person_model = args.person_model.resolve()
    person_manifest = json.loads(args.person_manifest.read_text(encoding="utf-8"))
    person_hash = sha256(person_model)
    if person_hash != person_manifest.get("sha256"):
        raise ValueError("Person model manifest checksum mismatch")
    output.mkdir(parents=True)
    cv2.setNumThreads(args.opencv_threads)
    state = TestState(output, args.duration)
    state.model_info = {
        task: {
            "manifest": str(record["_path"]),
            "manifest_sha256": sha256(record["_path"]),
            "onnx": str(record["_onnx"]),
            "onnx_sha256": record["onnx_sha256"],
        }
        for task, record in (("posture", posture_record), ("drinking", drinking_record))
    }
    state.model_info["person_weak_auxiliary"] = {
        "model": str(person_model),
        "sha256": person_hash,
        "manifest": str(args.person_manifest.resolve()),
        "manifest_sha256": sha256(args.person_manifest.resolve()),
    }
    detector = BehaviorDetector(
        OnnxClassifier(posture_record),
        OnnxClassifier(drinking_record),
        person_model,
        person_threads=args.person_threads,
        person_spinning=args.person_spinning,
        auxiliary_mode=args.auxiliary_mode,
        person_wait_ms=args.person_wait_ms,
    )
    if args.person_association == "seat":
        from scripts.behavior_seat_gate import SeatPersonGate

        detector.gate = SeatPersonGate(roi=tuple(args.seat_roi))
    baseline_gate = PersonTrackGate(max_gap=STALE_AFTER)
    baseline_timeline = BehaviorTimelineV2(
        posture_confirm_seconds=POSTURE_CONFIRM,
        drinking_confirm_seconds=DRINK_CONFIRM,
        drinking_end_seconds=DRINK_END,
        unknown_bridge_seconds=UNKNOWN_BRIDGE,
        max_gap=STALE_AFTER,
    )
    diagnostics = TransitionEvidence(output)
    baseline_events = []
    timeline = BehaviorTimelineV2(
        posture_confirm_seconds=POSTURE_CONFIRM,
        drinking_confirm_seconds=DRINK_CONFIRM,
        drinking_end_seconds=DRINK_END,
        unknown_bridge_seconds=UNKNOWN_BRIDGE,
        max_gap=STALE_AFTER,
    )
    source = CameraSource(args.camera, width=args.width, height=args.height, backend=args.backend)
    process = psutil.Process()
    process.cpu_percent()
    seen_sequence = 0
    last_attempt_sequence = 0
    previous_raw_posture = "unknown"
    previous_continuous = False

    def observe(observation: Any, jpeg: bytes | None) -> None:
        nonlocal seen_sequence, last_attempt_sequence, previous_raw_posture, previous_continuous
        pair = (
            detector.snapshot(include_frame=observation.fresh)
            if observation.inference_ms is not None
            else None
        )
        attempt = inference_attempt(observation, pair, last_attempt_sequence)
        if attempt:
            last_attempt_sequence = attempt["sequence"]
        frame_age = time.monotonic() - observation.monotonic_at
        fresh = fresh_pair(observation, pair, seen_sequence, frame_age)
        if fresh:
            seen_sequence = pair["sequence"]
            raw_posture, raw_drinking = pair["posture"]["label"], pair["drinking"]["label"]
            pair["person_gate"] = detector.gate.observe(
                observation.monotonic_at,
                pair["person_candidates"],
                (observation.width, observation.height),
            )
            if pair["auxiliary_status"] != "ready":
                pair["person_gate"]["reason"] = "auxiliary_" + pair["auxiliary_status"]
            continuous = pair["person_gate"]["person_track_supported"] is True
            exit_evidence = pair["person_gate"].get("exit_evidence", False) is True
            baseline_support = baseline_gate.observe(
                observation.monotonic_at,
                pair["person_candidates"],
                (observation.width, observation.height),
            )["person_track_supported"]
        else:
            raw_posture = raw_drinking = "unknown"
            continuous = False
            exit_evidence = False
            detector.reset_gate()
            baseline_gate.reset()
            baseline_support = False
        result = timeline.observe(
            observation.monotonic_at,
            raw_posture,
            raw_drinking,
            fresh=fresh,
            continuous_visible=continuous,
            exit_evidence=exit_evidence,
        )
        baseline = baseline_timeline.observe(
            observation.monotonic_at,
            raw_posture,
            raw_drinking,
            fresh=fresh,
            continuous_visible=baseline_support,
        )
        baseline_events.extend(baseline["events"])
        row = {
            "baseline_result": baseline,
            # Attempt diagnostics never enter the classifier/state logic on a rejected frame.
            "inference_attempt": attempt,
            "processing_timings_ms": observation.processing_timings_ms,
            "processing_stage": observation.processing_stage,
            "worker_inference_ms": observation.inference_ms,
            "auxiliary_status": pair["auxiliary_status"] if attempt else "not_attempted",
            "auxiliary_error": pair["auxiliary_error"] if attempt else None,
            "drinking_auxiliary_status": pair["drinking_auxiliary_status"]
            if attempt
            else "not_attempted",
            "drinking_auxiliary_error": pair["drinking_auxiliary_error"] if attempt else None,
            "previous_callback_ms": worker.last_callback_ms,
            "person_candidates": [
                {"confidence": c.confidence, "bbox": list(c.bbox)}
                for c in pair["person_candidates"]
            ]
            if fresh
            else [],
            "recorded_at": _utcnow(),
            "observed_at": observation.observed_at.isoformat(),
            "monotonic_at": observation.monotonic_at,
            "status": observation.status,
            "fresh": fresh,
            "error": observation.error,
            "frame_sequence": pair["sequence"] if fresh else None,
            "frame_sha256": pair["frame_sha256"] if fresh else None,
            "posture": pair["posture"] if fresh else {"label": "unknown"},
            "drinking": pair["drinking"] if fresh else {"label": "unknown"},
            "person_gate_weak_auxiliary": pair["person_gate"]
            if fresh
            else {
                "person_track_supported": False,
                "exit_evidence": False,
                "reason": "not_fresh",
            },
            "confirmed_posture": result["posture"],
            "confirmed_drinking": result["drinking"],
            "events": result["events"],
            "frame_age_seconds": round(frame_age, 6),
            "total_inference_ms": pair["total_inference_ms"] if fresh else None,
            "person_inference_ms": pair["person_inference_ms"] if fresh else None,
            "cpu_percent_one_core_100": process.cpu_percent(),
            "rss_mib": round(process.memory_info().rss / 1024**2, 3),
        }
        trigger = (
            (raw_posture == "unknown" and previous_raw_posture != "unknown")
            or (previous_continuous and not continuous)
            or bool(result["events"])
        )
        diagnostics.observe(row, jpeg, trigger)
        previous_raw_posture, previous_continuous = raw_posture, continuous
        with state.lock:
            state.fresh_count += int(fresh)
            if not fresh and not state.first_fresh.is_set():
                state.startup_count += 1
            elif not fresh and observation.status != "stopped":
                state.fault_count += 1
            if fresh:
                if state.first_fresh_mono is None:
                    state.first_fresh_mono = observation.monotonic_at
                state.first_fresh.set()
            for event in result["events"]:
                saved = _save_event_image(state, pair["frame"], event) if fresh else None
                full_event = {
                    **event,
                    "recorded_at": row["recorded_at"],
                    "frame_sequence": row["frame_sequence"],
                    "image": saved,
                }
                state.events.append(full_event)
                _append_jsonl(output / "events.jsonl", full_event)
            state.samples.append(row)
            state.latest = row
            if jpeg:
                state.latest_jpeg = jpeg
                temporary = output / ".latest.jpg.tmp"
                temporary.write_bytes(jpeg)
                os.replace(temporary, output / "latest.jpg")
            _append_jsonl(output / "samples.jsonl", row)
        state.write_progress()

    worker = VisionWorker(detector, source, observe, INTERVAL, STALE_AFTER)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler_for(state))
    server_thread = threading.Thread(
        target=server.serve_forever, name="behavior-test-http", daemon=True
    )
    reached_deadline = False
    stop_error = None
    try:
        state.write_progress()
        server_thread.start()
        print(f"r03行为预览: http://127.0.0.1:{server.server_port}", flush=True)
        worker.start()
        startup_deadline = time.monotonic() + min(30.0, args.duration)
        while (
            not state.first_fresh.is_set()
            and not state.stop.is_set()
            and time.monotonic() < startup_deadline
        ):
            state.first_fresh.wait(0.1)
        reached_deadline = state.first_fresh.is_set() and not state.stop.wait(args.duration)
    except KeyboardInterrupt:
        state.stop_requested = True
    finally:
        worker.stop()
        if worker.running or worker.last_callback_error:
            stop_error = worker.last_callback_error or "worker did not stop"
        if not detector.close():
            stop_error = (stop_error + "; " if stop_error else "") + "auxiliary worker did not stop"
        server.shutdown()
        server.server_close()
        server_thread.join(5)
    fresh_rows = [row for row in state.samples if row["fresh"]]
    summary = state.public() | {
        "association": args.person_association,
        "seat_roi": args.seat_roi,
        "association_parameters": {
            key: getattr(detector.gate, key, None)
            for key in (
                "strong_confidence",
                "weak_confidence",
                "weak_seconds",
                "min_iou",
                "max_center_distance",
                "max_gap",
            )
        },
        "code_sha256": code_hashes,
        "baseline_events": baseline_events,
        "diagnostic_groups": diagnostics.groups,
        "finished_at": _utcnow(),
        "reached_deadline": reached_deadline,
        "stop_requested": state.stop_requested,
        "worker_stop_error": stop_error,
        "platform": platform.platform(),
        "camera_index": args.camera,
        "requested_size": [args.width, args.height],
        "actual_size": [source.actual_width, source.actual_height],
        "backend": source.backend_name,
        "parameters": {
            "person_threads": args.person_threads,
            "auxiliary_mode": args.auxiliary_mode,
            "person_wait_ms": args.person_wait_ms,
            "person_spinning": args.person_spinning,
            "opencv_threads": cv2.getNumThreads(),
            "opencv_requested_threads": args.opencv_threads,
            "fps": FPS,
            "posture_confirm_seconds": POSTURE_CONFIRM,
            "drinking_confirm_seconds": DRINK_CONFIRM,
            "drinking_end_seconds": DRINK_END,
            "max_gap_seconds": STALE_AFTER,
            "unknown_bridge_seconds": UNKNOWN_BRIDGE,
        },
        "latency_ms": {
            name: _metrics([float(row[name]) for row in fresh_rows if row[name] is not None])
            for name in ("total_inference_ms", "person_inference_ms")
        },
        "attempt_latency_ms_including_failures": {
            "worker_detect": _metrics(
                [
                    row["worker_inference_ms"]
                    for row in state.samples
                    if row["worker_inference_ms"] is not None
                ]
            ),
            "detector_stages": {
                stage: _metrics(
                    [
                        row["inference_attempt"]["stage_times_ms"][stage]
                        for row in state.samples
                        if row["inference_attempt"]
                        and stage in row["inference_attempt"]["stage_times_ms"]
                    ]
                )
                for stage in sorted(
                    {
                        key
                        for row in state.samples
                        if row["inference_attempt"]
                        for key in row["inference_attempt"]["stage_times_ms"]
                    }
                )
            },
        },
        "retention": {
            "video": False,
            "latest_jpeg_overwritten": True,
            "event_png_limit": MAX_EVENT_IMAGES,
        },
        "cloud_or_agent_calls": 0,
    }
    _atomic_json(output / "summary.json", summary)
    return (
        0
        if (reached_deadline or state.stop_requested) and not stop_error and state.fresh_count
        else 1
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posture-manifest", required=True, type=Path)
    parser.add_argument("--drinking-manifest", required=True, type=Path)
    parser.add_argument("--person-model", type=Path, default=ROOT / "models/yolo26n-e2e.onnx")
    parser.add_argument(
        "--person-manifest", type=Path, default=ROOT / "model_manifests/yolo26n-e2e.onnx.json"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="must be a new ignored/private directory"
    )
    parser.add_argument("--duration", type=float, choices=(30.0, 60.0, 180.0, 300.0), default=180.0)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--person-association", choices=("strict", "seat"), default="strict")
    parser.add_argument(
        "--person-threads",
        type=int,
        choices=(1, 2, 4),
        default=4 if (os.cpu_count() or 1) >= 8 else 2,
    )
    parser.add_argument("--person-spinning", action="store_true")
    parser.add_argument("--auxiliary-mode", choices=("serial", "bounded"), default="bounded")
    parser.add_argument(
        "--person-wait-ms", type=float, choices=(80.0, 100.0, 120.0, 150.0), default=120.0
    )
    parser.add_argument("--opencv-threads", type=int, choices=(1, 2, 4, 10), default=1)
    parser.add_argument("--seat-roi", type=float, nargs=4, default=(0.2, 0.2, 0.95, 1.0))
    parser.add_argument("--backend", type=int)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.camera < 0 or args.width <= 0 or args.height <= 0 or not 0 <= args.port <= 65535:
        parser.error("invalid camera, dimensions, or port")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
