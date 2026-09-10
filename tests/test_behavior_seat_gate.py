import pytest

from scripts.behavior_seat_gate import SeatPersonGate, parse_roi
from scripts.behavior_visibility import PersonCandidate

SIZE = (100, 100)
TARGET = PersonCandidate(0.9, (30, 30, 70, 90))


def test_weak_box_cannot_establish_but_can_continue_for_bounded_time():
    gate = SeatPersonGate()
    weak = PersonCandidate(0.3, TARGET.bbox)
    assert gate.observe(0.0, [weak], SIZE)["reason"] == "zero_strong_in_roi"
    assert gate.observe(1.0, [TARGET], SIZE)["reason"] == "needs_previous_frame"
    for timestamp in (1.1, 1.2, 1.3):
        assert gate.observe(timestamp, [weak], SIZE)["person_track_supported"]
    result = gate.observe(1.4, [weak], SIZE)
    assert result["person_track_supported"]
    assert result["reason"] == "chosen_weak_continuation"
    assert gate.observe(1.401, [weak], SIZE)["reason"] == "weak_detection_timeout"


def test_outside_bystander_does_not_interrupt_unique_target():
    gate = SeatPersonGate()
    outside = PersonCandidate(0.99, (0, 0, 10, 90))
    first = gate.observe(0.0, [TARGET, outside], SIZE)
    assert not first["person_track_supported"]
    assert first["reason"] == "needs_previous_frame"
    assert first["strong_count"] == 1
    result = gate.observe(0.1, [TARGET, outside], SIZE)
    assert result["person_track_supported"]
    assert result["in_roi_count"] == 1
    assert len(result["all_candidates"]) == 2


def test_ambiguous_spatial_matches_are_rejected_and_reset():
    gate = SeatPersonGate()
    gate.observe(0.0, [TARGET], SIZE)
    nearby = PersonCandidate(0.7, (32, 32, 72, 92))
    assert gate.observe(0.1, [TARGET, nearby], SIZE)["reason"] == "multiple_spatial_matches"
    result = gate.observe(0.2, [PersonCandidate(0.3, TARGET.bbox)], SIZE)
    assert result["reason"] == "zero_strong_in_roi"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"candidates": [], "frame_size": SIZE}, "zero_eligible_in_roi"),
        ({"candidates": [TARGET], "frame_size": (200, 100)}, "frame_size_changed"),
        ({"candidates": [TARGET], "frame_size": SIZE, "valid_frame": False}, "invalid_frame"),
    ],
)
def test_faults_reset_track(kwargs, reason):
    gate = SeatPersonGate()
    gate.observe(1.0, [TARGET], SIZE)
    assert gate.observe(1.1, **kwargs)["reason"] == reason
    result = gate.observe(1.2, [PersonCandidate(0.3, TARGET.bbox)], SIZE)
    assert not result["person_track_supported"]


def test_spatial_mismatch_and_reverse_time_reset():
    gate = SeatPersonGate()
    gate.observe(1.0, [TARGET], SIZE)
    far = PersonCandidate(0.9, (75, 30, 95, 90))
    assert gate.observe(1.1, [far], SIZE)["reason"] == "spatial_mismatch"
    gate.observe(2.0, [TARGET], SIZE)
    assert gate.observe(2.0, [TARGET], SIZE)["reason"] == "timestamp_not_increasing"


def test_timestamp_gap_resets_before_weak_continuation():
    gate = SeatPersonGate()
    gate.observe(0.0, [TARGET], SIZE)
    result = gate.observe(0.251, [PersonCandidate(0.3, TARGET.bbox)], SIZE)
    assert result["reason"] == "timestamp_gap"
    assert not result["person_track_supported"]


def test_roi_validation_and_parsing():
    assert parse_roi(".2,.2,.95,1") == (0.2, 0.2, 0.95, 1.0)
    with pytest.raises(ValueError):
        SeatPersonGate((0.2, 0.2, 1.1, 1.0))
