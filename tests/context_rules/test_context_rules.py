from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import Detection, SceneObservation

BASE = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = BASE

    def __call__(self):
        return self.value


def observation(second: int, *, category: str | None = "cup", fresh: bool = True):
    detections = []
    if category:
        detections = [
            Detection(category=category, confidence=0.9, bbox=(0, 0, 4, 4), region="left")
        ]
    return SceneObservation(
        observed_at=BASE + timedelta(seconds=second),
        monotonic_at=float(second),
        status="running",
        fresh=fresh,
        detections=detections,
        source="test",
        scene_id="scene-1",
    )


def object_facts(seconds=(1.0, 1.1, 1.2), *, broken=False):
    return [
        {
            "observed_at": BASE + timedelta(seconds=second),
            "ingested_at": BASE + timedelta(seconds=second),
            "status": "error" if broken and index == 1 else "running",
            "fresh": True,
            "source": "test",
            "scene_id": "scene-1",
            "detections": [{"category": "cup", "region": "left"}],
            "evidence_id": None,
        }
        for index, second in enumerate(seconds)
    ]


def test_rule_requires_strict_time_and_three_stable_prior_facts(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    created = service.create_rule(
        "stood_up",
        request_id="create",
        message="杯子仍在左侧",
        after_time="10:00",
        object_category="cup",
        region="left",
    )
    assert (
        service.process_event(
            {"event_id": "early", "kind": "stood_up", "timestamp": BASE, "source": "test"}
        )
        == []
    )
    assert (
        service.process_event(
            {
                "event_id": "unstable",
                "kind": "stood_up",
                "timestamp": BASE + timedelta(seconds=2),
                "source": "test",
            }
        )
        == []
    )
    clock.value = BASE + timedelta(seconds=2)
    jobs = service.process_event(
        {
            "event_id": "valid",
            "kind": "stood_up",
            "timestamp": BASE + timedelta(seconds=1.2),
            "source": "test",
            "scene_id": "scene-1",
            "object_facts": object_facts(),
        }
    )
    assert len(jobs) == 1
    assert jobs[0]["rule_id"] == created.data["rule"]["rule_id"]
    assert (
        service.process_event(
            {
                "event_id": "valid",
                "kind": "stood_up",
                "timestamp": BASE + timedelta(seconds=1.2),
                "source": "test",
                "scene_id": "scene-1",
                "object_facts": object_facts(),
            }
        )
        == []
    )


def test_unknown_object_fact_never_matches(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    service.create_rule(
        "left_seat", request_id="create", message="手机在右边", object_category="cell phone"
    )
    for second in (1, 2, 3):
        store.ingest(observation(second, category=None))
    assert (
        service.process_event(
            {
                "event_id": "left",
                "kind": "left_seat",
                "timestamp": BASE + timedelta(seconds=3),
                "source": "test",
            }
        )
        == []
    )
    assert (
        service.process_event(
            {
                "event_id": "broken",
                "kind": "left_seat",
                "timestamp": BASE + timedelta(seconds=2),
                "source": "test",
                "scene_id": "scene-1",
                "object_facts": object_facts(broken=True),
            }
        )
        == []
    )


def test_database_object_facts_do_not_skip_fault_or_cross_scene(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock, max_gap_seconds=2.5)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    service.create_rule(
        "stood_up",
        request_id="db-facts",
        message="杯子在左侧",
        object_category="cup",
        region="left",
    )
    for second in (1, 2, 3):
        clock.value = BASE + timedelta(seconds=second)
        store.ingest(observation(second))
    clock.value = BASE + timedelta(seconds=3.1)
    event = {
        "event_id": "db-valid",
        "kind": "stood_up",
        "timestamp": BASE + timedelta(seconds=3),
        "trigger_ingested_at": clock.value,
        "source": "test",
        "scene_id": "scene-1",
    }
    assert len(service.process_event(event)) == 1

    clock.value = BASE + timedelta(seconds=4)
    store.ingest(
        SceneObservation(
            observed_at=clock.value,
            monotonic_at=4.0,
            status="error",
            fresh=False,
            source="test",
            scene_id="scene-1",
        )
    )
    for second in (5, 6):
        clock.value = BASE + timedelta(seconds=second)
        store.ingest(observation(second))
    clock.value = BASE + timedelta(seconds=6.1)
    assert (
        service.process_event(
            {
                **event,
                "event_id": "after-fault",
                "timestamp": BASE + timedelta(seconds=6),
                "trigger_ingested_at": clock.value,
            }
        )
        == []
    )

    for second in (7, 8, 9):
        clock.value = BASE + timedelta(seconds=second)
        other = observation(second).model_copy(update={"scene_id": "scene-2"})
        store.ingest(other)
    clock.value = BASE + timedelta(seconds=9.1)
    assert (
        service.process_event(
            {
                **event,
                "event_id": "other-scene",
                "timestamp": BASE + timedelta(seconds=9),
                "trigger_ingested_at": clock.value,
            }
        )
        == []
    )


def test_update_supersedes_old_job_and_blocks_notification(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    result = service.create_rule("sat_down", request_id="c", message="坐下了")
    rule_id = result.data["rule"]["rule_id"]
    clock.value = BASE + timedelta(seconds=1)
    jobs = service.process_event(
        {"event_id": "sat", "kind": "sat_down", "timestamp": BASE + timedelta(seconds=1)}
    )
    assert len(jobs) == 1
    updated = service.update_rule(rule_id, request_id="u", message="再次坐下")
    assert updated.data["rule"]["version"] == 2
    assert not service.notify(rule_id, 1, "sat", "late").ok
    assert service.recover() == []


def test_claim_is_atomic_and_recover_requeues_only_active_version(tmp_path):
    clock = Clock()
    service = ContextRuleService(MemoryStore(tmp_path, clock=clock), timezone="UTC", clock=clock)
    rule = service.create_rule("sat_down", request_id="claim", message="坐下").data["rule"]
    clock.value += timedelta(seconds=1)
    service.process_event({"event_id": "claim-event", "kind": "sat_down", "timestamp": clock.value})

    def claim():
        return service.claim_job(rule["rule_id"], 1, "claim-event")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim(), range(8)))
    assert sum(result.ok for result in results) == 1
    recovered = service.recover()
    assert len(recovered) == 1 and recovered[0]["status"] == "queued"
    assert claim().ok
    service.update_rule(rule["rule_id"], request_id="claim-update", message="新消息")
    assert service.recover() == []
    assert not claim().ok


def test_seated_duration_unknown_resets_continuity_but_left_seat_rearms(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock, max_gap_seconds=70)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    rule = service.create_rule(
        "seated_duration", request_id="break", message="建议休息", seated_minutes=1
    ).data["rule"]
    base_observation = {
        "posture": "seated",
        "fresh": True,
        "scene_id": "scene-1",
        "source": "test",
        "model_version": "v1",
        "session_id": "run-1",
    }
    assert service.process_observation({**base_observation, "timestamp": BASE}) == []
    clock.value = BASE + timedelta(seconds=0.5)
    service.process_observation({"timestamp": clock.value, "posture": "unknown", "fresh": True})
    jobs = []
    for step in range(241):
        clock.value = BASE + timedelta(seconds=1 + step / 4)
        jobs += service.process_observation({**base_observation, "timestamp": clock.value})
    assert len(jobs) == 1
    stored = service.get_job(rule["rule_id"], jobs[0]["event_id"]).data["events"][0]
    behavior_fact = stored["fact_snapshot"]["behavior"]
    assert behavior_fact["continuous_seated_seconds"] == 60.0
    assert behavior_fact["threshold_minutes"] == 1
    assert behavior_fact["continuous_since"]
    assert behavior_fact["continuous_until"]
    clock.value += timedelta(seconds=0.25)
    assert service.process_observation({**base_observation, "timestamp": clock.value}) == []
    clock.value += timedelta(seconds=0.25)
    service.process_observation({**base_observation, "timestamp": clock.value, "posture": "empty"})
    clock.value += timedelta(seconds=0.25)
    assert service.process_observation({**base_observation, "timestamp": clock.value}) == []
    assert rule["enabled"] == 1


def test_notification_and_recovery_are_idempotent(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock)
    service = ContextRuleService(store, timezone="UTC", clock=clock)
    rule = service.create_rule("seat_occupied", request_id="c", message="座位有人").data["rule"]
    clock.value = BASE + timedelta(seconds=1)
    service.process_event(
        {
            "event_id": "occupied",
            "kind": "seat_occupied",
            "timestamp": BASE + timedelta(seconds=1),
            "scene_id": "scene-1",
            "model_version": "v1",
            "source": "test",
        }
    )
    job = service.get_job(rule["rule_id"], "occupied").data["events"][0]
    assert job["fact_snapshot"]["behavior"]["scene_id"] == "scene-1"
    first = service.fallback(job)
    second = service.notify(rule["rule_id"], 1, "occupied", "座位有人")
    assert first.ok and second.data["deduplicated"] is True
    assert service.recover() == []
    event = service.get_job(rule["rule_id"], "occupied")
    assert event.ok and len(event.data["events"]) == 1
    assert service.list_notifications().data["notifications"][0]["event_id"] == "occupied"


def test_validation_is_canonical_and_bounded(tmp_path):
    service = ContextRuleService(MemoryStore(tmp_path), timezone="UTC")
    assert not service.create_rule(
        "stood_up", request_id="bad-time", message="x", after_time="9:05"
    ).ok
    assert not service.create_rule(
        "seated_duration", request_id="bad-bool", message="x", seated_minutes=True
    ).ok
    assert not service.create_rule(
        "seated_duration", request_id="bad-large", message="x", seated_minutes=1441
    ).ok


def test_duration_crossing_is_consumed_when_time_condition_is_false(tmp_path):
    clock = Clock()
    service = ContextRuleService(MemoryStore(tmp_path, clock=clock), timezone="UTC", clock=clock)
    service.create_rule(
        "seated_duration",
        request_id="edge",
        message="休息",
        seated_minutes=1,
        after_time="10:01",
    )
    template = {
        "posture": "seated",
        "fresh": True,
        "scene_id": "scene-1",
        "source": "test",
        "model_version": "v1",
        "session_id": "run-1",
    }
    jobs = []
    for step in range(481):
        clock.value = BASE + timedelta(seconds=step / 4)
        jobs += service.process_observation({**template, "timestamp": clock.value})
    assert jobs == []


def test_restart_discards_unfinished_wall_clock_interval(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path, clock=clock)
    first = ContextRuleService(store, timezone="UTC", clock=clock)
    first.create_rule("seated_duration", request_id="restart", message="休息", seated_minutes=1)
    template = {
        "posture": "seated",
        "fresh": True,
        "scene_id": "scene-1",
        "source": "test",
        "model_version": "v1",
        "session_id": "run-1",
    }
    for step in range(240):
        clock.value = BASE + timedelta(seconds=step / 4)
        first.process_observation({**template, "timestamp": clock.value})
    restarted = ContextRuleService(store, timezone="UTC", clock=clock)
    clock.value += timedelta(seconds=0.25)
    assert restarted.process_observation({**template, "timestamp": clock.value}) == []


def test_future_observation_cuts_active_interval_without_advancing_it(tmp_path):
    clock = Clock()
    service = ContextRuleService(MemoryStore(tmp_path, clock=clock), timezone="UTC", clock=clock)
    service.create_rule("seated_duration", request_id="future", message="休息", seated_minutes=1)
    template = {
        "posture": "seated",
        "fresh": True,
        "scene_id": "scene-1",
        "source": "test",
        "model_version": "v1",
        "session_id": "run-1",
    }
    service.process_observation({**template, "timestamp": BASE})
    assert (
        service.process_observation({**template, "timestamp": BASE + timedelta(seconds=60)}) == []
    )
    clock.value = BASE + timedelta(seconds=60)
    assert service.process_observation({**template, "timestamp": clock.value}) == []
    with service.store._read_connection() as connection:
        state = connection.execute("SELECT * FROM context_rule_state").fetchone()
    assert state["continuous_since"] == state["last_valid_at"]
    assert state["crossed"] == 0
