from visual_ai_agent.laptop_state import LaptopTimeline


def feed(timeline, start, label, n=7, **kwargs):
    return [timeline.observe(round(start + i * 0.1, 6), label, **kwargs) for i in range(n)]


def test_baseline_closed_is_not_a_close_event_and_repeated_frames_do_not_repeat():
    timeline = LaptopTimeline()
    assert not any(r.event for r in feed(timeline, 0, "closed"))
    assert [r.event for r in feed(timeline, 0.7, "open") if r.event] == ["laptop_opened"]
    assert [r.event for r in feed(timeline, 1.4, "closed", 20) if r.event] == ["laptop_closed"]


def test_occlusion_gap_scene_change_cannot_prove_a_close():
    for interruption in ("unknown", "stale", "gap", "scene", "duplicate"):
        timeline = LaptopTimeline()
        feed(timeline, 0, "open")
        if interruption == "gap":
            rows = feed(timeline, 2, "closed")
        elif interruption == "scene":
            rows = feed(timeline, 0.7, "closed", scene_id="other")
        else:
            timeline.observe(
                0.6 if interruption == "duplicate" else 0.7,
                "unknown" if interruption == "unknown" else "closed",
                fresh=interruption != "stale",
            )
            rows = feed(timeline, 0.8, "closed")
        assert not any(r.event for r in rows)


def test_long_visible_open_motion_can_close_but_brief_closed_noise_cannot():
    timeline = LaptopTimeline()
    feed(timeline, 0, "open", 50)
    assert not any(r.event for r in feed(timeline, 5, "closed", 2))
    assert not any(r.event for r in feed(timeline, 5.2, "open", 7))
    assert [r.event for r in feed(timeline, 5.9, "closed") if r.event] == ["laptop_closed"]


def test_removed_partial_label_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="Invalid laptop state"):
        LaptopTimeline().observe(0, "partial")


def test_single_rejection_requires_continuous_independent_visibility():
    timeline = LaptopTimeline()
    feed(timeline, 0, "open", visibility_verified=True)
    rejected = timeline.observe(0.7, "unknown", visibility_verified=True)
    assert rejected.state == "unknown" and rejected.event is None
    assert rejected.reason == "visible_transition_uncertain"
    rows = feed(timeline, 0.8, "closed", visibility_verified=True)
    assert all(row.event is None for row in rows[:5])
    assert rows[5].event == "laptop_closed"  # a full fresh .5s after rejection
    assert sum(row.event is not None for row in rows) == 1


def test_bridge_never_uses_unknown_visibility_or_two_rejections():
    for failure in ("previous", "during", "after", "two", "late", "fault", "scene"):
        timeline = LaptopTimeline()
        feed(timeline, 0, "open", visibility_verified=failure != "previous")
        timeline.observe(
            0.7, "unknown", visibility_verified=failure != "during",
            fresh=failure != "fault", scene_id="other" if failure == "scene" else "default",
        )
        start = 0.8
        if failure == "two":
            timeline.observe(0.8, "unknown", visibility_verified=True)
            start = 0.9
        if failure == "late":
            start = 0.9  # within max_gap, outside the .15s visibility bridge
        rows = feed(timeline, start, "closed", visibility_verified=failure != "after")
        assert not any(row.event for row in rows), failure


def test_visible_rejection_cannot_bridge_without_a_recent_certain_sample():
    timeline = LaptopTimeline()
    feed(timeline, 0, "open", visibility_verified=True)
    result = timeline.observe(0.8, "unknown", visibility_verified=True)
    assert result.reason == "unknown"
    assert not any(row.event for row in feed(timeline, 0.9, "closed", visibility_verified=True))


def test_two_separated_rejections_cannot_extend_one_unconfirmed_transition():
    timeline = LaptopTimeline()
    feed(timeline, 0, "open", visibility_verified=True)
    timeline.observe(0.7, "unknown", visibility_verified=True)
    feed(timeline, 0.8, "closed", n=2, visibility_verified=True)
    assert timeline.observe(1.0, "unknown", visibility_verified=True).reason == "unknown"
    assert not any(row.event for row in feed(timeline, 1.1, "closed", visibility_verified=True))
