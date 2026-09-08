from dataclasses import dataclass
from datetime import timedelta
from threading import Event

import pytest

from visual_ai_agent.config import Config
from visual_ai_agent.models import Detection, SceneObservation, utcnow
from visual_ai_agent.runtime import ApplicationRuntime


@dataclass
class SlowAgent:
    entered: Event
    release: Event
    calls: int = 0

    async def run_user(self, message, request_id):
        import asyncio

        self.calls += 1
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return "done"


def test_agent_wait_does_not_block_local_observation(tmp_path):
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    slow = SlowAgent(Event(), Event())
    runtime.agent = slow
    try:
        future = runtime.submit_user("查询")
        assert slow.entered.wait(2)
        for i in range(3):
            runtime.ingest(
                SceneObservation(
                    observed_at=utcnow() + timedelta(milliseconds=i),
                    monotonic_at=i,
                    status="running",
                    fresh=True,
                    detections=[
                        Detection(
                            category="cup", confidence=0.9, bbox=(1, 1, 20, 20), region="left"
                        )
                    ],
                    source="test",
                ),
                None,
            )
        assert runtime.memory.find_object("cup").data["found"]
        assert not future.done()
        slow.release.set()
        assert future.result(timeout=3) == "done"
        assert slow.calls == 1
    finally:
        slow.release.set()
        runtime.close()
    assert not runtime._thread.is_alive()


def test_shutdown_is_idempotent_and_releases_instance(tmp_path):
    first = ApplicationRuntime(Config(data_dir=tmp_path))
    with pytest.raises(RuntimeError, match="已有应用实例"):
        ApplicationRuntime(Config(data_dir=tmp_path))
    first.close()
    first.close()
    second = ApplicationRuntime(Config(data_dir=tmp_path))
    second.close()


def test_no_credentials_user_request_is_visible_and_zero_calls(tmp_path):
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    try:
        result = runtime.submit_user("杯子在哪").result(timeout=3)
        assert result.status == "not_connected"
        assert result.request_attempts == 0
        assert result.total_tokens is None
    finally:
        runtime.close()


def test_restarting_requeues_claimed_event_once_without_new_observation(tmp_path):
    from time import monotonic, sleep

    from visual_ai_agent.memory import MemoryStore
    from visual_ai_agent.watches import WatchService

    memory = MemoryStore(tmp_path)
    watches = WatchService(memory)
    watches.create_watch("cup", "appeared", request_id="recovered-watch")
    events = []
    for i in range(3):
        events = memory.ingest(
            SceneObservation(
                observed_at=utcnow(),
                monotonic_at=i,
                status="running",
                fresh=True,
                detections=[
                    Detection(category="cup", confidence=0.9, bbox=(1, 1, 20, 20), region="left")
                ],
                source="test",
            )
        )
    assert len(watches.match_event(events[0])) == 1
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    try:
        deadline = monotonic() + 3
        while not runtime.watches.list_notifications().data["notifications"]:
            assert monotonic() < deadline
            sleep(0.01)
        notices = runtime.watches.list_notifications().data["notifications"]
        assert len(notices) == 1
        assert notices[0]["source"] == "fallback"
    finally:
        runtime.close()
    second = ApplicationRuntime(Config(data_dir=tmp_path))
    try:
        assert second._jobs.empty()
        assert len(second.watches.list_notifications().data["notifications"]) == 1
    finally:
        second.close()


def test_repeated_start_reuses_model_and_camera_worker(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    constructed = []

    class FakeWorker:
        running = False

        def __init__(self, *args, **kwargs):
            constructed.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    models = []

    def detector(*args, **kwargs):
        models.append(kwargs)
        return object()

    monkeypatch.setattr(module, "YoloOnnxDetector", detector)
    monkeypatch.setattr(module, "VisionWorker", FakeWorker)
    runtime = ApplicationRuntime(Config(data_dir=tmp_path, model_sha256="test-sha"))
    try:
        assert runtime.start_camera().ok
        assert runtime.start_camera().data["status"] == "already_running"
        assert len(constructed) == 1
        assert len(models) == 1
        assert models[0]["expected_sha256"] == "test-sha"
        runtime.stop_camera()
        assert runtime.start_camera(interval=2).ok
        assert len(constructed) == 2
        assert len(models) == 1
        assert runtime.memory.max_gap_seconds == 5
    finally:
        runtime.close()


def test_stop_timeout_is_visible_and_does_not_allow_replacement(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    constructed = []

    class ReplacementWorker:
        def __init__(self, *_args, **_kwargs):
            constructed.append(self)

        running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "VisionWorker", ReplacementWorker)

    class BlockedWorker:
        running = True
        stopped_cleanly = False
        observation = None

        def stop(self):
            if self.running:
                self.observation = SceneObservation(
                    observed_at=utcnow(),
                    monotonic_at=0,
                    status="error",
                    fresh=False,
                    error="worker did not stop",
                )
                runtime.memory.ingest(self.observation)
            else:
                self.stopped_cleanly = True

        def snapshot(self):
            if self.stopped_cleanly:
                return None, None
            return self.observation, None

    worker = BlockedWorker()
    runtime._vision = worker
    try:
        result = runtime.stop_camera()
        assert not result.ok
        assert "尚未停止" in result.error
        assert runtime.memory.get_current_scene().data["effective_status"] == "error"
        assert runtime.start_camera().data["status"] == "already_running"

        worker.running = False
        result = runtime.start_camera()
        assert not result.ok
        assert "停止清理" in result.error
        assert runtime._vision is worker
        assert not constructed

        assert runtime.stop_camera().ok
        assert runtime._vision is None
        assert runtime.start_camera().ok
        assert len(constructed) == 1
    finally:
        worker.running = False
        runtime.close()


def test_source_close_failure_is_not_overwritten_or_restarted(tmp_path):
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    close_error = SceneObservation(
        observed_at=utcnow(),
        monotonic_at=0,
        status="error",
        fresh=False,
        error="Source close failed: synthetic release failure",
    )

    class CloseFailingWorker:
        running = True

        def stop(self):
            self.running = False
            runtime.ingest(close_error, None)

        def snapshot(self):
            return close_error, None

    worker = CloseFailingWorker()
    runtime._vision = worker
    try:
        result = runtime.stop_camera()
        assert not result.ok
        assert result.error == close_error.error
        assert runtime.last_error == close_error.error
        assert runtime._vision is worker
        assert runtime.memory.get_current_scene().data["effective_status"] == "error"

        restart = runtime.start_camera()
        assert not restart.ok
        assert "停止清理" in restart.error
        assert runtime._vision is worker

        runtime.close()
        assert runtime.last_error == close_error.error
        with pytest.raises(RuntimeError, match="已有应用实例"):
            ApplicationRuntime(Config(data_dir=tmp_path))
    finally:
        runtime._instance.close()
