from scripts.behavior_events import BehaviorTimeline


def test_rise_requires_observed_standing_and_is_not_repeated():
    model = BehaviorTimeline()
    model.observe(0, "seated", "not_drinking")
    model.observe(0.5, "seated", "not_drinking")
    assert not model.observe(1, "standing", "not_drinking")["events"]
    assert model.observe(1.5, "standing", "not_drinking")["events"][0]["kind"] == "stood_up"
    assert not model.observe(2, "standing", "not_drinking")["events"]


def test_unknown_gap_cannot_become_rise_or_drinking():
    model = BehaviorTimeline()
    model.observe(0, "seated", "not_drinking")
    model.observe(0.5, "seated", "not_drinking")
    assert model.observe(1, "empty", "drinking", fresh=False)["posture"] == "unknown"
    model.observe(8, "standing", "drinking")
    assert not model.observe(8.5, "standing", "drinking")["events"]


def test_drinking_and_seated_are_simultaneous():
    model = BehaviorTimeline()
    model.observe(0, "seated", "not_drinking")
    model.observe(0.5, "seated", "not_drinking")
    model.observe(1, "seated", "drinking")
    result = model.observe(1.5, "seated", "drinking")
    assert result["posture"] == "seated"
    assert result["drinking"] == "drinking"
    assert result["events"] == [{"kind": "suspected_drink", "timestamp": 1.5}]


def test_seated_to_empty_does_not_invent_standing():
    model = BehaviorTimeline()
    model.observe(0, "seated", "not_drinking")
    model.observe(0.5, "seated", "not_drinking")
    model.observe(1, "empty", "not_drinking")
    assert model.observe(1.5, "empty", "not_drinking")["events"] == [
        {"kind": "left_seat", "timestamp": 1.5}
    ]


def test_empty_person_evidence_blocks_contradictory_drink():
    model = BehaviorTimeline()
    model.observe(0, "seated", "not_drinking")
    model.observe(0.5, "seated", "not_drinking")
    model.observe(1, "empty", "drinking")
    result = model.observe(1.5, "empty", "drinking")
    assert result["drinking"] == "unknown"
    assert all(event["kind"] != "suspected_drink" for event in result["events"])
