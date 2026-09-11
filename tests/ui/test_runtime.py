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


def test_camera_settings_are_forwarded_and_running_changes_are_rejected(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    sources = []

    class FakeSource:
        def __init__(self, **kwargs):
            sources.append(kwargs)

    class FakeWorker:
        running = False

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    monkeypatch.setattr(module, "CameraSource", FakeSource)
    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "VisionWorker", FakeWorker)
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    try:
        started = runtime.start_camera(
            2,
            2,
            resolution=(1280, 720),
            observation_region=(0.1, 0.2, 0.9, 0.8),
        )
        assert started.ok
        assert sources == [
            {
                "device_index": 2,
                "width": 1280,
                "height": 720,
                "observation_region": (0.1, 0.2, 0.9, 0.8),
            }
        ]
        assert (
            runtime.start_camera(
                2,
                2,
                resolution=(1280, 720),
                observation_region=(0.1, 0.2, 0.9, 0.8),
            ).data["status"]
            == "already_running"
        )
        changed = runtime.start_camera(2, 2, resolution=(1920, 1080))
        assert not changed.ok
        assert "先停止" in changed.error
        assert len(sources) == 1
    finally:
        runtime.close()


def test_cup_recheck_wraps_reused_detector_and_participates_in_settings(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    base_detector = object()
    wrappers = []
    workers = []

    class FakeWrapper:
        def __init__(self, detector):
            assert detector is base_detector
            wrappers.append(self)

    class FakeWorker:
        running = False

        def __init__(self, detector, *_args, **_kwargs):
            self.detector = detector
            workers.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    monkeypatch.setattr(module, "CameraSource", lambda **_kwargs: object())
    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: base_detector)
    monkeypatch.setattr(module, "CupScaleRecheckDetector", FakeWrapper)
    monkeypatch.setattr(module, "VisionWorker", FakeWorker)
    runtime = ApplicationRuntime(Config(data_dir=tmp_path, cup_scale_recheck=True))
    try:
        started = runtime.start_camera()
        assert started.ok
        assert started.data["settings"][-1] is True
        assert workers[-1].detector is wrappers[-1]
        assert runtime.start_camera().data["status"] == "already_running"
        assert len(wrappers) == 1

        changed = runtime.start_camera(cup_scale_recheck=False)
        assert not changed.ok
        assert "先停止" in changed.error
        assert runtime.stop_camera().ok
        assert runtime.start_camera(cup_scale_recheck=False).ok
        assert workers[-1].detector is base_detector
        assert len(wrappers) == 1
        assert runtime._detector is base_detector
    finally:
        runtime.close()


def test_view_change_resets_presence_without_creating_missing_event(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    class FakeWorker:
        running = False

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    monkeypatch.setattr(module, "CameraSource", lambda **_kwargs: object())
    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "VisionWorker", FakeWorker)
    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    now = utcnow()
    try:
        for second in range(3):
            runtime.memory.ingest(
                SceneObservation(
                    observed_at=now + timedelta(seconds=second),
                    monotonic_at=second,
                    status="running",
                    fresh=True,
                    detections=[
                        Detection(
                            category="cup", confidence=0.9, bbox=(1, 1, 20, 20), region="left"
                        )
                    ],
                    source="test",
                )
            )
        assert runtime.start_camera().ok
        assert runtime.stop_camera().ok
        assert runtime.start_camera(observation_region=(0.0, 0.0, 0.5, 1.0)).ok
        for second in range(10, 17):
            runtime.memory.ingest(
                SceneObservation(
                    observed_at=now + timedelta(seconds=second),
                    monotonic_at=second,
                    status="running",
                    fresh=True,
                    detections=[],
                    source="test",
                )
            )
        events = runtime.memory.search_events(
            "cup", now - timedelta(minutes=1), now + timedelta(minutes=1)
        ).data["events"]
        assert all(event["kind"] != "missing" for event in events)
        assert runtime.memory.find_object("cup").data["found"] is True
    finally:
        runtime.close()


def test_unaccepted_laptop_capability_stays_unavailable_while_objects_start(tmp_path, monkeypatch):
    import visual_ai_agent.runtime as module

    class FakeWorker:
        running = False

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    class FakeSource:
        source_name = "camera"
        status = "stopped"
        last_error = None

    monkeypatch.setattr(module, "CameraSource", lambda **_kwargs: FakeSource())
    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "VisionWorker", FakeWorker)
    runtime = ApplicationRuntime(Config(data_dir=tmp_path, laptop_enabled=True))
    try:
        result = runtime.start_camera()
        assert result.ok
        diagnostics = runtime.diagnostics()
        assert diagnostics["camera_running"] is True
        assert diagnostics["laptop_running"] is False
        assert diagnostics["laptop_available"] is False
        assert "电脑仍在画面中且清晰可见" in diagnostics["laptop_error"]
        current = runtime.laptop.current().data
        assert current["configured"] is True
        assert current["state"] == "unknown"
    finally:
        runtime.close()


def test_accepted_laptop_worker_uses_an_independent_subscription_to_shared_camera(
    tmp_path, monkeypatch
):
    import visual_ai_agent.runtime as module

    sources = []
    laptop_workers = []

    class FakeSource:
        source_name = "replay"
        status = "stopped"
        last_error = None

    class FakeVisionWorker:
        running = False

        def __init__(self, _detector, source, *_args, **_kwargs):
            self.source = source
            sources.append(source)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return None, None

    class FakeLaptopWorker:
        running = False

        def __init__(self, _detector, source, *_args, **kwargs):
            self.source = source
            self.model_version = kwargs["model_version"]
            self.presence_model_version = kwargs["presence_model_version"]
            laptop_workers.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    manifest = tmp_path / "accepted.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "CameraSource", lambda **_kwargs: FakeSource())
    monkeypatch.setattr(module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "VisionWorker", FakeVisionWorker)
    monkeypatch.setattr(module, "LaptopWorker", FakeLaptopWorker)
    monkeypatch.setattr(module, "load_laptop_capability_manifest", lambda _path, **_kwargs: {})
    monkeypatch.setattr(
        module,
        "detector_from_capability",
        lambda _record: (object(), "lid-accepted", "presence-accepted"),
    )
    runtime = ApplicationRuntime(
        Config(
            data_dir=tmp_path,
            laptop_enabled=True,
            laptop_capability_manifest=manifest,
        )
    )
    try:
        assert runtime.start_camera().ok
        assert len(sources) == 1 and len(laptop_workers) == 1
        assert sources[0] is not laptop_workers[0].source
        assert sources[0].camera is laptop_workers[0].source.camera
        assert runtime.diagnostics()["laptop_available"] is True
        assert runtime.stop_camera().ok
        laptop_current = runtime.laptop.current().data["observation"]
        assert laptop_current["status"] == "stopped"
        assert laptop_current["source"] == "replay"
        with runtime.memory._read_connection() as connection:
            row = connection.execute(
                "SELECT status,source FROM observations ORDER BY observation_id DESC LIMIT 1"
            ).fetchone()
        assert (row["status"], row["source"]) == ("stopped", "replay")
    finally:
        runtime.close()


def test_behavior_trigger_boundary_excludes_late_committed_old_object_frame(tmp_path, monkeypatch):
    """Precisely interleave a late object commit inside behavior persistence."""
    from visual_ai_agent.behavior_models import BehaviorEvent, BehaviorObservation

    runtime = ApplicationRuntime(Config(data_dir=tmp_path))
    base = utcnow()
    clock = [base]
    runtime.memory.clock = lambda: clock[0]
    runtime.rules.clock = lambda: clock[0]
    rule = runtime.rules.create_rule(
        "left_seat",
        request_id="boundary",
        message="检查手机",
        object_category="cell phone",
        region="right",
    ).data["rule"]
    detection = Detection(
        category="cell phone", confidence=0.9, bbox=(50, 1, 90, 40), region="right"
    )

    def put_object(offset):
        runtime.memory.ingest(
            SceneObservation(
                observed_at=base + timedelta(seconds=offset),
                monotonic_at=10 + offset,
                status="running",
                fresh=True,
                source="test",
                scene_id="same-scene",
                detections=[detection],
            )
        )

    try:
        clock[0] = base + timedelta(seconds=1)
        put_object(0.1)
        put_object(0.2)
        event = BehaviorEvent(
            kind="left_seat",
            observed_at=base + timedelta(seconds=0.8),
            confirmed_at=base + timedelta(seconds=0.8),
            model_version="test",
            scene_id="same-scene",
            source="test",
        )
        observation = BehaviorObservation(
            observed_at=event.confirmed_at,
            monotonic_at=10.8,
            status="running",
            fresh=True,
            posture="empty",
            events=[event],
            model_version="test",
            scene_id="same-scene",
            source="test",
        )

        def persist_then_late_commit(_observation, _jpeg):
            # Sample was captured before the trigger, but became available after its boundary.
            clock[0] = base + timedelta(seconds=1.1)
            put_object(0.3)
            return [event]

        monkeypatch.setattr(runtime.behavior, "ingest", persist_then_late_commit)
        seen = []
        for name in ("process_observation", "process_event"):
            original = getattr(runtime.rules, name)

            def capture(payload, operation=original):
                seen.append(payload["trigger_ingested_at"])
                return operation(payload)

            monkeypatch.setattr(runtime.rules, name, capture)
        runtime.ingest_behavior(observation)
        assert seen == [base + timedelta(seconds=1)] * 2
        assert not runtime.rules.get_job(rule["rule_id"], event.event_id).ok
        assert runtime.memory.list_agent_runs() == []
        assert runtime.last_error is None
    finally:
        runtime.close()
