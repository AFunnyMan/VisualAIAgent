from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from visual_ai_agent.vision import CameraSource, ReplaySource


class FakeCapture:
    def __init__(self, opened: bool, frames: list[np.ndarray] | None = None) -> None:
        self.opened = opened
        self.frames = list(frames or [])
        self.released = False
        self.position_resets = 0

    def isOpened(self) -> bool:
        return self.opened

    def release(self) -> None:
        self.released = True
        self.opened = False

    def set(self, prop: int, _value: float) -> bool:
        if prop == 1:
            self.position_resets += 1
        return True

    def get(self, prop: int) -> float:
        return {3: 640, 4: 480, 5: 30}.get(prop, 0)

    def getBackendName(self) -> str:
        return "FAKE"

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)


def test_camera_open_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = FakeCapture(False)
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource()

    with pytest.raises(RuntimeError, match="Unable to open"):
        source.open()
    assert source.status == "error"
    assert capture.released


@pytest.mark.parametrize("region", [None, (0.0, 0.0, 1.0, 1.0)])
def test_camera_records_actual_resolution_and_releases(
    monkeypatch: pytest.MonkeyPatch, region
) -> None:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    capture = FakeCapture(True, [frame])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource(observation_region=region)
    assert source.observation_region is None
    source.open()

    assert source.actual_width == 640
    assert source.actual_height == 480
    assert source.observation_width == 640
    assert source.observation_height == 480
    assert source.backend_name == "FAKE"
    assert source.read() is frame
    source.close()
    assert capture.released
    assert source.status == "stopped"


@pytest.mark.parametrize(
    "region",
    [
        (float("nan"), 0.0, 1.0, 1.0),
        (0.0, 0.0, float("inf"), 1.0),
        (-0.1, 0.0, 1.0, 1.0),
        (0.0, 0.0, 1.1, 1.0),
        (0.8, 0.0, 0.2, 1.0),
        (0.0, 0.7, 1.0, 0.7),
    ],
)
def test_camera_rejects_invalid_observation_region(
    region: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError, match="observation_region"):
        CameraSource(observation_region=region)


def test_camera_crops_normalized_region_and_copies_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.arange(8 * 10 * 3, dtype=np.uint8).reshape(8, 10, 3)
    capture = FakeCapture(True, [frame])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource(observation_region=(0.21, 0.24, 0.79, 0.76))
    source.open()

    cropped = source.read()

    assert cropped is not None
    np.testing.assert_array_equal(cropped, frame[1:7, 2:8])
    assert cropped.shape == (6, 6, 3)
    assert source.observation_width == 6
    assert source.observation_height == 6
    assert not np.shares_memory(cropped, frame)


def test_camera_region_scales_with_each_input_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = np.zeros((8, 10, 3), dtype=np.uint8)
    second = np.zeros((20, 30, 3), dtype=np.uint8)
    capture = FakeCapture(True, [first, second])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource(observation_region=(0.2, 0.25, 0.8, 0.75))
    source.open()

    assert source.read().shape == (4, 6, 3)  # type: ignore[union-attr]
    assert source.read().shape == (10, 18, 3)  # type: ignore[union-attr]
    assert (source.observation_width, source.observation_height) == (18, 10)


def test_camera_read_failure_after_crop_does_not_return_old_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((8, 10, 3), dtype=np.uint8)
    capture = FakeCapture(True, [frame])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource(observation_region=(0.2, 0.25, 0.8, 0.75))
    source.open()

    assert source.read() is not None
    assert source.read() is None
    assert source.status == "disconnected"
    assert source.last_error == "Camera frame read failed"


def test_replay_eof_is_stopped_not_empty_scene(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((12, 16, 3), dtype=np.uint8)
    capture = FakeCapture(True, [frame])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = ReplaySource(Path("example.mp4"))
    source.open()

    assert source.read() is frame
    assert source.read() is None
    assert source.status == "stopped"
    assert source.last_error is None
