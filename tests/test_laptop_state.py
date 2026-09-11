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


def test_only_positive_bounded_partial_evidence_can_bridge_transition():
    timeline = LaptopTimeline()
    feed(timeline, 0, "open")
    feed(timeline, 0.7, "partial", 3)
    assert [r.event for r in feed(timeline, 1, "closed") if r.event] == ["laptop_closed"]
    timeline = LaptopTimeline()
    feed(timeline, 0, "open")
    feed(timeline, 0.7, "partial", 40)
    assert not any(r.event for r in feed(timeline, 4.7, "closed"))
