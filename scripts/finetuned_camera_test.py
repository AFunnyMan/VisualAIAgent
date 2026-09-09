"""Compare an experimental scene model with production detection on one live camera.

This is an isolated manual test: it never reads ``.env``, production data, or Agent
credentials. It retains bounded still images rather than video.
"""

# The embedded page is deliberately kept in this standalone script.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
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
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.camera_acceptance import (  # noqa: E402
    _append_jsonl,
    _atomic_json,
    observation_bucket,
    observation_key,
)
from scripts.evaluate_scene_model import ExperimentalDetector, sha256  # noqa: E402
from visual_ai_agent.memory import MemoryStore  # noqa: E402
from visual_ai_agent.models import Detection, SceneObservation  # noqa: E402
from visual_ai_agent.vision import (  # noqa: E402
    CameraSource,
    CupScaleRecheckDetector,
    VisionWorker,
    YoloOnnxDetector,
    annotate_frame,
)

CONFIDENCE = 0.35
SAMPLE_INTERVAL = 1.0
STALE_AFTER = 2.5
BASELINE_PATH = ROOT / "models/yolo26n-e2e.onnx"
BASELINE_MANIFEST = ROOT / "model_manifests/yolo26n-e2e.onnx.json"
MAX_PERIODIC_IMAGES = 12
MAX_DISAGREEMENT_IMAGES = 12


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _detections(value: list[Detection]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in value]


def disagreement(candidate: list[Detection], baseline: list[Detection]) -> bool:
    """Flag observable class/count/region differences without pixel-level jitter."""

    def signature(rows: list[Detection]) -> list[tuple[str, str]]:
        return sorted((item.category, item.region) for item in rows)

    return signature(candidate) != signature(baseline)


def _write_png(path: Path, frame: Any) -> dict[str, Any]:
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    payload = encoded.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return {
        "path": str(path.relative_to(path.parent.parent)),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


@dataclass(frozen=True)
class PairResult:
    frame: Any
    sequence: int
    candidate: list[Detection]
    baseline: list[Detection]
    candidate_ms: float
    baseline_ms: float
    candidate_started_at: str
    baseline_started_at: str
    frame_sha256: str


class PairedDetector:
    """Run candidate first and production baseline second on the same frame."""

    def __init__(self, candidate: Any, baseline: Any) -> None:
        self.candidate = candidate
        self.baseline = baseline
        self._lock = threading.Lock()
        self._sequence = 0
        self._latest: PairResult | None = None

    def detect(self, frame: Any) -> list[Detection]:
        candidate_started = _utcnow()
        started = time.perf_counter()
        candidate = self.candidate.detect(frame)
        candidate_ms = 1000 * (time.perf_counter() - started)
        baseline_started = _utcnow()
        started = time.perf_counter()
        baseline = self.baseline.detect(frame)
        baseline_ms = 1000 * (time.perf_counter() - started)
        with self._lock:
            self._sequence += 1
            self._latest = PairResult(
                frame.copy(),
                self._sequence,
                candidate,
                baseline,
                candidate_ms,
                baseline_ms,
                candidate_started,
                baseline_started,
                hashlib.sha256(memoryview(frame)).hexdigest(),
            )
        return candidate

    def snapshot(self) -> PairResult | None:
        with self._lock:
            value = self._latest
            if value is None:
                return None
            return PairResult(
                value.frame.copy(),
                value.sequence,
                list(value.candidate),
                list(value.baseline),
                value.candidate_ms,
                value.baseline_ms,
                value.candidate_started_at,
                value.baseline_started_at,
                value.frame_sha256,
            )


class TestState:
    def __init__(self, output: Path, duration: float) -> None:
        self.output = output
        self.duration = duration
        self.started_at = _utcnow()
        self.started_mono = time.monotonic()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.first_fresh = threading.Event()
        self.stop_requested = False
        self.latest: dict[str, Any] | None = None
        self.latest_jpeg: bytes | None = None
        self.samples: list[dict[str, Any]] = []
        self.events = {"candidate": [], "baseline": []}
        self.seen: set[tuple[str, float, str, bool]] = set()
        self.fresh_count = 0
        self.fault_count = 0
        self.periodic_images: list[dict[str, Any]] = []
        self.disagreement_images: list[dict[str, Any]] = []
        self.last_fresh_monotonic: float | None = None
        self.model_info: dict[str, Any] = {}

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "experimental_model": True,
                "preview_model": "finetuned_candidate",
                "started_at": self.started_at,
                "elapsed_seconds": round(time.monotonic() - self.started_mono, 3),
                "target_seconds": self.duration,
                "latest": self.latest,
                "fresh_observations": self.fresh_count,
                "fault_observations": self.fault_count,
                "event_counts": {key: len(value) for key, value in self.events.items()},
                "retained_png_counts": {
                    "periodic": len(self.periodic_images),
                    "disagreement": len(self.disagreement_images),
                },
                "models": dict(self.model_info),
                "notice": "实验模型实时对照；不调用 Agent，结果不会写入生产数据库。",
            }

    def write_progress(self) -> None:
        _atomic_json(self.output / "progress.json", self.public())


def _page() -> str:
    return """<!doctype html><meta charset=utf-8><title>实验模型摄像头对照</title>
<style>body{font:16px system-ui;max-width:1100px;margin:16px auto;padding:0 16px}img{display:block;max-width:100%;background:#222}#s{background:#f4f4f4;padding:10px;line-height:1.6}.warn{color:#9b3d00;font-weight:bold}pre{white-space:pre-wrap;overflow:auto}</style>
<h1>实验模型摄像头对照</h1><p class=warn>当前预览框来自本次实验模型，不是正式产品模型。</p><div id=s>等待摄像头...</div><img id=p alt="等待最新画面"><p><button onclick="stopRun()">停止测试</button></p><details><summary>完整运行状态 JSON</summary><pre id=j></pre></details>
<script>const names={'cell phone':'手机',cup:'杯子',bottle:'瓶子'};function ds(row){return(row&&row.detections||[]).map(x=>(names[x.category]||x.category)+' '+Math.round(x.confidence*100)+'%（'+x.region+'）').join('、')||'无'}async function poll(){try{let r=await fetch('/progress.json',{cache:'no-store'}),x=await r.json(),l=x.latest||{},left=Math.max(0,x.target_seconds-x.elapsed_seconds);document.getElementById('s').innerHTML='剩余：<b>'+Math.ceil(left)+' 秒</b>　新鲜帧：'+x.fresh_observations+'　故障：'+x.fault_observations+'<br>实验候选：'+ds(l.candidate)+'<br>正式基线：'+ds(l.baseline);document.getElementById('j').textContent=JSON.stringify(x,null,2);document.getElementById('p').src='/latest.jpg?t='+Date.now()}catch(e){document.getElementById('s').textContent=e.message}}async function stopRun(){await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});poll()}setInterval(poll,1000);poll()</script>"""


def _handler_for(state: TestState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(200, _page().encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/progress.json"):
                self._send(
                    200, json.dumps(state.public(), ensure_ascii=False).encode(), "application/json"
                )
            elif self.path.startswith("/latest.jpg"):
                with state.lock:
                    jpeg = state.latest_jpeg
                self._send(200 if jpeg else 404, jpeg or b"", "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/stop":
                self._send(404, b"not found", "text/plain")
                return
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Origin") != expected_origin:
                self._send(403, b"invalid origin", "text/plain")
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, b"application/json required", "text/plain")
                return
            state.stop_requested = True
            state.stop.set()
            self._send(200, b'{"ok":true}', "application/json")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _metrics(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 3) if ordered else None,
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)
        if ordered
        else None,
        "max": round(max(ordered), 3) if ordered else None,
    }


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {output}")
    output.mkdir(parents=True)
    candidate_path = args.candidate.resolve()
    candidate_hash = sha256(candidate_path)
    if candidate_hash.lower() != args.candidate_sha.lower():
        raise SystemExit("Candidate checksum mismatch")
    baseline_hash = sha256(BASELINE_PATH)
    baseline_manifest_hash = sha256(BASELINE_MANIFEST)
    state = TestState(output, args.duration)
    state.model_info = {
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_hash,
        "preserve_coco_head": args.preserve_coco_head,
        "candidate_cup_recheck": args.candidate_cup_recheck,
        "baseline_path": str(BASELINE_PATH),
        "baseline_sha256": baseline_hash,
        "baseline_manifest_sha256": baseline_manifest_hash,
    }
    candidate_store = MemoryStore(output / "memory-candidate", max_gap_seconds=STALE_AFTER)
    baseline_store = MemoryStore(output / "memory-baseline", max_gap_seconds=STALE_AFTER)
    candidate = ExperimentalDetector(
        candidate_path,
        args.candidate_sha.lower(),
        confidence=CONFIDENCE,
        preserve_coco_head=args.preserve_coco_head,
    )
    if args.candidate_cup_recheck:
        candidate = CupScaleRecheckDetector(candidate)
    official = YoloOnnxDetector(BASELINE_PATH, confidence=CONFIDENCE)
    paired = PairedDetector(candidate, CupScaleRecheckDetector(official))
    source = CameraSource(args.camera, width=args.width, height=args.height, backend=args.backend)
    process = psutil.Process()
    process.cpu_percent()

    def observe(observation: SceneObservation, jpeg: bytes | None) -> None:
        key = observation_key(observation)
        with state.lock:
            if key in state.seen:
                return
            state.seen.add(key)
        pair = paired.snapshot() if observation.status == "running" and observation.fresh else None
        if pair is not None:
            candidate_observation = observation.model_copy(
                update={"detections": pair.candidate, "inference_ms": pair.candidate_ms}
            )
            baseline_observation = observation.model_copy(
                update={"detections": pair.baseline, "inference_ms": pair.baseline_ms}
            )
        else:
            candidate_observation = observation.model_copy(update={"detections": []})
            baseline_observation = observation.model_copy(update={"detections": []})
        candidate_events = candidate_store.ingest(candidate_observation, jpeg)
        baseline_jpeg: bytes | None = None
        if pair is not None:
            baseline_rendered = annotate_frame(pair.frame, pair.baseline)
            encoded_ok, encoded = cv2.imencode(
                ".jpg", baseline_rendered, [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            if not encoded_ok:
                raise RuntimeError("Baseline JPEG encoding failed")
            baseline_jpeg = encoded.tobytes()
        baseline_events = baseline_store.ingest(baseline_observation, baseline_jpeg)
        row: dict[str, Any] = {
            "recorded_at": _utcnow(),
            "observed_at": observation.observed_at.isoformat(),
            "monotonic_at": observation.monotonic_at,
            "status": observation.status,
            "fresh": observation.fresh,
            "error": observation.error,
            "width": observation.width,
            "height": observation.height,
            "pair_sequence": pair.sequence if pair else None,
            "input_frame_sha256": pair.frame_sha256 if pair else None,
            "candidate": {
                "detections": _detections(candidate_observation.detections),
                "inference_ms": candidate_observation.inference_ms,
                "started_at": pair.candidate_started_at if pair else None,
                "event_ids": [event.event_id for event in candidate_events],
            },
            "baseline": {
                "detections": _detections(baseline_observation.detections),
                "inference_ms": baseline_observation.inference_ms,
                "started_at": pair.baseline_started_at if pair else None,
                "event_ids": [event.event_id for event in baseline_events],
            },
            "pair_total_inference_ms": observation.inference_ms,
            "candidate_to_baseline_start_ms": (pair.candidate_ms if pair else None),
            "cpu_percent_one_core_100": process.cpu_percent(),
            "rss_mib": round(process.memory_info().rss / 1024**2, 3),
        }
        with state.lock:
            bucket = observation_bucket(observation, state.fresh_count > 0)
            if bucket == "fresh":
                state.fresh_count += 1
                state.first_fresh.set()
                assert pair is not None
                row["fresh_interval_seconds"] = (
                    None
                    if state.last_fresh_monotonic is None
                    else round(observation.monotonic_at - state.last_fresh_monotonic, 6)
                )
                state.last_fresh_monotonic = observation.monotonic_at
                if len(state.periodic_images) < MAX_PERIODIC_IMAGES and (
                    state.fresh_count == 1 or state.fresh_count % 30 == 0
                ):
                    info = _write_png(
                        output / "periodic" / f"fresh-{state.fresh_count:04d}.png", pair.frame
                    )
                    info.update(fresh_index=state.fresh_count, pair_sequence=pair.sequence)
                    state.periodic_images.append(info)
                if (
                    disagreement(pair.candidate, pair.baseline)
                    and len(state.disagreement_images) < MAX_DISAGREEMENT_IMAGES
                ):
                    info = _write_png(
                        output / "disagreements" / f"pair-{pair.sequence:04d}.png", pair.frame
                    )
                    info.update(fresh_index=state.fresh_count, pair_sequence=pair.sequence)
                    state.disagreement_images.append(info)
                    row["disagreement_image"] = info["path"]
            elif bucket == "fault":
                state.fault_count += 1
            serialized_candidate = [event.model_dump(mode="json") for event in candidate_events]
            serialized_baseline = [event.model_dump(mode="json") for event in baseline_events]
            state.events["candidate"].extend(serialized_candidate)
            state.events["baseline"].extend(serialized_baseline)
            state.samples.append(row)
            state.latest = row
            if jpeg:
                state.latest_jpeg = jpeg
                temporary = output / ".latest.jpg.tmp"
                temporary.write_bytes(jpeg)
                os.replace(temporary, output / "latest.jpg")
            _append_jsonl(output / "samples.jsonl", row)
            for name, events in (
                ("candidate", serialized_candidate),
                ("baseline", serialized_baseline),
            ):
                for event in events:
                    _append_jsonl(output / f"events-{name}.jsonl", event)
        state.write_progress()

    worker = VisionWorker(paired, source, observe, SAMPLE_INTERVAL, STALE_AFTER)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler_for(state))
    server_thread = threading.Thread(
        target=server.serve_forever, name="finetune-test-http", daemon=True
    )
    reached_deadline = False
    stop_error: str | None = None
    try:
        state.write_progress()
        server_thread.start()
        print(f"实验模型预览: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"进度: {output / 'progress.json'}", flush=True)
        print(f"最新画面: {output / 'latest.jpg'}", flush=True)
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
        server.shutdown()
        server.server_close()
        server_thread.join(5)
    with state.lock:
        samples = list(state.samples)
        events = {key: list(value) for key, value in state.events.items()}
    fresh = [row for row in samples if row["fresh"] and row["status"] == "running"]
    summary = state.public() | {
        "finished_at": _utcnow(),
        "reached_deadline": reached_deadline,
        "stop_requested": state.stop_requested,
        "worker_stop_error": stop_error,
        "platform": platform.platform(),
        "camera_index": args.camera,
        "requested_size": [args.width, args.height],
        "actual_size": [source.actual_width, source.actual_height],
        "backend": source.backend_name,
        "confidence": CONFIDENCE,
        "sample_interval_seconds": SAMPLE_INTERVAL,
        "stale_after_seconds": STALE_AFTER,
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_hash,
        "preserve_coco_head": args.preserve_coco_head,
        "candidate_cup_recheck": args.candidate_cup_recheck,
        "baseline_path": str(BASELINE_PATH),
        "baseline_sha256": baseline_hash,
        "baseline_manifest_sha256": baseline_manifest_hash,
        "baseline_pipeline": "official YOLO26n + CupScaleRecheckDetector",
        "execution_order": "candidate first, then production baseline on the same frame",
        "same_source_and_frame": True,
        "cloud_or_agent_calls": 0,
        "retention": {
            "video": False,
            "latest_jpeg_overwritten": True,
            "periodic_png_limit": MAX_PERIODIC_IMAGES,
            "disagreement_png_limit": MAX_DISAGREEMENT_IMAGES,
        },
        "inference_ms": {
            name: _metrics(
                [
                    row[name]["inference_ms"]
                    for row in fresh
                    if row[name]["inference_ms"] is not None
                ]
            )
            for name in ("candidate", "baseline")
        },
        "events": events,
        "samples": samples,
    }
    _atomic_json(output / "summary.json", summary)
    return 0 if reached_deadline and not stop_error and state.fault_count == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument(
        "--preserve-coco-head",
        "--preserve-80",
        dest="preserve_coco_head",
        action="store_true",
        help="candidate retains the original 80-class COCO head",
    )
    parser.add_argument("--candidate-cup-recheck", action="store_true")
    parser.add_argument("--output", required=True, type=Path, help="must be a new directory")
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--backend", type=int)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if (
        args.duration <= 0
        or args.camera < 0
        or args.width <= 0
        or args.height <= 0
        or not 0 <= args.port <= 65535
    ):
        parser.error("invalid duration, camera, dimensions, or port")
    if len(args.candidate_sha) != 64 or any(
        c not in "0123456789abcdefABCDEF" for c in args.candidate_sha
    ):
        parser.error("candidate SHA-256 must be 64 hexadecimal characters")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
