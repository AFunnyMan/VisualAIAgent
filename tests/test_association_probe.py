from scripts.association_probe import associate


def candidate(score, category="cup", bbox=(0, 0, 10, 10)):
    return {"category": category, "confidence": score, "bbox": bbox}


def test_low_score_cannot_start_or_indefinitely_refresh_a_track():
    assert associate([], [candidate(0.2)], 0) == []
    state = associate([], [candidate(0.8)], 0)
    state = associate(state, [candidate(0.2)], 1)
    assert len(state) == 1
    state = associate(state, [candidate(0.2)], 2)
    assert len(state) == 1
    assert associate(state, [candidate(0.2)], 3) == []


def test_wrong_class_location_and_long_gap_cannot_reuse_confirmation():
    state = associate([], [candidate(0.8)], 0)
    assert associate(state, [candidate(0.2, "bottle")], 1) == []
    assert associate(state, [candidate(0.2, bbox=(20, 20, 30, 30))], 1) == []
    assert associate(state, [candidate(0.2)], 3) == []


def test_one_prior_box_cannot_confirm_multiple_low_candidates():
    state = associate([], [candidate(0.8)], 0)
    state = associate(state, [candidate(0.2), candidate(0.15)], 1)
    assert len(state) == 1
