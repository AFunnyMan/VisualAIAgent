from scripts.behavior_timeline_v2 import BehaviorTimelineV2


def _confirm_initial(model: BehaviorTimelineV2, posture: str = "seated") -> None:
    model.observe(0.0, posture, "not_drinking")
    model.observe(0.5, posture, "not_drinking")


def test_unknown_is_not_empty_and_does_not_invent_left_seat():
    model = BehaviorTimelineV2()
    _confirm_initial(model)

    first = model.observe(0.6, "unknown", "not_drinking")
    second = model.observe(1.1, "unknown", "not_drinking")

    assert first["posture"] == second["posture"] == "unknown"
    assert first["unknown_reasons"]["posture"] == "classifier_unknown"
    assert first["events"] == second["events"] == []


def test_short_unknown_needs_explicit_continuous_visibility_to_bridge_context():
    without_evidence = BehaviorTimelineV2()
    _confirm_initial(without_evidence)
    without_evidence.observe(0.7, "unknown", "not_drinking")
    without_evidence.observe(0.8, "standing", "not_drinking")
    assert without_evidence.observe(1.3, "standing", "not_drinking")["events"] == []

    visible = BehaviorTimelineV2()
    _confirm_initial(visible)
    assert (
        visible.observe(0.7, "unknown", "not_drinking", continuous_visible=True)["posture"]
        == "unknown"
    )
    recovering = visible.observe(0.8, "standing", "not_drinking", continuous_visible=True)
    assert recovering["posture"] == "unknown"
    assert visible.observe(1.3, "standing", "not_drinking", continuous_visible=True)["events"] == [
        {"kind": "stood_up", "timestamp": 1.3}
    ]


def test_long_unknown_clears_context_even_with_visibility_evidence():
    model = BehaviorTimelineV2(max_gap=2.0, unknown_bridge_seconds=0.6)
    _confirm_initial(model)
    model.observe(0.7, "unknown", "not_drinking", continuous_visible=True)
    model.observe(1.4, "unknown", "not_drinking", continuous_visible=True)
    model.observe(1.5, "standing", "not_drinking")
    assert model.observe(2.0, "standing", "not_drinking")["events"] == []


def test_late_recovery_and_recovery_without_visibility_clear_context():
    late = BehaviorTimelineV2(max_gap=2.0, unknown_bridge_seconds=0.6)
    _confirm_initial(late)
    late.observe(0.7, "unknown", "not_drinking", continuous_visible=True)
    late.observe(1.4, "standing", "not_drinking", continuous_visible=True)
    assert late.observe(1.9, "standing", "not_drinking", continuous_visible=True)["events"] == []

    invisible = BehaviorTimelineV2()
    _confirm_initial(invisible)
    invisible.observe(0.7, "unknown", "not_drinking", continuous_visible=True)
    invisible.observe(0.8, "standing", "not_drinking")
    assert (
        invisible.observe(1.3, "standing", "not_drinking", continuous_visible=True)["events"] == []
    )


def test_drinking_requires_two_samples_and_rearms_only_after_confirmed_end():
    model = BehaviorTimelineV2()
    _confirm_initial(model)

    assert model.observe(0.6, "seated", "drinking")["events"] == []
    assert model.observe(0.7, "seated", "drinking")["events"] == [
        {"kind": "suspected_drink", "timestamp": 0.7}
    ]
    assert model.observe(0.9, "seated", "drinking")["events"] == []
    model.observe(1.0, "seated", "not_drinking")
    model.observe(1.2, "seated", "drinking")  # short end candidate did not rearm
    assert model.observe(1.3, "seated", "drinking")["events"] == []
    model.observe(1.4, "seated", "not_drinking")
    model.observe(1.7, "seated", "not_drinking")
    model.observe(1.8, "seated", "drinking")
    assert model.observe(1.9, "seated", "drinking")["events"] == [
        {"kind": "suspected_drink", "timestamp": 1.9}
    ]


def test_one_drinking_unknown_drinking_sequence_does_not_repeat_event():
    model = BehaviorTimelineV2()
    _confirm_initial(model)
    model.observe(0.6, "seated", "drinking")
    assert model.observe(0.7, "seated", "drinking")["events"]
    assert model.observe(0.8, "seated", "unknown", continuous_visible=True)["drinking"] == "unknown"
    assert (
        model.observe(0.9, "seated", "drinking", continuous_visible=True)["drinking"] == "unknown"
    )
    assert model.observe(1.0, "seated", "drinking", continuous_visible=True)["events"] == []


def test_gap_stale_and_reverse_time_cannot_emit_cross_fault_action():
    for fault in ("gap", "stale", "reverse"):
        model = BehaviorTimelineV2()
        _confirm_initial(model)
        if fault == "gap":
            model.observe(2.0, "standing", "drinking")
            result = model.observe(2.5, "standing", "drinking")
        elif fault == "stale":
            model.observe(0.7, "standing", "drinking", fresh=False)
            model.observe(0.8, "standing", "drinking")
            result = model.observe(1.3, "standing", "drinking")
        else:
            model.observe(0.4, "standing", "drinking")
            result = model.observe(0.9, "standing", "drinking")
        assert result["events"] == []


def test_two_fresh_samples_after_stale_only_build_new_baseline():
    model = BehaviorTimelineV2()
    _confirm_initial(model)
    model.observe(0.7, "standing", "drinking", fresh=False)
    assert model.observe(0.8, "standing", "drinking")["events"] == []
    assert model.observe(1.3, "standing", "drinking")["events"] == []


def test_drinking_min_samples_rejects_single_sample_and_nonfinite_values():
    import pytest

    for invalid in (1, 1.0, float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            BehaviorTimelineV2(drinking_min_samples=invalid)


def test_posture_and_drinking_use_different_confirmation_windows():
    model = BehaviorTimelineV2()
    _confirm_initial(model)
    first = model.observe(0.6, "standing", "drinking")
    second = model.observe(0.7, "standing", "drinking")

    assert first["events"] == []
    assert second["events"] == [{"kind": "suspected_drink", "timestamp": 0.7}]
    assert second["posture"] == "seated"
    assert model.observe(1.1, "standing", "drinking")["events"] == [
        {"kind": "stood_up", "timestamp": 1.1}
    ]


def test_exit_evidence_only_bridges_standing_to_empty_and_not_drinking():
    model = BehaviorTimelineV2(posture_confirm_seconds=0.3)
    _confirm_initial(model, "standing")
    model.observe(0.6, "unknown", "unknown", exit_evidence=True)
    model.observe(0.7, "empty", "unknown", exit_evidence=True)
    result = model.observe(1.0, "empty", "unknown", exit_evidence=True)
    assert result["events"] == [{"kind": "left_seat", "timestamp": 1.0}]
    assert result["drinking"] == "unknown"

    seated = BehaviorTimelineV2(posture_confirm_seconds=0.3)
    _confirm_initial(seated)
    seated.observe(0.6, "unknown", "unknown", exit_evidence=True)
    seated.observe(0.7, "empty", "unknown", exit_evidence=True)
    assert seated.observe(1.0, "empty", "unknown", exit_evidence=True)["events"] == []


def test_exit_evidence_does_not_bridge_standing_recovery_or_faults():
    recovery = BehaviorTimelineV2(posture_confirm_seconds=0.3)
    _confirm_initial(recovery, "standing")
    recovery.observe(0.6, "unknown", "unknown", exit_evidence=True)
    recovery.observe(0.7, "seated", "not_drinking", exit_evidence=True)
    assert recovery.observe(1.0, "seated", "not_drinking", exit_evidence=True)["events"] == []

    stale = BehaviorTimelineV2(posture_confirm_seconds=0.3)
    _confirm_initial(stale, "standing")
    stale.observe(0.6, "unknown", "unknown", fresh=False, exit_evidence=True)
    stale.observe(0.7, "empty", "unknown", exit_evidence=True)
    assert stale.observe(1.0, "empty", "unknown", exit_evidence=True)["events"] == []
