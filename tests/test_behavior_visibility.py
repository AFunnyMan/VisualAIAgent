import math

import numpy as np
import pytest

from scripts.behavior_visibility import PersonCandidate, PersonTrackGate, _person_candidates

SIZE = (100, 100)
BOX = PersonCandidate(0.9, (10, 10, 80, 90))


def test_sustained_unique_person_requires_two_continuous_frames():
    gate = PersonTrackGate()
    assert not gate.observe(0.0, [BOX], SIZE)["person_track_supported"]
    result = gate.observe(0.1, [BOX], SIZE)
    assert result["person_track_supported"]
    assert result["person_candidate"] == {"confidence": 0.9, "bbox": [10, 10, 80, 90]}


def test_missing_frame_and_disconnect_reset_track():
    gate = PersonTrackGate()
    gate.observe(0.0, [BOX], SIZE)
    assert not gate.observe(0.1, [], SIZE)["person_track_supported"]
    assert not gate.observe(0.2, [BOX], SIZE)["person_track_supported"]
    assert not gate.observe(0.3, [BOX], SIZE, valid_frame=False)["person_track_supported"]
    assert not gate.observe(0.4, [BOX], SIZE)["person_track_supported"]


def test_multiple_strong_people_reset_track():
    gate = PersonTrackGate()
    gate.observe(0.0, [BOX], SIZE)
    other = PersonCandidate(0.8, (0, 0, 20, 40))
    assert gate.observe(0.1, [BOX, other], SIZE)["reason"] == "not_one_strong_person"
    assert not gate.observe(0.2, [BOX], SIZE)["person_track_supported"]


def test_box_jump_and_dimension_change_reset_track():
    gate = PersonTrackGate()
    gate.observe(0.0, [BOX], SIZE)
    jumped = PersonCandidate(0.9, (0, 0, 20, 20))
    assert gate.observe(0.1, [jumped], SIZE)["reason"] == "box_jump"
    assert not gate.observe(0.2, [jumped], SIZE)["person_track_supported"]
    assert gate.observe(0.3, [jumped], (120, 100))["reason"] == "frame_size_changed"
    assert not gate.observe(0.4, [jumped], (120, 100))["person_track_supported"]


def test_low_confidence_nonfinite_and_time_gap_reset_track():
    gate = PersonTrackGate()
    gate.observe(0.0, [BOX], SIZE)
    low = PersonCandidate(0.49, BOX.bbox)
    assert not gate.observe(0.1, [low], SIZE)["person_track_supported"]
    assert (
        gate.observe(0.2, [PersonCandidate(math.nan, BOX.bbox)], SIZE)["reason"]
        == "invalid_candidate"
    )
    gate.observe(0.3, [BOX], SIZE)
    assert gate.observe(0.7, [BOX], SIZE)["reason"] == "timestamp_gap"


def test_valid_frame_freshness_alone_never_supports_track():
    gate = PersonTrackGate()
    for timestamp in (0.0, 0.1, 0.2):
        assert not gate.observe(timestamp, [], SIZE, valid_frame=True)["person_track_supported"]


def test_detector_output_rejects_any_nonfinite_value_before_filtering():
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 299, 4] = np.nan
    with pytest.raises(ValueError, match="finite static"):
        _person_candidates(raw, transform=None)
