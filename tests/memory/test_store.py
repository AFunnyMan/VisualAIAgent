import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event

import pytest

from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import Detection, SceneObservation
from visual_ai_agent.watches import WatchService


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def observation(second: float, category: str | None = "cup") -> SceneObservation:
    detections = []
    if category:
        detections.append(
            Detection(category=category, confidence=0.88, bbox=(1, 2, 3, 4), region="center")
        )
    return SceneObservation(
        observed_at=BASE + timedelta(seconds=second),
        monotonic_at=second,
        status="running",
        fresh=True,
        detections=detections,
        source="test",
        width=640,
        height=480,
    )


def test_persists_last_seen_evidence_and_restart_is_not_current(tmp_path) -> None:
    clock = Clock(BASE + timedelta(seconds=2))
    store = MemoryStore(tmp_path, clock=clock)
    for second in range(3):
        events = store.ingest(observation(second), b"jpeg-data")
    assert len(events) == 1
    result = store.find_object("cup")
    assert result.ok and result.data["found"]
    assert len(result.data["candidates"]) == 1
    assert result.data["evidence_id"]
    assert "path" not in result.data
    assert store.evidence_path(result.data["evidence_id"]).read_bytes() == b"jpeg-data"
    assert store.get_current_scene().data["current"] is True
    assert store.get_current_scene().data["observation"]["scene_id"] == "default"

    restarted = MemoryStore(tmp_path, clock=clock)
    scene = restarted.get_current_scene()
    assert scene.data["current"] is False
    assert scene.data["effective_status"] == "stale"
    assert restarted.find_object("cup").data["found"] is True


def test_find_object_returns_all_same_category_candidates_without_identity_claim(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    scene = observation(0).model_copy(
        update={
            "detections": [
                Detection(category="cup", confidence=0.9, bbox=(0, 0, 10, 10), region="left"),
                Detection(category="cup", confidence=0.8, bbox=(20, 0, 30, 10), region="right"),
            ]
        }
    )
    store.ingest(scene)
    result = store.find_object("cup")
    assert len(result.data["candidates"]) == 2
    assert result.data["regions"] == ["left", "right"]


def test_current_scene_becomes_stale_by_wall_clock(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock, max_gap_seconds=3)
    store.ingest(observation(0))
    assert store.get_current_scene().data["current"] is True
    clock.value += timedelta(seconds=4)
    assert store.get_current_scene().data["current"] is False


def test_current_scene_preserves_camera_failure_status(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    failed = observation(0, None).model_copy(
        update={"status": "disconnected", "fresh": False, "error": "camera lost"}
    )
    store.ingest(failed)
    scene = store.get_current_scene()
    assert scene.data["current"] is False
    assert scene.data["effective_status"] == "disconnected"
    assert scene.data["observation"]["error"] == "camera lost"


def test_future_observation_is_not_treated_as_current(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    store.ingest(observation(1))
    scene = store.get_current_scene()
    assert scene.data["current"] is False
    assert scene.data["effective_status"] == "stale"
    assert scene.data["clock_skew"] is True


def test_changing_max_gap_resets_pending_confirmation(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    store.ingest(observation(0))
    store.ingest(observation(1))
    store.set_max_gap_seconds(5)
    assert store.ingest(observation(2)) == []
    assert store.ingest(observation(3)) == []
    assert [event.kind for event in store.ingest(observation(4))] == ["appeared"]


def test_reset_event_baseline_forgets_presence_but_preserves_history(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    for second in range(3):
        appeared = store.ingest(observation(second))
    appeared_id = appeared[0].event_id

    store.reset_event_baseline()
    for second in (3, 6, 9):
        assert store.ingest(observation(second, None)) == []
    assert store.get_event(appeared_id) is not None
    assert store.find_object("cup").data["found"] is True
    assert store.ingest(observation(10)) == []
    assert store.ingest(observation(11)) == []
    assert [event.kind for event in store.ingest(observation(12))] == ["appeared"]


def test_failed_database_transaction_does_not_advance_machine(tmp_path, monkeypatch) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    store.ingest(observation(0))
    store.ingest(observation(1))
    real_transaction = store._transaction

    def fail_transaction(*, immediate=False):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "_transaction", fail_transaction)
    with pytest.raises(OSError, match="disk unavailable"):
        store.ingest(observation(2), b"orphan-must-be-removed")
    monkeypatch.setattr(store, "_transaction", real_transaction)
    events = store.ingest(observation(2))
    assert [event.kind for event in events] == ["appeared"]
    assert list((tmp_path / "evidence").glob("*.jpg")) == []


def test_evidence_write_failure_records_fact_without_false_reference(tmp_path, monkeypatch) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    monkeypatch.setattr(store, "_write_evidence", lambda _: None)
    for second in range(3):
        events = store.ingest(observation(second), b"jpeg")
    assert events[0].evidence_id is None
    result = store.find_object("cup")
    assert result.data["evidence_id"] is None
    assert result.data["evidence_available"] is False


def test_search_events_limits_and_reports_truncation(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    for cycle in range(3):
        start = cycle * 10
        for second in (start, start + 1, start + 2):
            store.ingest(observation(second))
        store.ingest(observation(start + 3, None))
        store.ingest(observation(start + 6, None))
        store.ingest(observation(start + 8, None))
    result = store.search_events("cup", BASE, BASE + timedelta(minutes=1), limit=2)
    assert result.ok
    assert len(result.data["events"]) == 2
    assert result.data["truncated"] is True
    assert not store.search_events("person", BASE, BASE + timedelta(minutes=1)).ok


def test_chat_and_agent_accounting_are_separate_and_preserve_unknown_usage(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    for index in range(7):
        store.append_chat_interaction(f"request-{index}", f"u{index}", f"a{index}")
    interactions = store.list_chat_interactions()
    assert [item["request_id"] for item in interactions] == [
        "request-2",
        "request-3",
        "request-4",
        "request-5",
        "request-6",
    ]
    assert store.reserve_event_agent_run("run-1", "event-1", date(2026, 1, 1), 1)
    assert not store.reserve_event_agent_run("run-2", "event-2", date(2026, 1, 1), 1)
    store.record_agent_run(
        run_id="run-1",
        request_id="event-1",
        kind="event",
        status="failed",
        started_at=BASE,
        finished_at=BASE + timedelta(seconds=1),
        model="test-model",
        request_attempts=1,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        error="offline",
        tool_summary=[],
        local_date=date(2026, 1, 1),
    )
    assert store.count_agent_runs(local_date=date(2026, 1, 1)) == 1
    runs = store.list_agent_runs()
    assert runs[0]["has_error"] is True
    assert "error" not in runs[0]
    assert runs[0]["tool_summary"] == []
    usage = store.get_usage_summary(date(2026, 1, 1))
    assert usage["total_runs"] == 1
    assert usage["total_request_attempts"] == 1
    assert usage["total_tokens"] is None
    assert usage["known_total_tokens"] == 0
    assert usage["usage_complete"] is False
    with store._connect() as connection:
        row = connection.execute("SELECT * FROM agent_runs WHERE run_id='run-1'").fetchone()
    assert row["input_tokens"] is None and row["total_tokens"] is None


def test_event_budget_reservation_is_atomic_under_concurrency(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)

    def reserve(index: int) -> bool:
        return store.reserve_event_agent_run(
            f"run-{index}", f"request-{index}", date(2026, 1, 1), 3
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, range(10)))
    assert sum(results) == 3
    assert store.count_agent_runs(local_date=date(2026, 1, 1)) == 3


def test_completing_user_run_adds_it_to_local_day_usage(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    common = {
        "run_id": "user-run",
        "request_id": "user-request",
        "kind": "user",
        "started_at": BASE,
        "model": "test-model",
        "error": None,
        "tool_summary": [],
    }
    store.record_agent_run(
        **common,
        status="running",
        finished_at=None,
        request_attempts=0,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )
    store.record_agent_run(
        **common,
        status="completed",
        finished_at=BASE + timedelta(seconds=1),
        request_attempts=1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        local_date=date(2026, 1, 1),
        usage_complete=True,
    )
    usage = store.get_usage_summary(date(2026, 1, 1))
    assert usage["user_runs"] == 1
    assert usage["total_tokens"] == 15
    assert usage["known_total_tokens"] == 15
    assert usage["usage_complete"] is True


def test_usage_summary_exposes_known_partial_without_claiming_complete(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    store.record_agent_run(
        run_id="partial-run",
        request_id="partial-request",
        kind="event",
        status="failed",
        started_at=BASE,
        finished_at=BASE + timedelta(seconds=1),
        model="test-model",
        request_attempts=2,
        input_tokens=12,
        output_tokens=3,
        total_tokens=15,
        error="third request failed",
        tool_summary=[],
        local_date=date(2026, 1, 1),
        usage_complete=False,
    )
    usage = store.get_usage_summary(date(2026, 1, 1))
    assert usage["known_total_tokens"] == 15
    assert usage["total_tokens"] is None
    assert usage["usage_complete"] is False


def test_schema_one_database_migrates_usage_completeness_without_data_loss(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '1');
        CREATE TABLE agent_runs (
            run_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            local_date TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            model TEXT,
            request_attempts INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            error TEXT,
            tool_summary_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO agent_runs(
            run_id, request_id, kind, local_date, status, started_at
        ) VALUES ('old-run', 'old-request', 'event', '2026-01-01', 'failed',
                  '2026-01-01T00:00:00+00:00');
        """
    )
    connection.close()

    store = MemoryStore(tmp_path, clock=lambda: BASE)
    runs = store.list_agent_runs()
    assert runs[0]["run_id"] == "old-run"
    assert runs[0]["usage_complete"] is False
    with store._read_connection() as migrated:
        version = migrated.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "4"
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    backup_version = backup.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    backup_columns = {row[1] for row in backup.execute("PRAGMA table_info(agent_runs)").fetchall()}
    old_run = backup.execute(
        "SELECT request_id FROM agent_runs WHERE run_id = 'old-run'"
    ).fetchone()[0]
    backup.close()
    assert backup_version == "1"
    assert "usage_complete" not in backup_columns
    assert old_run == "old-request"


def test_failed_migration_backup_does_not_modify_source_database(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '1');
        CREATE TABLE agent_runs (
            run_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            local_date TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            model TEXT,
            request_attempts INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            error TEXT,
            tool_summary_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO agent_runs(
            run_id, request_id, kind, local_date, status, started_at
        ) VALUES ('old-run', 'old-request', 'event', '2026-01-01', 'failed',
                  '2026-01-01T00:00:00+00:00');
        """
    )
    connection.close()
    (tmp_path / "backups").write_text("blocks backup directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="migration backup failed"):
        MemoryStore(tmp_path, clock=lambda: BASE)

    unchanged = sqlite3.connect(database)
    version = unchanged.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    columns = {row[1] for row in unchanged.execute("PRAGMA table_info(agent_runs)").fetchall()}
    old_run = unchanged.execute(
        "SELECT request_id FROM agent_runs WHERE run_id = 'old-run'"
    ).fetchone()[0]
    tables = {
        row[0]
        for row in unchanged.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    unchanged.close()
    assert version == "1"
    assert "usage_complete" not in columns
    assert old_run == "old-request"
    assert tables == {"schema_meta", "agent_runs"}


def test_unknown_schema_version_is_rejected_without_mutating_database(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '99');
        CREATE TABLE sentinel (value TEXT NOT NULL);
        INSERT INTO sentinel VALUES ('keep-me');
        """
    )
    connection.close()

    with pytest.raises(RuntimeError, match="schema version is not supported"):
        MemoryStore(tmp_path, clock=lambda: BASE)
    unchanged = sqlite3.connect(database)
    tables = {
        row[0]
        for row in unchanged.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    value = unchanged.execute("SELECT value FROM sentinel").fetchone()[0]
    unchanged.close()
    assert tables == {"schema_meta", "sentinel"}
    assert value == "keep-me"


def test_cleanup_preserves_last_seen_and_notification_evidence(tmp_path) -> None:
    clock = Clock(BASE - timedelta(seconds=1))
    store = MemoryStore(tmp_path, clock=clock)
    service = WatchService(store, clock=clock)
    service.create_watch("cup", "appeared", request_id="watch-1")
    for second in range(3):
        clock.value = BASE + timedelta(seconds=second)
        appeared = store.ingest(observation(second), f"seen-{second}".encode())
    event = appeared[0]
    claimed = service.match_event(event)
    clock.value = BASE + timedelta(seconds=3)
    assert service.notify_user(claimed[0].watch_id, event.event_id, "Cup appeared").ok
    protected_path = store.evidence_path(event.evidence_id)
    assert protected_path is not None

    for second in (3, 5, 8):
        store.ingest(observation(second, None), b"empty-scene")
    missing = store.search_events("cup", BASE, BASE + timedelta(minutes=1)).data["events"][0]
    missing_path = store.evidence_path(missing["evidence_id"])
    assert missing_path is not None

    clock.value = BASE + timedelta(days=8)
    removed = store.cleanup(now=clock.value)
    assert removed["events"] == 1
    assert store.get_event(event.event_id) is not None
    assert store.get_event(missing["event_id"]) is None
    assert protected_path.is_file()
    assert not missing_path.exists()
    assert store.find_object("cup").data["evidence_available"] is True


def test_cleanup_removes_crash_orphan_files(tmp_path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: BASE)
    orphan = tmp_path / "evidence" / "orphan.jpg"
    temporary = tmp_path / "evidence" / ".interrupted.tmp"
    orphan.write_bytes(b"orphan")
    temporary.write_bytes(b"partial")
    removed = store.cleanup(now=BASE)
    assert removed["evidence"] == 2
    assert not orphan.exists() and not temporary.exists()


def test_cleanup_holds_store_lock_through_file_scan_while_ingest_waits(
    tmp_path, monkeypatch
) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    store.ingest(observation(0), b"old-evidence")
    clock.value = BASE + timedelta(days=8)

    scan_reached = Event()
    allow_scan = Event()
    ingest_started = Event()
    ingest_lock_acquired = Event()
    real_remove = store._remove_evidence_files
    real_ingest_locked = store._ingest_locked

    def paused_remove(removed_files, indexed_paths):
        scan_reached.set()
        assert allow_scan.wait(timeout=2)
        return real_remove(removed_files, indexed_paths)

    monkeypatch.setattr(store, "_remove_evidence_files", paused_remove)

    def observed_ingest_locked(observation_value, jpeg):
        ingest_lock_acquired.set()
        return real_ingest_locked(observation_value, jpeg)

    monkeypatch.setattr(store, "_ingest_locked", observed_ingest_locked)

    def ingest_new_evidence():
        ingest_started.set()
        return store.ingest(observation(8 * 24 * 60 * 60), b"new-evidence")

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup_future = pool.submit(store.cleanup, 7, clock.value)
        assert scan_reached.wait(timeout=2)
        ingest_future = pool.submit(ingest_new_evidence)
        assert ingest_started.wait(timeout=2)
        assert not ingest_lock_acquired.wait(timeout=0.1)
        allow_scan.set()
        cleanup_future.result(timeout=2)
        ingest_future.result(timeout=2)
        assert ingest_lock_acquired.is_set()

    found = store.find_object("cup")
    evidence_path = store.evidence_path(found.data["evidence_id"])
    assert evidence_path is not None
    assert evidence_path.read_bytes() == b"new-evidence"
