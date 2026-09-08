"""One process-owned camera and a bounded, serial Agent worker, independent of the UI."""

import asyncio
import atexit
from concurrent.futures import Future
from dataclasses import replace
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from visual_ai_agent.agent import AgentService
from visual_ai_agent.config import Config
from visual_ai_agent.instance_lock import InstanceLock
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import SceneObservation, ToolResult, utcnow
from visual_ai_agent.vision import CameraSource, VisionWorker, YoloOnnxDetector
from visual_ai_agent.watches import WatchService


class ApplicationRuntime:
    def __init__(self, config: Config):
        self.config = config
        self._lock = RLock()
        self._camera_lock = Lock()
        self._instance = InstanceLock(config.data_dir / "app.lock")
        self._closed = False
        self._stop = Event()
        self._jobs: Queue = Queue(maxsize=64)
        self._thread: Thread | None = None
        self._vision = None
        self._detector = None
        self._active_camera_settings: (
            tuple[int, int, int, tuple[float, float, float, float] | None, float] | None
        ) = None
        self._last_view_key: (
            tuple[int, int, int, tuple[float, float, float, float] | None] | None
        ) = None
        self.last_error: str | None = None
        try:
            self.memory = MemoryStore(config.data_dir, max_gap_seconds=config.sample_interval * 2.5)
            self.watches = WatchService(self.memory)
            recovered = self.watches.recover()
            self.agent = AgentService(config, self.memory, self.watches)
            self.memory.cleanup()
        except Exception:
            self._instance.close()
            raise
        self._ensure_worker()
        for watch in recovered:
            if watch.status == "processing" and watch.event_id:
                event = self.memory.get_event(watch.event_id)
                if event:
                    try:
                        self._jobs.put_nowait(("event", (watch, event), None))
                    except Full:
                        self.watches.notify_user(
                            watch.watch_id,
                            event.event_id,
                            f"本地规则降级提醒（恢复队列已满）：{event.category} {event.kind}；"
                            f"确认时间 {event.confirmed_at.isoformat()}；"
                            f"证据 {event.evidence_id or '不可用'}。",
                            source="fallback",
                        )
        atexit.register(self.close)

    @property
    def closed(self):
        return self._closed

    @property
    def camera_running(self):
        return bool(self._vision and self._vision.running)

    def _ensure_worker(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("应用已关闭")
            if self._thread is None:
                self._thread = Thread(target=self._run_jobs, name="vaa-agent", daemon=True)
                self._thread.start()

    def submit_user(self, message: str, request_id: str | None = None) -> Future:
        if not message.strip() or len(message) > 8000:
            raise ValueError("请输入 1–8000 字的请求。")
        self._ensure_worker()
        future: Future = Future()
        try:
            self._jobs.put_nowait(("user", (message, request_id or uuid4().hex), future))
        except Full as exc:
            raise RuntimeError("请求队列已满，请稍后再试。") from exc
        return future

    def ingest(self, observation: SceneObservation, jpeg: bytes | None):
        """Called by the inference worker; never blocks on a model request."""
        try:
            events = self.memory.ingest(observation, jpeg)
            for event in events:
                for watch in self.watches.match_event(event):
                    self._ensure_worker()
                    try:
                        self._jobs.put_nowait(("event", (watch, event), None))
                    except Full:
                        self.watches.notify_user(
                            watch.watch_id,
                            event.event_id,
                            f"本地规则降级提醒（请求队列已满）：{event.category} "
                            f"{event.kind}；确认时间 {event.confirmed_at.isoformat()}；"
                            f"证据 {event.evidence_id or '不可用'}。",
                            source="fallback",
                        )
        except Exception as exc:
            # Persisted failure must be visible, but raw exceptions can contain private paths.
            self.last_error = f"视觉事实保存失败（{type(exc).__name__}），请检查本地磁盘。"

    def _run_jobs(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        next_cleanup = monotonic() + 3600
        try:
            while not self._stop.is_set():
                if monotonic() >= next_cleanup:
                    try:
                        self.memory.cleanup()
                        self.watches.list_watches()
                    except Exception:
                        self.last_error = "本地数据清理失败，请检查磁盘空间。"
                    next_cleanup = monotonic() + 3600
                try:
                    kind, arguments, future = self._jobs.get(timeout=0.25)
                except Empty:
                    continue
                try:
                    if future is not None and not future.set_running_or_notify_cancel():
                        continue
                    coroutine = (
                        self.agent.run_user(*arguments)
                        if kind == "user"
                        else self.agent.run_event(*arguments)
                    )
                    result = loop.run_until_complete(coroutine)
                    if future is not None:
                        future.set_result(result)
                except Exception as exc:
                    self.last_error = f"Agent 工作异常（{type(exc).__name__}）。"
                    if future is not None:
                        future.set_exception(RuntimeError(self.last_error))
                    elif kind == "event":
                        watch, event = arguments
                        try:
                            self.watches.notify_user(
                                watch.watch_id,
                                event.event_id,
                                f"本地规则降级提醒：{event.category} {event.kind}；"
                                f"确认时间 {event.confirmed_at.isoformat()}；"
                                f"证据 {event.evidence_id or '不可用'}。",
                                source="fallback",
                            )
                        except Exception:
                            self.last_error = "Agent 与本地提醒写入失败，请检查本地数据目录。"
                finally:
                    self._jobs.task_done()
        finally:
            # Queued user futures finish visibly; matched events remain recoverable in SQLite.
            while True:
                try:
                    _, _, future = self._jobs.get_nowait()
                except Empty:
                    break
                if future is not None and not future.done():
                    future.set_exception(RuntimeError("应用已停止，请重新提交请求。"))
                self._jobs.task_done()
            if hasattr(self.agent, "close"):
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(
                            self.agent.close(),
                            timeout=self.config.api_timeout_seconds,
                        )
                    )
                except Exception:
                    self.last_error = "Agent 连接关闭未完成。"
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def start_camera(
        self,
        camera_index: int | None = None,
        interval: float | None = None,
        *,
        resolution: tuple[int, int] | None = None,
        observation_region: tuple[float, float, float, float] | None = None,
    ):
        with self._camera_lock:
            return self._start_camera(camera_index, interval, resolution, observation_region)

    def _start_camera(self, camera_index, interval, resolution, observation_region):
        with self._lock:
            if self._closed:
                return ToolResult(ok=False, error="应用已关闭")
            try:
                width, height = resolution or (self.config.camera_width, self.config.camera_height)
                config = replace(
                    self.config,
                    camera_index=self.config.camera_index if camera_index is None else camera_index,
                    sample_interval=self.config.sample_interval if interval is None else interval,
                    camera_width=width,
                    camera_height=height,
                    observation_region=(
                        self.config.observation_region
                        if observation_region is None
                        else observation_region
                    ),
                )
                settings = (
                    config.camera_index,
                    config.camera_width,
                    config.camera_height,
                    config.observation_region,
                    config.sample_interval,
                )
                if self._vision is not None:
                    if self._vision.running:
                        if (
                            self._active_camera_settings is None
                            or settings == self._active_camera_settings
                        ):
                            return ToolResult(
                                ok=True,
                                data={"status": "already_running", "settings": settings},
                            )
                        return ToolResult(
                            ok=False,
                            error="观察设置已更改，请先停止观察，再重新开始。",
                        )
                    return ToolResult(ok=False, error="上一次摄像头工作线程尚未完成停止清理。")
                if self._detector is None:
                    self._detector = YoloOnnxDetector(
                        config.model_path,
                        confidence=config.confidence,
                        expected_sha256=config.model_sha256 or None,
                    )
                self.memory.set_max_gap_seconds(config.sample_interval * 2.5)
                view_key = settings[:4]
                if self._last_view_key is not None and view_key != self._last_view_key:
                    self.memory.reset_event_baseline()
                source = CameraSource(
                    device_index=config.camera_index,
                    width=config.camera_width,
                    height=config.camera_height,
                    observation_region=config.observation_region,
                )
                self._vision = VisionWorker(
                    self._detector,
                    source,
                    self.ingest,
                    inference_interval=config.sample_interval,
                    stale_after=config.sample_interval * 2.5,
                )
                self._vision.start()
                self._active_camera_settings = settings
                self._last_view_key = view_key
                self.last_error = None
                return ToolResult(ok=True, data={"status": "starting", "settings": settings})
            except Exception as exc:
                self.last_error = (
                    f"启动失败（{type(exc).__name__}）。请检查模型校验、摄像头权限和设备。"
                )
                return ToolResult(ok=False, error=self.last_error)

    def stop_camera(self):
        with self._camera_lock:
            return self._stop_camera()

    def _stop_camera(self):
        # Do not hold the runtime lock while joining the callback thread.
        with self._lock:
            worker = self._vision
        if worker:
            worker.stop()
            if worker.running:
                self.last_error = "摄像头工作线程尚未停止，请退出进程后检查设备。"
                return ToolResult(ok=False, error=self.last_error)
            observation, _ = worker.snapshot()
            if observation is not None and observation.status == "error":
                self.last_error = observation.error or "摄像头资源释放失败，请退出应用后重试。"
                return ToolResult(ok=False, error=self.last_error)
        with self._lock:
            # Keep the old worker reachable until its threads have joined and the
            # stopped fact is durable, so start_camera cannot replace it midway.
            self.memory.ingest(
                SceneObservation(
                    observed_at=utcnow(),
                    monotonic_at=0,
                    status="stopped",
                    fresh=False,
                ),
                None,
            )
            if self._vision is worker:
                self._vision = None
                self._active_camera_settings = None
        return ToolResult(ok=True, data={"status": "stopped"})

    def snapshot(self) -> tuple[SceneObservation | None, bytes | None]:
        if self._vision:
            return self._vision.snapshot()
        return None, None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.stop_camera()
        finally:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=self.config.api_timeout_seconds * 3 + 5)
            if (self._thread and self._thread.is_alive()) or self._vision is not None:
                self.last_error = self.last_error or (
                    "工作线程尚未停止；保留实例锁，避免重复访问设备。"
                )
            else:
                self._instance.close()
                atexit.unregister(self.close)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "camera_running": self.camera_running,
            "queued_requests": self._jobs.qsize(),
            "agent_configured": self.config.agent_connected,
            "error": self.last_error,
            "camera_settings": self._active_camera_settings,
        }
