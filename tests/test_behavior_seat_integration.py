import pytest

from scripts.behavior_seat_gate import SeatPersonGate
from scripts.behavior_timeline_v2 import BehaviorTimelineV2
from scripts.behavior_visibility import PersonCandidate, PersonTrackGate


@pytest.mark.parametrize("fault", [False, True])
def test_matched_weak_detection_preserves_transition_but_fault_never_does(fault):
    counts = []
    for gate in (PersonTrackGate(), SeatPersonGate()):
        timeline = BehaviorTimelineV2(posture_confirm_seconds=0.3, max_gap=0.25)
        events = []
        for i in range(12):
            posture = "standing" if i < 6 else "unknown" if i == 6 else "seated"
            candidate = PersonCandidate(0.35 if i == 6 else 0.9, (25, 20, 80, 90))
            fresh = not (fault and i == 6)
            support = gate.observe(i / 10, [candidate], (100, 100), valid_frame=fresh)
            result = timeline.observe(
                i / 10,
                posture,
                "not_drinking",
                fresh=fresh,
                continuous_visible=support["person_track_supported"],
            )
            if i == 6:
                assert result["posture"] == "unknown"
                assert result["events"] == []
            events.extend(result["events"])
        counts.append(sum(e["kind"] == "sat_down" for e in events))
    assert counts == ([0, 0] if fault else [0, 1])
