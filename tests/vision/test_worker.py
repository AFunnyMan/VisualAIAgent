from __future__ import annotations

import threading
import time

import numpy as np

from visual_ai_agent.models import Detection, SceneObservation
from visual_ai_agent.vision import VisionWorker


class ContinuousSource:
    source_name = "test"

    def __init__(self) -> None:
        self.status = "stopped"
        self.last_error: str | None = None
        self.read_count = 0

    def open(self) -> None:
        self.status = "running"

    def read(self) -> np.ndarray:
        time.sleep(0.002)
        self.read_count += 1
        return np.full((30, 30, 3), self.read_count % 255, dtype=np.uint8)

    def close(self) -> None:
        self.status = "stopped"


class RecordingDetector:
    def __init__(self) -> None:
        self.frame_values: list[int] = []

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        self.frame_values.append(int(frame_bgr[0, 0, 0]))
        time.sleep(0.02)
        return []


class DetectingDetector:
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.calls = 0

    def detect(self, _frame_bgr: np.ndarray) -> list[Detection]:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return [
            Detection(
                category="cup",
                confidence=0.9,
                bbox=(1, 1, 20, 20),
                region="left",
            )
        ]


class OneFrameReplay:
    source_name = "replay"

    def __init__(self) -> None:
        self.status = "stopped"
        self.last_error: str | None = None
        self.sent = False

    def open(self) -> None:
        self.status = "running"
        self.sent = False

    def read(self) -> np.ndarray | None:
        if not self.sent:
            self.sent = True
            return np.zeros((30, 30, 3), dtype=np.uint8)
        self.status = "stopped"
        return None

    def close(self) -> None:
        self.status = "stopped"


def test_worker_uses_latest_frame_without_backlog_and_stops() -> None:
    source = ContinuousSource()
    detector = RecordingDetector()
    observations: list[SceneObservation] = []
    got_fresh = threading.Event()

    def callback(observation: SceneObservation, _jpeg: bytes | None) -> None:
        observations.append(observation)
        if observation.fresh:
            got_fresh.set()

    worker = VisionWorker(detector, source, callback, inference_interval=0.01, stale_after=0.1)
    worker.start()
    assert got_fresh.wait(1)
    time.sleep(0.12)
    worker.stop()

    assert len(detector.frame_values) >= 2
    assert len(detector.frame_values) < source.read_count
    assert any(
        newer - older > 1
        for older, newer in zip(detector.frame_values, detector.frame_values[1:], strict=False)
    )
    assert observations[-1].status == "stopped"
    assert observations[-1].fresh is False
    assert source.status == "stopped"
    assert not worker.running


def test_slow_inference_cannot_publish_an_expired_frame_as_fresh() -> None:
    source = ContinuousSource()
    detector = DetectingDetector(delay=0.08)
    observations: list[SceneObservation] = []
    got_expired = threading.Event()

    def callback(observation: SceneObservation, _jpeg: bytes | None) -> None:
        observations.append(observation)
        if observation.error == "Inference completed after the frame expired":
            got_expired.set()

    worker = VisionWorker(detector, source, callback, inference_interval=0.01, stale_after=0.03)
    worker.start()
    assert got_expired.wait(1)
    worker.stop()

    expired = next(
        observation
        for observation in observations
        if observation.error == "Inference completed after the frame expired"
    )
    assert expired.status == "stale"
    assert expired.fresh is False
    assert expired.detections == []
    assert expired.inference_ms is not None and expired.inference_ms >= 70
    assert not any(observation.fresh for observation in observations)


def test_replay_eof_processes_last_frame_once_then_marks_it_stale() -> None:
    source = OneFrameReplay()
    detector = DetectingDetector()
    observations: list[SceneObservation] = []
    got_stale = threading.Event()

    def callback(observation: SceneObservation, _jpeg: bytes | None) -> None:
        observations.append(observation)
        if observation.status == "stale":
            got_stale.set()

    worker = VisionWorker(detector, source, callback, inference_interval=0.02, stale_after=0.5)
    worker.start()
    assert got_stale.wait(1)
    worker.stop()

    fresh = [observation for observation in observations if observation.fresh]
    stale = [observation for observation in observations if observation.status == "stale"]
    assert detector.calls == 1
    assert len(fresh) == 1
    assert fresh[0].detections[0].category == "cup"
    assert stale
    assert all(observation.detections == [] for observation in stale)


def test_worker_can_restart_after_a_complete_stop() -> None:
    source = ContinuousSource()
    detector = RecordingDetector()
    fresh_count = 0
    got_fresh = threading.Event()

    def callback(observation: SceneObservation, _jpeg: bytes | None) -> None:
        nonlocal fresh_count
        if observation.fresh:
            fresh_count += 1
            got_fresh.set()

    worker = VisionWorker(detector, source, callback, inference_interval=0.02)
    worker.start()
    assert got_fresh.wait(1)
    worker.stop()
    assert not worker.running

    got_fresh.clear()
    worker.start()
    assert got_fresh.wait(1)
    worker.stop()

    assert fresh_count >= 2
    assert not worker.running


class DisconnectingSource:
    source_name = "test"

    def __init__(self) -> None:
        self.status = "stopped"
        self.last_error: str | None = None
        self.sent = False

    def open(self) -> None:
        self.status = "running"

    def read(self) -> np.ndarray | None:
        if not self.sent:
            self.sent = True
            return np.zeros((30, 30, 3), dtype=np.uint8)
        self.status = "disconnected"
        self.last_error = "synthetic disconnect"
        time.sleep(0.002)
        return None

    def close(self) -> None:
        self.status = "stopped"


def test_disconnect_invalidates_buffered_pixels_without_inference() -> None:
    detector = DetectingDetector()
    observations: list[SceneObservation] = []
    got_disconnect = threading.Event()

    def callback(observation: SceneObservation, _jpeg: bytes | None) -> None:
        observations.append(observation)
        if observation.status == "disconnected":
            got_disconnect.set()

    worker = VisionWorker(
        detector,
        DisconnectingSource(),
        callback,
        inference_interval=0.02,
        stale_after=0.5,
    )
    worker.start()
    assert got_disconnect.wait(1)
    worker.stop()

    disconnected = [item for item in observations if item.status == "disconnected"]
    assert disconnected
    assert detector.calls == 0
    assert all(not item.fresh and item.detections == [] for item in disconnected)


class CloseUnblocksSource:
    source_name = "test"

    def __init__(self) -> None:
        self.status = "stopped"
        self.last_error: str | None = None
        self.read_started = threading.Event()
        self.unblock = threading.Event()

    def open(self) -> None:
        self.status = "running"
        self.unblock.clear()

    def read(self) -> None:
        self.read_started.set()
        self.unblock.wait()
        return None

    def close(self) -> None:
        self.status = "stopped"
        self.unblock.set()


def test_stop_releases_source_before_waiting_for_a_blocked_read() -> None:
    source = CloseUnblocksSource()
    worker = VisionWorker(RecordingDetector(), source, lambda *_args: None)
    worker.start()
    assert source.read_started.wait(1)

    started = time.monotonic()
    worker.stop(timeout=0.5)

    assert time.monotonic() - started < 0.3
    assert not worker.running
    observation, _ = worker.snapshot()
    assert observation is not None and observation.status == "stopped"


class IgnoringCloseSource(CloseUnblocksSource):
    def close(self) -> None:
        self.status = "stopped"


def test_stop_timeout_does_not_claim_stopped_or_allow_duplicate_start() -> None:
    source = IgnoringCloseSource()
    worker = VisionWorker(RecordingDetector(), source, lambda *_args: None)
    worker.start()
    assert source.read_started.wait(1)

    worker.stop(timeout=0.02)
    observation, _ = worker.snapshot()
    assert worker.running
    assert observation is not None and observation.status == "error"
    assert "did not stop" in (observation.error or "")

    original_thread = worker._capture_thread
    worker.start()
    assert worker._capture_thread is original_thread

    source.unblock.set()
    deadline = time.monotonic() + 1
    while worker.running and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop(timeout=0.5)
    assert not worker.running


class FailingSource:
    source_name = "test"
    status = "stopped"
    last_error: str | None = None

    def open(self) -> None:
        self.status = "error"
        self.last_error = "synthetic open failure"
        raise RuntimeError(self.last_error)

    def read(self) -> None:
        return None

    def close(self) -> None:
        self.status = "stopped"


def test_worker_reports_source_failure_as_nonfresh() -> None:
    observations: list[SceneObservation] = []
    worker = VisionWorker(
        RecordingDetector(),
        FailingSource(),
        lambda observation, _jpeg: observations.append(observation),
        inference_interval=0.01,
    )
    worker.start()
    deadline = time.monotonic() + 1
    while not observations and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert any(observation.status == "error" for observation in observations)
    error = next(observation for observation in observations if observation.status == "error")
    assert error.fresh is False
    assert error.detections == []
    assert error.error == "synthetic open failure"
