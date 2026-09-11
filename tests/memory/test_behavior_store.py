import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from visual_ai_agent.behavior_models import BehaviorEvent, BehaviorObservation
from visual_ai_agent.behavior_store import BehaviorStore
from visual_ai_agent.memory import MemoryStore

BASE = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def observation(
    seconds: float,
    *,
    posture: str = "seated",
    drinking: bool | None = False,
    status: str = "running",
    fresh: bool = True,
    events: list[BehaviorEvent] | None = None,
) -> BehaviorObservation:
    return BehaviorObservation(
        observed_at=BASE + timedelta(seconds=seconds),
        monotonic_at=seconds,
        status=status,
        fresh=fresh,
        posture=posture,
        drinking=drinking,
        events=events or [],
        model_version="behavior-r03",
        scene_id="desk-1",
        source="test",
    )


def test_counts_only_short_continuous_intervals_and_restart_breaks_continuity(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    for second in (0.0, 0.1, 0.2):
        clock.value = BASE + timedelta(seconds=second)
        behavior.ingest(observation(second))
    clock.value = BASE + timedelta(seconds=1)
    behavior.ingest(observation(1.0))
    restarted = BehaviorStore(memory)
    clock.value = BASE + timedelta(seconds=1.1)
    restarted.ingest(observation(1.1))

    stats = behavior.statistics(date(2026, 1, 1)).data
    assert stats["seated_seconds"] == 0.2
    assert stats["unknown_seconds"] == pytest.approx(0.8)
    assert stats["observed_seconds"] == pytest.approx(1.0)


def test_unknown_is_counted_but_fault_and_time_rollback_are_not(tmp_path) -> None:
    clock = Clock(BASE)
    behavior = BehaviorStore(MemoryStore(tmp_path, clock=clock))
    samples = [
        observation(0, posture="unknown", drinking=None),
        observation(0.1, posture="unknown", drinking=None),
        observation(0.2, posture="unknown", status="error", fresh=False),
        observation(0.3, posture="unknown", drinking=None),
        observation(0.25, posture="unknown", drinking=None),
    ]
    for sample in samples:
        clock.value = max(clock.value, sample.observed_at)
        behavior.ingest(sample)
    stats = behavior.statistics(date(2026, 1, 1)).data
    assert stats["unknown_seconds"] == pytest.approx(0.3)


def test_event_evidence_is_deduplicated_and_search_is_bounded(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    event = BehaviorEvent(
        event_id="event-1",
        kind="stood_up",
        observed_at=BASE,
        confirmed_at=BASE,
        model_version="behavior-r03",
        scene_id="desk-1",
        source="test",
    )
    behavior.ingest(observation(0, posture="standing"))
    clock.value += timedelta(seconds=0.1)
    event = event.model_copy(update={"observed_at": clock.value, "confirmed_at": clock.value})
    sample = observation(0.1, posture="standing", events=[event])
    first = behavior.ingest(sample, b"jpeg")
    duplicate = behavior.ingest(sample, b"duplicate")
    assert first[0].evidence_id is not None
    assert duplicate == []
    assert memory.evidence_path(first[0].evidence_id).read_bytes() == b"jpeg"
    assert list((tmp_path / "evidence").glob("*.jpg")) == [
        memory.evidence_path(first[0].evidence_id)
    ]
    result = behavior.search_events(BASE - timedelta(seconds=1), BASE + timedelta(seconds=1))
    assert result.ok and result.data["events"][0]["kind"] == "stood_up"
    assert not behavior.search_events(BASE, BASE + timedelta(seconds=1), limit=101).ok


def test_statistics_splits_interval_at_local_midnight(tmp_path) -> None:
    start = datetime(2026, 1, 1, 15, 59, 59, 900000, tzinfo=UTC)
    clock = Clock(start)
    behavior = BehaviorStore(MemoryStore(tmp_path, clock=clock))
    behavior.ingest(observation(0).model_copy(update={"observed_at": start, "monotonic_at": 0.0}))
    clock.value = start + timedelta(seconds=0.2)
    behavior.ingest(
        observation(0.2).model_copy(
            update={"observed_at": start + timedelta(seconds=0.2), "monotonic_at": 0.2}
        )
    )
    assert behavior.statistics(date(2026, 1, 1)).data["seated_seconds"] == 0.1
    assert behavior.statistics(date(2026, 1, 2)).data["seated_seconds"] == 0.1


def test_current_is_session_scoped_and_future_sample_is_unknown(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    behavior.ingest(observation(0))
    assert behavior.current().data["current"] is True
    assert behavior.current().data["posture"] == "seated"
    restarted = BehaviorStore(memory)
    assert restarted.current().data["current"] is False
    restarted.ingest(observation(1))
    current = restarted.current().data
    assert current["current"] is False
    assert current["posture"] == "unknown"
    assert current["observation"]["observed_at"] == BASE.isoformat()


def test_intervals_are_coalesced_instead_of_storing_every_frame(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    for index in range(100):
        clock.value = BASE + timedelta(seconds=index / 10)
        behavior.ingest(observation(index / 10))
    with memory._read_connection() as connection:
        rows = connection.execute("SELECT * FROM behavior_intervals").fetchall()
    assert len(rows) == 1
    assert rows[0]["duration_seconds"] == pytest.approx(9.9)


def test_invalid_event_boundaries_are_rejected_and_break_next_continuity(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    event = BehaviorEvent(
        event_id="unsafe",
        kind="stood_up",
        observed_at=BASE,
        confirmed_at=BASE,
        model_version="behavior-r03",
        scene_id="desk-1",
        source="test",
    )
    assert behavior.ingest(observation(0, events=[event])) == []  # first sample is baseline
    clock.value += timedelta(seconds=0.1)
    stale = observation(
        0.1,
        status="stale",
        fresh=False,
        events=[event.model_copy(update={"observed_at": clock.value, "confirmed_at": clock.value})],
    )
    assert behavior.ingest(stale, b"unsafe") == []
    clock.value += timedelta(seconds=0.1)
    assert (
        behavior.ingest(
            observation(
                0.2,
                events=[
                    event.model_copy(
                        update={
                            "event_id": "after-fault",
                            "observed_at": clock.value,
                            "confirmed_at": clock.value,
                        }
                    )
                ],
            )
        )
        == []
    )
    assert list((tmp_path / "evidence").glob("*.jpg")) == []


def test_time_rollback_does_not_overlap_or_bridge_later_intervals(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    for second in (0.0, 0.1):
        clock.value = BASE + timedelta(seconds=second)
        behavior.ingest(observation(second))
    behavior.ingest(observation(0.05))
    for second in (0.2, 0.3):
        clock.value = BASE + timedelta(seconds=second)
        behavior.ingest(observation(second))
    assert behavior.statistics(date(2026, 1, 1)).data["seated_seconds"] == 0.2


def test_future_sample_is_ignored_and_normal_samples_resume_from_a_new_boundary(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    behavior.ingest(observation(0))
    behavior.ingest(observation(10))
    clock.value += timedelta(seconds=0.1)
    behavior.ingest(observation(0.1))
    clock.value += timedelta(seconds=0.1)
    behavior.ingest(observation(0.2))
    current = behavior.current().data
    assert current["observation"]["observed_at"] == (BASE + timedelta(seconds=0.2)).isoformat()
    assert behavior.statistics(date(2026, 1, 1)).data["seated_seconds"] == pytest.approx(0.1)


def test_source_change_breaks_interval_and_rejects_mismatched_event(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    behavior.ingest(observation(0))
    clock.value += timedelta(seconds=0.1)
    behavior.ingest(observation(0.1).model_copy(update={"source": "camera"}))
    assert behavior.statistics(date(2026, 1, 1)).data["observed_seconds"] == 0
    event = BehaviorEvent(
        kind="stood_up",
        observed_at=clock.value,
        confirmed_at=clock.value,
        model_version="behavior-r03",
        scene_id="desk-1",
        source="test",
    )
    with pytest.raises(ValueError, match="source"):
        behavior.ingest(observation(0.1).model_copy(update={"source": "camera", "events": [event]}))


def test_v4_schema_and_rule_schema_roll_back_together(tmp_path, monkeypatch) -> None:
    import visual_ai_agent.context_rules as context_rules

    database = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '2');
        CREATE TABLE agent_runs (
            run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            local_date TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            model TEXT, request_attempts INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER,
            output_tokens INTEGER, total_tokens INTEGER, usage_complete INTEGER NOT NULL DEFAULT 0,
            error TEXT, tool_summary_json TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    connection.close()

    def fail_schema(connection):
        connection.execute("CREATE TABLE must_roll_back(value TEXT)")
        raise sqlite3.OperationalError("injected schema failure")

    monkeypatch.setattr(context_rules, "create_schema", fail_schema)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        MemoryStore(tmp_path, clock=lambda: BASE)
    with sqlite3.connect(database) as unchanged:
        assert (
            unchanged.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "2"
        )
        tables = {
            row[0]
            for row in unchanged.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "behavior_events" not in tables
    assert "must_roll_back" not in tables


def test_long_clock_consistent_gap_is_unknown_within_same_live_session(tmp_path) -> None:
    clock = Clock(BASE)
    behavior = BehaviorStore(MemoryStore(tmp_path, clock=clock))
    behavior.ingest(observation(0))
    clock.value += timedelta(seconds=5)
    behavior.ingest(observation(5))
    stats = behavior.statistics(date(2026, 1, 1)).data
    assert stats["unknown_seconds"] == 5
    assert stats["seated_seconds"] == 0


def test_stopped_gap_restart_and_clock_jump_are_not_observed_unknown_time(tmp_path) -> None:
    clock = Clock(BASE)
    memory = MemoryStore(tmp_path, clock=clock)
    behavior = BehaviorStore(memory)
    behavior.ingest(observation(0, status="stopped", fresh=False))
    clock.value += timedelta(seconds=5)
    behavior.ingest(observation(5))
    # Matching wall time without matching monotonic time is a clock discontinuity.
    clock.value += timedelta(seconds=5)
    behavior.ingest(observation(5.1).model_copy(update={"observed_at": clock.value}))
    assert behavior.statistics(date(2026, 1, 1)).data["observed_seconds"] == 0

    restarted = BehaviorStore(memory)
    clock.value += timedelta(seconds=5)
    restarted.ingest(
        observation(15.1).model_copy(update={"observed_at": clock.value, "monotonic_at": 15.1})
    )
    assert restarted.statistics(date(2026, 1, 1)).data["observed_seconds"] == 0


def test_schema_two_is_backed_up_and_upgraded_without_losing_data(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '2');
        CREATE TABLE agent_runs (
            run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            local_date TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            model TEXT, request_attempts INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER,
            output_tokens INTEGER, total_tokens INTEGER, usage_complete INTEGER NOT NULL DEFAULT 0,
            error TEXT, tool_summary_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO agent_runs(run_id,request_id,kind,status,started_at)
        VALUES ('old-run','old-request','event','failed','2026-01-01T00:00:00+00:00');
        """
    )
    connection.close()

    store = MemoryStore(tmp_path, clock=lambda: BASE)
    assert store.list_agent_runs()[0]["run_id"] == "old-run"
    with store._read_connection() as migrated:
        assert (
            migrated.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[
                0
            ]
            == "4"
        )
        tables = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "behavior_observations",
        "behavior_intervals",
        "behavior_events",
        "laptop_observations",
        "laptop_events",
    } <= tables
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as archived:
        assert (
            archived.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[
                0
            ]
            == "2"
        )
