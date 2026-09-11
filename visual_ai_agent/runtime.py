"""One process-owned camera and a bounded, serial Agent worker, independent of the UI."""

import asyncio
import atexit
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from visual_ai_agent.agent import AgentService
from visual_ai_agent.behavior import (
    BehaviorDetector,
    BehaviorWorker,
    OnnxClassifier,
    load_behavior_manifest,
    sha256,
)
from visual_ai_agent.behavior_models import BehaviorObservation, LaptopObservation
from visual_ai_agent.behavior_store import BehaviorStore
from visual_ai_agent.config import Config
from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.instance_lock import InstanceLock
from visual_ai_agent.laptop import (
    LaptopWorker,
    detector_from_capability,
    load_laptop_capability_manifest,
)
from visual_ai_agent.laptop_store import LaptopStore
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import SceneObservation, ToolResult, utcnow
from visual_ai_agent.vision import (
    CameraSource,
    CupScaleRecheckDetector,
    SharedCamera,
    VisionWorker,
    YoloOnnxDetector,
)
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
        self._behavior_worker = None
        self._laptop_worker = None
        self._shared_camera = None
        self._detector = None
        self._active_camera_settings: (
            tuple[int, int, int, tuple[float, float, float, float] | None, float, bool] | None
        ) = None
        self._last_view_key: (
            tuple[int, int, int, tuple[float, float, float, float] | None, bool] | None
        ) = None
        self._active_behavior_settings = None
        self._active_laptop_settings = None
        self._active_scene_id: str | None = None
        self._active_behavior_model_version: str | None = None
        self._active_laptop_model_version: str | None = None
        self._active_laptop_presence_version: str | None = None
        self.behavior_error: str | None = None
        self.laptop_error: str | None = None
        self.last_error: str | None = None
        try:
            self.memory = MemoryStore(config.data_dir, max_gap_seconds=config.sample_interval * 2.5)
            self.watches = WatchService(self.memory)
            self.behavior = BehaviorStore(self.memory, config.timezone)
            self.laptop = LaptopStore(self.memory)
            self.rules = ContextRuleService(
                self.memory, self.behavior, config.timezone, laptop=self.laptop
            )
            recovered = self.watches.recover()
            recovered_rules = self.rules.recover()
            self.agent = AgentService(
                config,
                self.memory,
                self.watches,
                behavior=self.behavior,
                laptop=self.laptop,
                rules=self.rules,
            )
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
        for job in recovered_rules:
            try:
                self._jobs.put_nowait(("rule", (job,), None))
            except Full:
                self.rules.fallback(job)
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
            if self._active_scene_id:
                observation = observation.model_copy(update={"scene_id": self._active_scene_id})
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
                    if kind == "user":
                        coroutine = self.agent.run_user(*arguments)
                    elif kind == "event":
                        coroutine = self.agent.run_event(*arguments)
                    else:
                        coroutine = self.agent.run_rule(*arguments)
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
                    elif kind == "rule":
                        try:
                            self.rules.fallback(arguments[0])
                        except Exception:
                            self.last_error = "情境规则提醒写入失败，请检查本地数据目录。"
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
        cup_scale_recheck: bool | None = None,
        behavior_enabled: bool | None = None,
        behavior_posture_manifest=None,
        behavior_drinking_manifest=None,
        laptop_enabled: bool | None = None,
        laptop_capability_manifest=None,
    ):
        with self._camera_lock:
            return self._start_camera(
                camera_index,
                interval,
                resolution,
                observation_region,
                cup_scale_recheck,
                behavior_enabled,
                behavior_posture_manifest,
                behavior_drinking_manifest,
                laptop_enabled,
                laptop_capability_manifest,
            )

    def _start_camera(
        self,
        camera_index,
        interval,
        resolution,
        observation_region,
        cup_scale_recheck,
        behavior_enabled,
        behavior_posture_manifest,
        behavior_drinking_manifest,
        laptop_enabled,
        laptop_capability_manifest,
    ):
        with self._lock:
            if self._closed:
                return ToolResult(ok=False, error="应用已关闭")
            if self._behavior_worker is not None:
                return ToolResult(ok=False, error="上一次行为识别线程尚未完成停止清理。")
            if self._laptop_worker is not None:
                return ToolResult(ok=False, error="上一次笔记本识别线程尚未完成停止清理。")
            try:
                self.behavior_error = None
                self.laptop_error = None
                self._active_behavior_model_version = None
                self._active_laptop_model_version = None
                self._active_laptop_presence_version = None
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
                    cup_scale_recheck=(
                        self.config.cup_scale_recheck
                        if cup_scale_recheck is None
                        else cup_scale_recheck
                    ),
                    behavior_enabled=(
                        self.config.behavior_enabled
                        if behavior_enabled is None
                        else behavior_enabled
                    ),
                    behavior_posture_manifest=(
                        self.config.behavior_posture_manifest
                        if behavior_posture_manifest is None
                        else Path(behavior_posture_manifest)
                    ),
                    behavior_drinking_manifest=(
                        self.config.behavior_drinking_manifest
                        if behavior_drinking_manifest is None
                        else Path(behavior_drinking_manifest)
                    ),
                    laptop_enabled=(
                        self.config.laptop_enabled if laptop_enabled is None else laptop_enabled
                    ),
                    laptop_capability_manifest=(
                        self.config.laptop_capability_manifest
                        if laptop_capability_manifest is None
                        else Path(laptop_capability_manifest)
                    ),
                )
                settings = (
                    config.camera_index,
                    config.camera_width,
                    config.camera_height,
                    config.observation_region,
                    config.sample_interval,
                    config.cup_scale_recheck,
                )
                behavior_settings = (
                    config.behavior_enabled,
                    config.behavior_posture_manifest,
                    config.behavior_drinking_manifest,
                    config.behavior_person_model,
                    config.behavior_model_version,
                    config.behavior_scene_id,
                    config.behavior_seat_roi,
                )
                laptop_settings = (
                    config.laptop_enabled,
                    config.laptop_capability_manifest,
                    config.laptop_model_version,
                )
                if self._vision is not None:
                    if self._vision.running:
                        if self._active_camera_settings is None or (
                            settings == self._active_camera_settings
                            and behavior_settings == self._active_behavior_settings
                            and laptop_settings == self._active_laptop_settings
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
                view_key = (*settings[:4], settings[5])
                if self._last_view_key is not None and view_key != self._last_view_key:
                    self.memory.reset_event_baseline()
                source = CameraSource(
                    device_index=config.camera_index,
                    width=config.camera_width,
                    height=config.camera_height,
                    observation_region=config.observation_region,
                )
                scene_id = f"{config.behavior_scene_id}-{uuid4().hex}"
                self._active_scene_id = scene_id
                if config.behavior_enabled or config.laptop_enabled:
                    self._shared_camera = SharedCamera(source)
                    vision_source = self._shared_camera.subscribe()
                else:
                    vision_source = source
                active_detector = (
                    CupScaleRecheckDetector(self._detector)
                    if config.cup_scale_recheck
                    else self._detector
                )
                self._vision = VisionWorker(
                    active_detector,
                    vision_source,
                    self.ingest,
                    inference_interval=config.sample_interval,
                    stale_after=config.sample_interval * 2.5,
                )
                self._vision.start()
                self.last_error = None
                if config.behavior_enabled:
                    try:
                        assert config.behavior_posture_manifest is not None
                        assert config.behavior_drinking_manifest is not None
                        person_model = config.behavior_person_model or config.model_path
                        if person_model.resolve() != config.model_path.resolve():
                            raise ValueError(
                                "Independent behavior person models require a verified manifest"
                            )
                        posture_record = load_behavior_manifest(
                            config.behavior_posture_manifest, "posture"
                        )
                        drinking_record = load_behavior_manifest(
                            config.behavior_drinking_manifest, "drinking"
                        )
                        posture = OnnxClassifier(posture_record)
                        drinking = OnnxClassifier(drinking_record)
                        behavior_model_version = (
                            f"{config.behavior_model_version}:"
                            f"p={posture_record['onnx_sha256'][:12]}:"
                            f"d={drinking_record['onnx_sha256'][:12]}:"
                            f"person={sha256(person_model)[:12]}"
                        )
                        behavior_detector = BehaviorDetector(
                            posture,
                            drinking,
                            person_model,
                            seat_roi=config.behavior_seat_roi,
                            expected_person_sha256=config.model_sha256 or None,
                            retain_diagnostic_frame=False,
                        )
                        self._behavior_worker = BehaviorWorker(
                            behavior_detector,
                            self._shared_camera.subscribe(),
                            self.ingest_behavior,
                            model_version=behavior_model_version,
                            scene_id=scene_id,
                        )
                        self._behavior_worker.start()
                        self._active_behavior_model_version = behavior_model_version
                    except Exception as exc:
                        self._behavior_worker = None
                        self.behavior_error = (
                            f"行为识别未启动（{type(exc).__name__}），物品观察继续运行。"
                        )
                if config.laptop_enabled:
                    self.laptop.set_capability(
                        configured=True,
                        available=False,
                        reason="笔记本开合模型尚不能确认电脑仍在画面中且清晰可见。",
                    )
                    laptop_detector = None
                    candidate_worker = None
                    try:
                        if config.laptop_capability_manifest is None:
                            raise ValueError("laptop capability manifest is required")
                        capability = load_laptop_capability_manifest(
                            config.laptop_capability_manifest,
                            expected_scene_id=config.behavior_scene_id,
                            expected_source_size=(config.camera_width, config.camera_height),
                        )
                        laptop_detector, laptop_version, presence_version = (
                            detector_from_capability(capability)
                        )
                        assert self._shared_camera is not None
                        candidate_worker = LaptopWorker(
                            laptop_detector,
                            self._shared_camera.subscribe(),
                            self.ingest_laptop,
                            model_version=laptop_version,
                            presence_model_version=presence_version,
                            scene_id=scene_id,
                        )
                        candidate_worker.start()
                        self._laptop_worker = candidate_worker
                        self._active_laptop_model_version = laptop_version
                        self._active_laptop_presence_version = presence_version
                        self.laptop.set_capability(
                            configured=True,
                            available=True,
                            reason="实验能力已加载；模型仍按固定场景实验范围使用。",
                        )
                    except Exception as exc:
                        if candidate_worker is not None:
                            candidate_worker.stop()
                        elif laptop_detector is not None:
                            laptop_detector.close()
                        self._laptop_worker = None
                        self.laptop_error = (
                            f"笔记本开合识别未启动（{type(exc).__name__}）："
                            "配置的实验能力尚不能确认电脑仍在画面中且清晰可见；物品观察继续运行。"
                        )
                        self.laptop.set_capability(
                            configured=True,
                            available=False,
                            reason=self.laptop_error,
                        )
                else:
                    self.laptop.set_capability(
                        configured=False,
                        available=False,
                        reason="笔记本开合实验未启用。",
                    )
                self._active_camera_settings = settings
                self._active_behavior_settings = behavior_settings
                self._active_laptop_settings = laptop_settings
                self._last_view_key = view_key
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
            if self._laptop_worker:
                laptop_worker = self._laptop_worker
                laptop_source = getattr(laptop_worker.source, "source_name", "camera")
                laptop_worker.stop()
                if laptop_worker.running:
                    self.laptop_error = "笔记本识别工作线程尚未停止，请退出进程后检查。"
                else:
                    self._laptop_worker = None
                    if self._active_scene_id:
                        self.ingest_laptop(
                            LaptopObservation(
                                observed_at=utcnow(),
                                monotonic_at=max(0.0, monotonic()),
                                status="stopped",
                                fresh=False,
                                state="unknown",
                                model_version=(
                                    self._active_laptop_model_version
                                    or self.config.laptop_model_version
                                ),
                                presence_model_version=(
                                    self._active_laptop_presence_version or "presence-unavailable"
                                ),
                                scene_id=self._active_scene_id,
                                source=laptop_source,
                            )
                        )
            if self._behavior_worker:
                behavior_worker = self._behavior_worker
                behavior_source = getattr(behavior_worker.source, "source_name", "camera")
                behavior_worker.stop()
                if behavior_worker.running:
                    self.behavior_error = "行为识别工作线程尚未停止，请退出进程后检查。"
                else:
                    self._behavior_worker = None
                    if self._active_scene_id:
                        self.ingest_behavior(
                            BehaviorObservation(
                                observed_at=utcnow(),
                                monotonic_at=max(0.0, monotonic()),
                                status="stopped",
                                fresh=False,
                                posture="unknown",
                                drinking=None,
                                model_version=(
                                    self._active_behavior_model_version
                                    or self.config.behavior_model_version
                                ),
                                scene_id=self._active_scene_id,
                                source=behavior_source,
                            )
                        )
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
                    source=getattr(getattr(worker, "source", None), "source_name", "camera"),
                ),
                None,
            )
            if self._vision is worker:
                self._vision = None
                self._shared_camera = None
                self._active_camera_settings = None
                self._active_behavior_settings = None
                self._active_laptop_settings = None
                if self._behavior_worker is None and self._laptop_worker is None:
                    self._active_scene_id = None
                    self._active_behavior_model_version = None
                    self._active_laptop_model_version = None
                    self._active_laptop_presence_version = None
        if self._behavior_worker is not None:
            return ToolResult(ok=False, error=self.behavior_error)
        if self._laptop_worker is not None:
            return ToolResult(ok=False, error=self.laptop_error)
        return ToolResult(ok=True, data={"status": "stopped"})

    def snapshot(self) -> tuple[SceneObservation | None, bytes | None]:
        if self._vision:
            return self._vision.snapshot()
        return None, None

    def ingest_behavior(self, observation, jpeg: bytes | None = None) -> None:
        """Persist behavior and enqueue only newly claimed local rule jobs."""
        try:
            trigger_ingested_at = self.memory.clock()
            events = self.behavior.ingest(observation, jpeg)
            observation_payload = observation.model_dump(mode="python")
            observation_payload["trigger_ingested_at"] = trigger_ingested_at
            jobs = self.rules.process_observation(observation_payload)
            for event in events:
                event_payload = event.model_dump(mode="python")
                event_payload["trigger_ingested_at"] = trigger_ingested_at
                jobs.extend(self.rules.process_event(event_payload))
            for job in jobs:
                try:
                    self._jobs.put_nowait(("rule", (job,), None))
                except Full:
                    self.rules.fallback(job)
        except Exception as exc:
            self.behavior_error = f"行为事实保存失败（{type(exc).__name__}），物品观察继续运行。"

    def ingest_laptop(self, observation, jpeg: bytes | None = None) -> None:
        """Persist independent lid facts and enqueue only confirmed transition jobs."""
        try:
            trigger_ingested_at = self.memory.clock()
            events = self.laptop.ingest(observation, jpeg)
            if observation.status in {"error", "disconnected"}:
                self.laptop_error = f"笔记本识别当前不可用（{observation.status}）。"
            elif observation.status == "running" and observation.fresh:
                self.laptop_error = None
            for event in events:
                payload = event.model_dump(mode="python")
                payload["trigger_ingested_at"] = trigger_ingested_at
                for job in self.rules.process_event(payload):
                    try:
                        self._jobs.put_nowait(("rule", (job,), None))
                    except Full:
                        self.rules.fallback(job)
        except Exception as exc:
            self.laptop_error = (
                f"笔记本开合事实保存失败（{type(exc).__name__}），物品观察继续运行。"
            )

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
            if (
                (self._thread and self._thread.is_alive())
                or self._vision is not None
                or self._behavior_worker is not None
                or self._laptop_worker is not None
                or (self._shared_camera is not None and self._shared_camera.running)
            ):
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
            "behavior_enabled": (
                self._active_behavior_settings[0]
                if self._active_behavior_settings is not None
                else self.config.behavior_enabled
            ),
            "behavior_running": bool(self._behavior_worker and self._behavior_worker.running),
            "behavior_settings": self._active_behavior_settings,
            "behavior_error": self.behavior_error,
            "scene_id": self._active_scene_id,
            "behavior_model_version": self._active_behavior_model_version,
            "laptop_enabled": (
                self._active_laptop_settings[0]
                if self._active_laptop_settings is not None
                else self.config.laptop_enabled
            ),
            "laptop_running": bool(self._laptop_worker and self._laptop_worker.running),
            "laptop_available": self.laptop.current().data.get("available", False),
            "laptop_settings": self._active_laptop_settings,
            "laptop_error": self.laptop_error,
            "laptop_model_version": self._active_laptop_model_version,
            "laptop_presence_model_version": self._active_laptop_presence_version,
        }
