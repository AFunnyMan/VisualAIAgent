import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from visual_ai_agent.behavior_models import LaptopEvent, LaptopObservation
from visual_ai_agent.behavior_store import BehaviorStore
from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.laptop_store import LaptopStore
from visual_ai_agent.memory import MemoryStore

BASE = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.value = BASE

    def __call__(self):
        return self.value


def observation(second, state, *, reason=None, event=None, present=True):
    return LaptopObservation(
        observed_at=BASE + timedelta(seconds=second),
        monotonic_at=second,
        status="running",
        fresh=True,
        state=state,
        state_reason=reason,
        presence_verified=present,
        presence_confidence=0.95 if present else None,
        events=[event] if event else [],
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )


def test_transition_persists_evidence_and_remains_separate_from_behavior_statistics(tmp_path):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.set_capability(configured=True, available=True, reason="test capability")
    behavior = BehaviorStore(memory)
    for second in (0.0, 0.1, 0.2, 0.3, 0.4):
        clock.value = BASE + timedelta(seconds=second)
        laptop.ingest(observation(second, "unknown", reason="confirming"))
    clock.value = BASE + timedelta(seconds=0.5)
    laptop.ingest(observation(0.5, "open"))
    for second in (0.6, 0.7, 0.8, 0.9, 1.0):
        clock.value = BASE + timedelta(seconds=second)
        laptop.ingest(observation(second, "unknown", reason="confirming"))
    clock.value = BASE + timedelta(seconds=1.1)
    event = LaptopEvent(
        event_id="closed-1",
        kind="laptop_closed",
        state="closed",
        observed_at=clock.value,
        confirmed_at=clock.value,
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )
    persisted = laptop.ingest(observation(1.1, "closed", event=event), b"laptop-jpeg")
    assert len(persisted) == 1
    assert memory.evidence_path(persisted[0].evidence_id).read_bytes() == b"laptop-jpeg"
    stored = laptop.search_events(BASE, BASE + timedelta(seconds=2)).data["events"][0]
    assert stored["model_version"] == "lid-v1"
    assert stored["presence_model_version"] == "presence-v1"
    assert stored["scene_id"] == "desk-1"
    assert behavior.statistics(BASE.date()).data["observed_seconds"] == 0

    restarted = LaptopStore(memory)
    restarted.set_capability(configured=True, available=True, reason="test capability")
    assert restarted.current().data["current"] is False
    assert len(restarted.search_events(BASE, BASE + timedelta(seconds=2)).data["events"]) == 1


def test_absent_breaks_transition_and_discarded_event_does_not_keep_image(tmp_path):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.set_capability(configured=True, available=True, reason="test capability")
    clock.value = BASE
    laptop.ingest(observation(0, "open"))
    clock.value += timedelta(seconds=0.1)
    laptop.ingest(observation(0.1, "unknown", present=False))
    clock.value += timedelta(seconds=0.1)
    event = LaptopEvent(
        event_id="unsafe",
        kind="laptop_closed",
        state="closed",
        observed_at=clock.value,
        confirmed_at=clock.value,
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )
    assert laptop.ingest(observation(0.2, "closed", event=event), b"unsafe") == []
    assert laptop.search_events(BASE, BASE + timedelta(seconds=1)).data["events"] == []
    assert list((tmp_path / "evidence").glob("*.jpg")) == []


def _closed_event(second, event_id):
    timestamp = BASE + timedelta(seconds=second)
    return LaptopEvent(
        event_id=event_id,
        kind="laptop_closed",
        state="closed",
        observed_at=timestamp,
        confirmed_at=timestamp,
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )


def test_store_retains_baseline_for_one_visible_uncertain_sample_only(tmp_path):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.ingest(observation(0, "open"))
    clock.value = BASE + timedelta(seconds=0.1)
    laptop.ingest(observation(0.1, "unknown", reason="visible_transition_uncertain"))
    clock.value = BASE + timedelta(seconds=0.2)
    event = _closed_event(0.2, "bridged-close")
    persisted = laptop.ingest(observation(0.2, "closed", event=event), b"bridge")
    assert [item.event_id for item in persisted] == [event.event_id]


@pytest.mark.parametrize("second_at", [0.2, 0.4])
def test_store_clears_baseline_for_second_unknown_or_bridge_gap(tmp_path, second_at):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.ingest(observation(0, "open"))
    clock.value = BASE + timedelta(seconds=0.1)
    laptop.ingest(observation(0.1, "unknown", reason="visible_transition_uncertain"))
    clock.value = BASE + timedelta(seconds=second_at)
    laptop.ingest(
        observation(second_at, "unknown", reason="visible_transition_uncertain")
    )
    event_at = second_at + 0.1
    clock.value = BASE + timedelta(seconds=event_at)
    event = _closed_event(event_at, f"unsafe-{second_at}")
    assert laptop.ingest(observation(event_at, "closed", event=event), b"unsafe") == []


@pytest.mark.parametrize("duplicate_second", [1.0, 0.9])
def test_duplicate_or_reverse_observation_does_not_break_committed_transition_chain(
    tmp_path, duplicate_second
):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.set_capability(configured=True, available=True, reason="test capability")
    clock.value = BASE + timedelta(seconds=1)
    laptop.ingest(observation(1, "open"))
    clock.value = BASE + timedelta(seconds=1.05)
    assert laptop.ingest(observation(duplicate_second, "open")) == []

    clock.value = BASE + timedelta(seconds=1.1)
    event = LaptopEvent(
        event_id=f"closed-after-{duplicate_second}",
        kind="laptop_closed",
        state="closed",
        observed_at=clock.value,
        confirmed_at=clock.value,
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )
    persisted = laptop.ingest(observation(1.1, "closed", event=event), b"transition")
    assert [item.event_id for item in persisted] == [event.event_id]


def test_transaction_failure_does_not_advance_in_memory_transition_state(tmp_path, monkeypatch):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    laptop.set_capability(configured=True, available=True, reason="test capability")
    laptop.ingest(observation(0, "open"))

    original_transaction = memory._transaction

    @contextmanager
    def fail_commit(*, immediate=False):
        with original_transaction(immediate=immediate) as connection:
            yield connection
            raise sqlite3.OperationalError("injected commit failure")

    monkeypatch.setattr(memory, "_transaction", fail_commit)
    clock.value = BASE + timedelta(seconds=0.1)
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        laptop.ingest(observation(0.1, "closed"))

    monkeypatch.setattr(memory, "_transaction", original_transaction)
    event = LaptopEvent(
        event_id="closed-after-rollback",
        kind="laptop_closed",
        state="closed",
        observed_at=clock.value,
        confirmed_at=clock.value,
        model_version="lid-v1",
        presence_model_version="presence-v1",
        scene_id="desk-1",
        source="test",
    )
    persisted = laptop.ingest(observation(0.1, "closed", event=event), b"transition")
    assert [item.event_id for item in persisted] == [event.event_id]


def test_laptop_rule_requires_available_capability_and_keeps_versioned_fact(tmp_path):
    clock = Clock()
    memory = MemoryStore(tmp_path, clock=clock)
    laptop = LaptopStore(memory)
    service = ContextRuleService(memory, timezone="UTC", laptop=laptop, clock=clock)
    unavailable = service.create_rule("laptop_closed", request_id="disabled", message="电脑已合上")
    assert not unavailable.ok

    laptop.set_capability(configured=True, available=True, reason="accepted test gate")
    created = service.create_rule("laptop_closed", request_id="create", message="电脑已合上")
    duplicate = service.create_rule("laptop_closed", request_id="create", message="不会覆盖")
    assert created.ok and duplicate.data["deduplicated"] is True
    rule_id = created.data["rule"]["rule_id"]
    clock.value += timedelta(seconds=1)
    event = {
        "event_id": "lid-event",
        "kind": "laptop_closed",
        "state": "closed",
        "confirmed_at": clock.value,
        "evidence_id": "evidence-1",
        "model_version": "lid-v1",
        "presence_model_version": "presence-v1",
        "scene_id": "desk-1",
        "source": "test",
    }
    # A real evidence FK is required for jobs carrying evidence ids.
    with memory._transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?)",
            ("evidence-1", "evidence/manual.jpg", BASE.isoformat(), "sha", 0),
        )
    jobs = service.process_event(event)
    assert len(jobs) == 1
    stored = service.get_job(rule_id, "lid-event").data["events"][0]
    assert stored["fact_snapshot"]["laptop"]["model_version"] == "lid-v1"
    assert stored["fact_snapshot"]["laptop"]["presence_model_version"] == "presence-v1"
    assert service.process_event(event) == []
    updated = service.update_rule(rule_id, request_id="update", message="确认已合盖")
    assert updated.data["rule"]["version"] == 2
    assert not service.notify(rule_id, 1, "lid-event", "late").ok
    assert service.cancel_rule(rule_id).ok


def test_schema_three_is_backed_up_before_laptop_tables_are_added(tmp_path):
    initial = MemoryStore(tmp_path, clock=lambda: BASE)
    with initial._transaction(immediate=True) as connection:
        connection.execute("DROP TABLE laptop_events")
        connection.execute("DROP TABLE laptop_observations")
        connection.execute("UPDATE schema_meta SET value='3' WHERE key='schema_version'")
    migrated = MemoryStore(tmp_path, clock=lambda: BASE)
    with migrated._read_connection() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert version == "4"
    assert {"laptop_observations", "laptop_events"} <= tables
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as archived:
        assert (
            archived.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[
                0
            ]
            == "3"
        )
