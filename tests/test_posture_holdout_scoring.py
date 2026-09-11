from scripts.evaluate_posture_holdout import score_actions


def test_return_alternatives_match_once_and_late_events_stay_extra():
    expected = [{"kinds": ["sat_down", "seat_occupied"], "start": 10, "end": 11}]
    actual = [{"kind": "seat_occupied", "timestamp": 10.5},
              {"kind": "sat_down", "timestamp": 10.6},
              {"kind": "sat_down", "timestamp": 11.01}]
    result = score_actions(expected, actual)
    assert len(result["matches"]) == 1
    assert result["extra"] == actual[1:]
    assert result["missed"] == []


def test_wrong_kind_does_not_hide_a_missed_action():
    expected = [{"kinds": ["stood_up"], "start": 1, "end": 2}]
    actual = [{"kind": "left_seat", "timestamp": 1.5}]
    result = score_actions(expected, actual)
    assert result == {"matches": [], "extra": actual, "missed": expected}
