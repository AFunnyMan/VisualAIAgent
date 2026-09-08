from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from visual_ai_agent.models import SceneObservation

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "camera_acceptance.py"
SPEC = importlib.util.spec_from_file_location("camera_acceptance", SCRIPT)
assert SPEC and SPEC.loader
camera_acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = camera_acceptance
SPEC.loader.exec_module(camera_acceptance)


def _observation(status: str, fresh: bool) -> SceneObservation:
    return SceneObservation(
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        monotonic_at=123.0,
        status=status,
        fresh=fresh,
        source="test",
    )


def test_dedup_preserves_same_frame_status_transition_but_drops_exact_repeat() -> None:
    running = _observation("running", True)
    stale = _observation("stale", False)
    keys = {camera_acceptance.observation_key(running)}
    assert camera_acceptance.observation_key(running) in keys
    assert camera_acceptance.observation_key(stale) not in keys


def test_all_nonfresh_statuses_before_first_frame_are_startup_statuses() -> None:
    for status in ("stopped", "paused", "stale", "disconnected", "error"):
        assert camera_acceptance.observation_bucket(_observation(status, False), False) == "startup"
    assert camera_acceptance.observation_bucket(_observation("stale", False), True) == "fault"


def test_recent_input_detector_keeps_only_defensive_latest_raw_frame() -> None:
    class FakeDetector:
        def detect(self, frame):
            frame[:] = 99
            return ["result"]

    wrapper = camera_acceptance.RecentInputDetector(FakeDetector())
    frame = np.full((2, 3, 3), 7, dtype=np.uint8)
    assert wrapper.detect(frame) == ["result"]
    saved, metadata = wrapper.snapshot()
    assert np.all(saved == 7)
    assert metadata["detector_sequence"] == 1
    saved[:] = 0
    assert np.all(wrapper.snapshot()[0] == 7)


def test_object_marker_saves_recent_completed_input_with_distinct_times(tmp_path: Path) -> None:
    state = camera_acceptance.ConsoleState(output=tmp_path, duration=10)
    state.latest_completed_raw = (
        np.full((3, 4, 3), 17, dtype=np.uint8),
        {
            "detector_sequence": 4,
            "detector_input_at": "2026-09-08T01:00:01+00:00",
            "detector_input_monotonic": 11.0,
            "observation_captured_at": "2026-09-08T01:00:00+00:00",
            "observation_monotonic_at": time.monotonic(),
        },
    )
    state.add_marker("empty_ready", None)
    assert not (tmp_path / "raw_markers").exists()
    state.add_marker("placed", "cup")
    manifest = json.loads((tmp_path / "raw_markers" / "manifest.json").read_text())
    assert len(manifest) == 1
    reference = manifest[0]
    assert reference["marker_server_time"] != reference["observation_captured_at"]
    assert reference["detector_sequence"] == 4
    assert (tmp_path / reference["raw_image"]).is_file()
    assert len(reference["png_sha256"]) == 64
    assert reference["raw_input_age_seconds"] <= state.max_raw_age_seconds
    assert "最近一次" in reference["qualification"]


def test_object_marker_rejects_expired_raw_input_but_keeps_marker(tmp_path: Path) -> None:
    state = camera_acceptance.ConsoleState(output=tmp_path, duration=10, max_raw_age_seconds=1)
    state.latest_completed_raw = (
        np.zeros((2, 2, 3), dtype=np.uint8),
        {"observation_monotonic_at": time.monotonic() - 2},
    )
    state.add_marker("removed", "bottle")
    assert len(state.markers) == 1
    assert "超过新鲜度" in state.markers[0]["raw_input_unavailable"]
    assert state.markers[0]["raw_input_age_seconds"] >= 2
    assert not (tmp_path / "raw_markers").exists()


def test_commands_require_csrf_known_action_category_and_no_extra_fields() -> None:
    validate = camera_acceptance.validate_command
    assert validate({"action": "placed", "category": "cup", "csrf": "token"}, "token") == (
        "placed",
        "cup",
    )
    assert validate({"action": "empty_ready", "csrf": "token"}, "token") == ("empty_ready", None)
    for bad in (
        {"action": "placed", "category": "person", "csrf": "token"},
        {"action": "erase", "csrf": "token"},
        {"action": "stop", "category": "cup", "csrf": "token"},
        {"action": "stop", "csrf": "wrong"},
        {"action": "stop", "csrf": "token", "extra": "x"},
        {"action": ["stop"], "csrf": "token"},
        {"action": {"name": "stop"}, "csrf": "token"},
        {"action": "stop", "csrf": "令牌"},
        {"action": "placed", "category": ["cup"], "csrf": "token"},
    ):
        with pytest.raises(ValueError):
            validate(bad, "token")


def test_http_input_rejects_cross_origin_unsupported_type_and_oversize_body() -> None:
    validate = camera_acceptance.validate_http_input
    valid = {
        "origin": "http://127.0.0.1:8765",
        "expected_origin": "http://127.0.0.1:8765",
        "content_type": "application/json",
        "content_length": "20",
    }
    assert validate(**valid) == 20
    with pytest.raises(PermissionError):
        validate(**(valid | {"origin": "http://evil.invalid"}))
    with pytest.raises(ValueError):
        validate(**(valid | {"content_type": "text/plain"}))
    with pytest.raises(ValueError):
        validate(**(valid | {"content_length": str(camera_acceptance.MAX_BODY_BYTES + 1)}))


@pytest.mark.parametrize(
    ("kwargs", "passed"),
    [
        (
            {
                "reached_deadline": True,
                "fresh_count": 10,
                "fault_count": 0,
                "stop_requested": False,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            True,
        ),
        (
            {
                "reached_deadline": False,
                "fresh_count": 10,
                "fault_count": 0,
                "stop_requested": False,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
        (
            {
                "reached_deadline": True,
                "fresh_count": 0,
                "fault_count": 0,
                "stop_requested": False,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
        (
            {
                "reached_deadline": True,
                "fresh_count": 10,
                "fault_count": 1,
                "stop_requested": False,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
        (
            {
                "reached_deadline": True,
                "fresh_count": 10,
                "fault_count": 0,
                "stop_requested": True,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
        (
            {
                "reached_deadline": True,
                "fresh_count": 10,
                "fault_count": 0,
                "stop_requested": False,
                "continuous_seconds": 9.9,
                "required_seconds": 10,
                "maximum_fresh_gap": 1,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
        (
            {
                "reached_deadline": True,
                "fresh_count": 10,
                "fault_count": 0,
                "stop_requested": False,
                "continuous_seconds": 10,
                "required_seconds": 10,
                "maximum_fresh_gap": 3,
                "allowed_fresh_gap": 2.5,
            },
            False,
        ),
    ],
)
def test_acceptance_never_passes_early_no_frame_fault_or_manual_stop(kwargs, passed) -> None:
    assert camera_acceptance.acceptance_result(**kwargs)[0] is passed


def test_all_markers_are_persisted_even_when_public_view_is_bounded(tmp_path: Path) -> None:
    state = camera_acceptance.ConsoleState(output=tmp_path, duration=10)
    for _ in range(61):
        state.add_marker("empty_ready", None)
    saved = __import__("json").loads((tmp_path / "markers.json").read_text())
    assert len(saved) == 61
    assert len(state.public()["markers"]) == 60
