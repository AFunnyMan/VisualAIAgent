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


def test_camera_records_actual_resolution_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    capture = FakeCapture(True, [frame])
    monkeypatch.setattr("visual_ai_agent.vision.cv2.VideoCapture", lambda *_args: capture)
    source = CameraSource()
    source.open()

    assert source.actual_width == 640
    assert source.actual_height == 480
    assert source.backend_name == "FAKE"
    assert source.read() is frame
    source.close()
    assert capture.released
    assert source.status == "stopped"


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
