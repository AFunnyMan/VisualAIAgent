"""Run a local, manual camera acceptance console without any cloud/Agent calls.

The operator must arrange each real scene and click the matching marker. Markers
are operator notes, not proof that detection was correct. The tool retains the
latest preview, event evidence, and explicit marker snapshots, never full video.
"""

# The embedded single-file HTML keeps deployment and review simple.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import cv2
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.memory import MemoryStore  # noqa: E402
from visual_ai_agent.models import CATEGORIES, SceneObservation  # noqa: E402
from visual_ai_agent.vision import (  # noqa: E402
    CameraSource,
    CupScaleRecheckDetector,
    VisionWorker,
    YoloOnnxDetector,
)

MAX_BODY_BYTES = 1024
KNOWN_ACTIONS = frozenset({"empty_ready", "placed", "removed", "stop"})


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _append_jsonl(path: Path, value: object) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def observation_key(observation: SceneObservation) -> tuple[str, float, str, bool]:
    """Deduplicate callbacks while preserving status changes for the same frame."""
    return (
        observation.observed_at.isoformat(),
        observation.monotonic_at,
        observation.status,
        observation.fresh,
    )


def observation_bucket(observation: SceneObservation, has_fresh: bool) -> str:
    if observation.fresh and observation.status == "running":
        return "fresh"
    if not has_fresh:
        return "startup"
    if observation.status in {"stale", "disconnected", "error"}:
        return "fault"
    return "other"


class RecentInputDetector:
    """Keep one raw detector input in memory for explicit operator snapshots."""

    def __init__(self, detector: Any) -> None:
        self.detector = detector
        self._lock = threading.Lock()
        self._sequence = 0
        self._latest: tuple[Any, dict[str, Any]] | None = None

    def detect(self, frame_bgr: Any) -> Any:
        with self._lock:
            self._sequence += 1
            metadata = {
                "detector_sequence": self._sequence,
                "detector_input_at": _utcnow(),
                "detector_input_monotonic": time.monotonic(),
            }
            self._latest = (frame_bgr.copy(), metadata)
        return self.detector.detect(frame_bgr)

    def snapshot(self) -> tuple[Any, dict[str, Any]] | None:
        with self._lock:
            if self._latest is None:
                return None
            frame, metadata = self._latest
            return frame.copy(), dict(metadata)


def validate_command(payload: dict[str, Any], csrf: str) -> tuple[str, str | None]:
    """Validate the complete, deliberately tiny POST command vocabulary."""
    if set(payload) - {"action", "category", "csrf"}:
        raise ValueError("请求包含未知字段")
    supplied_csrf = payload.get("csrf")
    action = payload.get("action")
    raw_category = payload.get("category")
    if not isinstance(supplied_csrf, str) or not supplied_csrf.isascii():
        raise ValueError("CSRF 必须是 ASCII 字符串")
    if not isinstance(action, str):
        raise ValueError("操作必须是字符串")
    if raw_category is not None and not isinstance(raw_category, str):
        raise ValueError("类别必须是字符串")
    if not secrets.compare_digest(supplied_csrf, csrf):
        raise ValueError("CSRF 校验失败")
    category = raw_category or None
    if action not in KNOWN_ACTIONS:
        raise ValueError("未知操作")
    if action in {"placed", "removed"} and category not in CATEGORIES:
        raise ValueError("放入/移出必须选择已知类别")
    if action in {"empty_ready", "stop"} and category is not None:
        raise ValueError("该操作不接受类别")
    return str(action), str(category) if category else None


def validate_http_input(
    *, origin: str | None, expected_origin: str, content_type: str, content_length: str
) -> int:
    if origin is not None and origin != expected_origin:
        raise PermissionError("来源校验失败")
    if content_type not in {"application/json", "application/x-www-form-urlencoded"}:
        raise ValueError("仅接受 JSON 或表单请求")
    try:
        length = int(content_length)
    except ValueError as exc:
        raise ValueError("请求体大小无效") from exc
    if not 0 <= length <= MAX_BODY_BYTES:
        raise ValueError("请求体大小无效")
    return length


def acceptance_result(
    *,
    reached_deadline: bool,
    fresh_count: int,
    fault_count: int,
    stop_requested: bool,
    continuous_seconds: float,
    required_seconds: float,
    maximum_fresh_gap: float,
    allowed_fresh_gap: float,
) -> tuple[bool, str]:
    if stop_requested:
        return False, "由页面提前停止，未达到完整时长"
    if not reached_deadline:
        return False, "运行未达到设定时长"
    if fresh_count == 0:
        return False, "全程没有有效新画面"
    if fault_count:
        return False, "运行期间出现无帧、断连、过期或错误状态"
    if continuous_seconds < required_seconds:
        return False, "有效新画面没有覆盖完整设定时长"
    if maximum_fresh_gap > allowed_fresh_gap:
        return False, "有效新画面间隔超过允许上限"
    return True, "连续运行时长和画面状态满足本次稳定性条件"


@dataclass
class ConsoleState:
    output: Path
    duration: float
    max_raw_age_seconds: float = 2.5
    observation_region: tuple[float, float, float, float] | None = None
    started_wall: str = field(default_factory=_utcnow)
    started_mono: float = field(default_factory=time.monotonic)
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    lock: threading.RLock = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    first_fresh_event: threading.Event = field(default_factory=threading.Event)
    stop_requested: bool = False
    latest: dict[str, Any] | None = None
    latest_jpeg: bytes | None = None
    seen: set[tuple[str, float, str, bool]] = field(default_factory=set)
    samples: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    markers: list[dict[str, Any]] = field(default_factory=list)
    fresh_count: int = 0
    fault_count: int = 0
    startup_status_count: int = 0
    first_fresh_mono: float | None = None
    last_fresh_mono: float | None = None
    fresh_received_monos: list[float] = field(default_factory=list)
    latest_completed_raw: tuple[Any, dict[str, Any]] | None = None
    raw_manifest: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        with self.lock:
            elapsed = max(0.0, time.monotonic() - self.started_mono)
            acceptance_elapsed = (
                0.0
                if self.first_fresh_mono is None
                else max(0.0, time.monotonic() - self.first_fresh_mono)
            )
            return {
                "started_at": self.started_wall,
                "elapsed_seconds": round(elapsed, 3),
                "target_seconds": self.duration,
                "observation_region": self.observation_region,
                "acceptance_elapsed_seconds": round(acceptance_elapsed, 3),
                "latest": self.latest,
                "fresh_observations": self.fresh_count,
                "fault_observations": self.fault_count,
                "startup_status_observations": self.startup_status_count,
                "event_count": len(self.events),
                "marker_count": len(self.markers),
                "events": list(self.events[-30:]),
                "markers": list(self.markers[-60:]),
                "instructions": "请实际布置场景后再点击；人工标记仅记录操作，不证明识别正确。",
            }

    def write_progress(self) -> None:
        with self.lock:
            _atomic_json(self.output / "progress.json", self.public())

    def add_marker(self, action: str, category: str | None) -> None:
        marker_id = secrets.token_hex(8)
        marker = {
            "marker_id": marker_id,
            "server_time": _utcnow(),
            "action": action,
            "category": category,
        }
        with self.lock:
            if action in {"placed", "removed"}:
                if self.latest_completed_raw is None:
                    marker["raw_input_unavailable"] = "没有可关联的正常新鲜检测输入"
                else:
                    frame, alignment = self.latest_completed_raw
                    age = max(0.0, time.monotonic() - alignment["observation_monotonic_at"])
                    if age > self.max_raw_age_seconds:
                        marker["raw_input_unavailable"] = "最近检测输入已超过新鲜度上限"
                        marker["raw_input_age_seconds"] = round(age, 6)
                    else:
                        relative_path = f"raw_markers/{marker_id}.png"
                        encoded_ok, encoded = cv2.imencode(".png", frame)
                        if not encoded_ok:
                            marker["raw_input_unavailable"] = "原始输入PNG编码失败"
                        else:
                            png = encoded.tobytes()
                            destination = self.output / relative_path
                            destination.parent.mkdir(exist_ok=True)
                            temporary = destination.with_suffix(".png.tmp")
                            temporary.write_bytes(png)
                            os.replace(temporary, destination)
                            reference = {
                                "marker_id": marker_id,
                                "raw_image": relative_path,
                                "png_sha256": hashlib.sha256(png).hexdigest(),
                                "raw_input_age_seconds": round(age, 6),
                                "marker_server_time": marker["server_time"],
                                **alignment,
                                "qualification": (
                                    "该图是点击前最近一次已完成检测的原始输入；点击时间与画面采集、"
                                    "检测输入时间不同，不代表同一瞬间。"
                                ),
                            }
                            marker["raw_input_reference"] = reference
                            self.raw_manifest.append(reference)
                            _atomic_json(
                                self.output / "raw_markers" / "manifest.json",
                                self.raw_manifest,
                            )
            self.markers.append(marker)
            _atomic_json(self.output / "markers.json", self.markers)
            self.write_progress()


def _handler_for(state: ConsoleState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CameraAcceptance/1"

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
                self._send(200, _page(state.csrf).encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
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
            if self.path != "/api/action":
                self._send(404, b"not found", "text/plain")
                return
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            try:
                length = validate_http_input(
                    origin=origin,
                    expected_origin=expected,
                    content_type=self.headers.get_content_type(),
                    content_length=self.headers.get("Content-Length", "-1"),
                )
                raw = self.rfile.read(length)
                if self.headers.get_content_type() == "application/json":
                    payload = json.loads(raw)
                else:
                    payload = {key: values[-1] for key, values in parse_qs(raw.decode()).items()}
                if not isinstance(payload, dict):
                    raise ValueError("请求必须是对象")
                action, category = validate_command(payload, state.csrf)
                if action == "stop":
                    with state.lock:
                        state.stop_requested = True
                    state.stop_event.set()
                else:
                    state.add_marker(action, category)
                self._send(200, b'{"ok":true}', "application/json")
            except PermissionError as exc:
                self._send(403, str(exc).encode(), "text/plain; charset=utf-8")
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json",
                )

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _page(csrf: str) -> str:
    return f"""<!doctype html><meta charset=utf-8><title>摄像头实机验收</title>
<style>body{{font:16px system-ui;max-width:960px;margin:24px auto;padding:0 16px}}img{{max-width:100%;background:#222}}button{{margin:5px;padding:9px}}#status{{background:#f4f4f4;padding:12px;line-height:1.7}}#error{{color:#b00020}}</style>
<h1>摄像头实机验收</h1><p><b>请先实际布置场景再点击。</b>人工标记只记录操作时间，不证明识别正确；最终准确率需人工核对。</p>
<div id=status>正在等待摄像头...</div><div id=error></div>
<img id=preview alt="等待最新画面"><div id=buttons>
<button onclick="send('empty_ready')">空桌面已就绪</button><br>
<span>手机：</span><button onclick="send('placed','cell phone')">已放入</button><button onclick="send('removed','cell phone')">已移出</button><br>
<span>杯子：</span><button onclick="send('placed','cup')">已放入</button><button onclick="send('removed','cup')">已移出</button><br>
<span>瓶子：</span><button onclick="send('placed','bottle')">已放入</button><button onclick="send('removed','bottle')">已移出</button><br>
<button onclick="send('stop')">提前停止（本次不通过）</button></div>
<script>const statusBox=document.getElementById('status'),errorBox=document.getElementById('error'),previewImage=document.getElementById('preview');const csrf={json.dumps(csrf)};const names={{'cell phone':'手机',cup:'杯子',bottle:'瓶子'}},states={{running:'正常采集',stopped:'已停止',stale:'画面过期',disconnected:'已断开',error:'异常',paused:'已暂停'}},regions={{left:'左侧',center:'中间',right:'右侧'}};async function send(action,category){{try{{let p={{action,csrf}};if(category)p.category=category;let r=await fetch('/api/action',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(p)}});if(!r.ok)throw new Error(await r.text());errorBox.textContent='操作已记录'}}catch(e){{errorBox.textContent='操作失败：'+e.message}}await poll()}}async function poll(){{try{{let r=await fetch('/api/state',{{cache:'no-store'}});if(!r.ok)throw new Error('状态读取失败');let s=await r.json(),l=s.latest||{{}},left=Math.max(0,s.target_seconds-s.acceptance_elapsed_seconds),ds=(l.detections||[]).map(d=>names[d.category]+' '+Math.round(d.confidence*100)+'%（'+(regions[d.region]||d.region)+'）').join('、')||'暂无';statusBox.innerHTML='摄像头状态：<b>'+(states[l.status]||'启动中')+'</b><br>剩余时长：'+Math.ceil(left)+' 秒<br>有效画面：'+s.fresh_observations+'，异常状态：'+s.fault_observations+'<br>观察范围：'+(s.observation_region?'仅预览中的裁剪范围（范围外不判断）':'全画面')+'<br>当前检测：'+ds+'<br>视觉事件：'+s.event_count+'，人工标记：'+s.marker_count;previewImage.src='/latest.jpg?t='+Date.now()}}catch(e){{errorBox.textContent='页面更新失败：'+e.message}}}}setInterval(poll,1000);poll()</script>"""


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"拒绝覆盖已有输出目录: {output}")
    output.mkdir(parents=True, exist_ok=False)
    config = Config.from_env()
    store = MemoryStore(output / "memory", max_gap_seconds=config.sample_interval * 2.5)
    state = ConsoleState(
        output=output,
        duration=args.duration,
        max_raw_age_seconds=config.sample_interval * 2.5,
    )
    process = psutil.Process()
    process.cpu_percent()
    width = args.width if args.width is not None else config.camera_width
    height = args.height if args.height is not None else config.camera_height
    region = tuple(args.region) if args.region is not None else config.observation_region
    source = CameraSource(
        args.camera,
        width=width,
        height=height,
        backend=args.backend,
        observation_region=region,
    )
    state.observation_region = source.observation_region
    local_detector = YoloOnnxDetector(
        config.model_path,
        confidence=config.confidence,
        expected_sha256=config.model_sha256 or None,
    )
    if config.cup_scale_recheck:
        local_detector = CupScaleRecheckDetector(local_detector)
    detector = RecentInputDetector(local_detector)

    def observe(observation: SceneObservation, jpeg: bytes | None) -> None:
        key = observation_key(observation)
        with state.lock:
            if key in state.seen:
                return
            state.seen.add(key)
        events = store.ingest(observation, jpeg)
        now_mono = time.monotonic()
        sample = {
            "recorded_at": _utcnow(),
            "observed_at": key[0],
            "monotonic_at": key[1],
            "status": observation.status,
            "fresh": observation.fresh,
            "error": observation.error,
            "width": observation.width,
            "height": observation.height,
            "inference_ms": observation.inference_ms,
            "cpu_percent_one_core_100": process.cpu_percent(),
            "rss_mib": round(process.memory_info().rss / 1024**2, 3),
            "detections": [item.model_dump(mode="json") for item in observation.detections],
            "event_ids": [item.event_id for item in events],
        }
        with state.lock:
            state.latest = sample
            state.samples.append(sample)
            serialized_events = [item.model_dump(mode="json") for item in events]
            state.events.extend(serialized_events)
            raw_snapshot = detector.snapshot()
            if observation.fresh and observation.status == "running" and raw_snapshot is not None:
                raw_frame, raw_metadata = raw_snapshot
                state.latest_completed_raw = (
                    raw_frame,
                    raw_metadata
                    | {
                        "observation_captured_at": observation.observed_at.isoformat(),
                        "observation_monotonic_at": observation.monotonic_at,
                    },
                )
            elif not observation.fresh or observation.status != "running":
                state.latest_completed_raw = None
            _append_jsonl(output / "samples.jsonl", sample)
            for event in serialized_events:
                _append_jsonl(output / "events.jsonl", event)
            bucket = observation_bucket(observation, state.first_fresh_mono is not None)
            if bucket == "fresh":
                state.fresh_count += 1
                state.first_fresh_mono = state.first_fresh_mono or now_mono
                state.last_fresh_mono = now_mono
                state.fresh_received_monos.append(now_mono)
                state.first_fresh_event.set()
            elif bucket == "startup":
                state.startup_status_count += 1
            elif bucket == "fault":
                state.fault_count += 1
            if jpeg:
                state.latest_jpeg = jpeg
                temporary = output / ".latest.jpg.tmp"
                temporary.write_bytes(jpeg)
                os.replace(temporary, output / "latest.jpg")
        state.write_progress()

    worker = VisionWorker(
        detector,
        source,
        observe,
        inference_interval=config.sample_interval,
        stale_after=config.sample_interval * 2.5,
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler_for(state))
    server_thread = threading.Thread(
        target=server.serve_forever, name="acceptance-http", daemon=True
    )
    reached_deadline = False
    try:
        state.write_progress()
        server_thread.start()
        print(f"测试页: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"本地证据目录: {output}", flush=True)
        worker.start()
        startup_deadline = time.monotonic() + min(30.0, args.duration)
        while (
            not state.first_fresh_event.is_set()
            and not state.stop_event.is_set()
            and time.monotonic() < startup_deadline
        ):
            state.first_fresh_event.wait(0.1)
        got_first_frame = state.first_fresh_event.is_set()
        reached_deadline = got_first_frame and not state.stop_event.wait(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        acceptance_ended_mono = time.monotonic()
        worker.stop()
        stop_failed = worker.running or worker.last_callback_error is not None
        stopped_observation, _ = worker.snapshot()
        if stopped_observation is not None and stopped_observation.status == "error":
            stop_failed = True
        server.shutdown()
        server.server_close()
        server_thread.join(5)
    elapsed = time.monotonic() - state.started_mono
    continuous = (
        0.0
        if state.first_fresh_mono is None
        else max(0.0, acceptance_ended_mono - state.first_fresh_mono)
    )
    with state.lock:
        coverage_points = list(state.fresh_received_monos)
    if coverage_points:
        gaps = [
            newer - older
            for older, newer in zip(coverage_points, coverage_points[1:], strict=False)
        ]
        gaps.append(max(0.0, acceptance_ended_mono - coverage_points[-1]))
        maximum_fresh_gap = max(gaps, default=0.0)
    else:
        maximum_fresh_gap = float("inf")
    allowed_fresh_gap = config.sample_interval * 2.5
    effective_faults = state.fault_count + int(stop_failed)
    passed, reason = acceptance_result(
        reached_deadline=reached_deadline,
        fresh_count=state.fresh_count,
        fault_count=effective_faults,
        stop_requested=state.stop_requested,
        continuous_seconds=continuous,
        required_seconds=args.duration,
        maximum_fresh_gap=maximum_fresh_gap,
        allowed_fresh_gap=allowed_fresh_gap,
    )
    with state.lock:
        samples = list(state.samples)
        all_events = list(state.events)
        all_markers = list(state.markers)
        raw_manifest = list(state.raw_manifest)
    inference_values = [
        item["inference_ms"] for item in samples if item["inference_ms"] is not None
    ]
    cpu_values = [item["cpu_percent_one_core_100"] for item in samples]
    rss_values = [item["rss_mib"] for item in samples]

    def metrics(values: list[float]) -> dict[str, float | None]:
        ordered = sorted(values)
        return {
            "p50": round(statistics.median(ordered), 3) if ordered else None,
            "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)
            if ordered
            else None,
            "max": round(max(ordered), 3) if ordered else None,
        }

    summary = state.public() | {
        "finished_at": _utcnow(),
        "elapsed_seconds": round(elapsed, 3),
        "valid_continuous_seconds": round(continuous, 3),
        "maximum_fresh_gap_seconds": (round(maximum_fresh_gap, 3) if coverage_points else None),
        "allowed_fresh_gap_seconds": allowed_fresh_gap,
        "passed_stability": passed,
        "result_reason": reason,
        "platform": platform.platform(),
        "camera_index": args.camera,
        "requested_size": [width, height],
        "actual_size": [source.actual_width, source.actual_height],
        "observation_size": [source.observation_width, source.observation_height],
        "cup_scale_recheck": config.cup_scale_recheck,
        "observation_region": source.observation_region,
        "backend": source.backend_name,
        "cloud_or_agent_calls": 0,
        "worker_stop_failed": stop_failed,
        "accuracy_90_percent": "未自动判定；须按实际场景与人工标记逐项核对",
        "inference_ms": metrics(inference_values),
        "cpu_percent_one_core_100": metrics(cpu_values),
        "rss_mib": metrics(rss_values),
        "events": all_events,
        "markers": all_markers,
        "raw_marker_manifest": raw_manifest,
        "samples": samples,
    }
    _atomic_json(output / "summary.json", summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "passed_stability",
                    "result_reason",
                    "elapsed_seconds",
                    "valid_continuous_seconds",
                    "fresh_observations",
                    "fault_observations",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="必须是尚不存在的新目录")
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, help="默认使用 VAA_CAMERA_WIDTH")
    parser.add_argument("--height", type=int, help="默认使用 VAA_CAMERA_HEIGHT")
    parser.add_argument(
        "--region",
        type=float,
        nargs=4,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="归一化观察范围；默认使用配置，0 0 1 1 表示全画面",
    )
    parser.add_argument("--backend", type=int)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if (
        args.duration <= 0
        or args.camera < 0
        or (args.width is not None and args.width <= 0)
        or (args.height is not None and args.height <= 0)
        or not 0 <= args.port <= 65535
    ):
        parser.error("时长、摄像头编号、尺寸或端口无效")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
