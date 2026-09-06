"""SQLite-backed visual memory and isolated agent accounting."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from .events import EventStateMachine
from .models import CATEGORIES, Category, SceneObservation, ToolResult, VisualEvent, utcnow


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _category(value: str) -> Category:
    if value not in CATEGORIES:
        raise ValueError(f"Unsupported category: {value}")
    return value  # type: ignore[return-value]


class MemoryStore:
    """Own the database, evidence files, and visual event state machine."""

    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Callable[[], datetime] = utcnow,
        max_gap_seconds: float = 3.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._evidence_dir = self.data_dir / "evidence"
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "memory.sqlite3"
        self.clock = clock
        self.max_gap_seconds = max_gap_seconds
        self._lock = threading.RLock()
        self._machine = EventStateMachine(max_gap_seconds=max_gap_seconds)
        self._has_session_observation = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _backup_before_migration(
        self, source: sqlite3.Connection, *, from_version: str, to_version: str
    ) -> Path:
        backup_dir = self.data_dir / "backups"
        stamp = self.clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = (
            backup_dir
            / f"memory-v{from_version}-before-v{to_version}-{stamp}-{uuid4().hex}.sqlite3"
        )
        destination: sqlite3.Connection | None = None
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            destination = sqlite3.connect(backup_path)
            source.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
            destination.close()
            destination = None
            if not backup_path.is_file() or backup_path.stat().st_size == 0:
                raise OSError("backup file was not persisted")
        except (OSError, sqlite3.Error) as error:
            if destination is not None:
                destination.close()
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("Database migration backup failed") from error
        return backup_path

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            existing_tables = {
                row[0]
                for row in connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
                ).fetchall()
            }
            existing_version: str | None = None
            if existing_tables:
                if "schema_meta" not in existing_tables:
                    raise RuntimeError("Existing database has no supported schema metadata")
                try:
                    version_row = connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                except sqlite3.Error as error:
                    raise RuntimeError("Existing database has invalid schema metadata") from error
                if version_row is None or version_row[0] not in {"1", "2"}:
                    raise RuntimeError("Existing database schema version is not supported")
                existing_version = version_row[0]
                if existing_version == "2":
                    required_tables = {
                        "schema_meta",
                        "evidence",
                        "observations",
                        "visual_events",
                        "last_seen",
                        "watches",
                        "notifications",
                        "chat_interactions",
                        "agent_runs",
                        "tool_runs",
                    }
                    if not required_tables.issubset(existing_tables):
                        raise RuntimeError("Current database schema is missing required tables")
                    current_columns = {
                        row[1]
                        for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()
                    }
                    if "usage_complete" not in current_columns:
                        raise RuntimeError("Current database schema is missing required columns")
                    return
                self._backup_before_migration(
                    connection, from_version=existing_version, to_version="2"
                )
            connection.executescript(
                """BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '2');

                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0)
                );
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    monotonic_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    fresh INTEGER NOT NULL,
                    detections_json TEXT NOT NULL,
                    evidence_id TEXT REFERENCES evidence(evidence_id),
                    error TEXT,
                    inference_ms REAL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observations_time
                    ON observations(observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_evidence
                    ON observations(evidence_id) WHERE evidence_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS visual_events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    regions_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    evidence_id TEXT REFERENCES evidence(evidence_id),
                    last_seen_at TEXT,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visual_events_lookup
                    ON visual_events(category, confirmed_at DESC);

                CREATE TABLE IF NOT EXISTS last_seen (
                    category TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    regions_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    evidence_id TEXT REFERENCES evidence(evidence_id),
                    source TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watches (
                    watch_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    event_id TEXT REFERENCES visual_events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_watches_match
                    ON watches(status, category, condition, expires_at);

                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
                    event_id TEXT NOT NULL REFERENCES visual_events(event_id),
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(watch_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS chat_interactions (
                    request_id TEXT PRIMARY KEY,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
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
                    usage_complete INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    tool_summary_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_budget
                    ON agent_runs(kind, local_date, status);

                CREATE TABLE IF NOT EXISTS tool_runs (
                    tool_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
                    tool_name TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    called_at TEXT NOT NULL
                );
                COMMIT;
                """
            )
            agent_run_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()
            }
            if existing_version == "1" and "usage_complete" not in agent_run_columns:
                connection.executescript(
                    """BEGIN IMMEDIATE;
                    ALTER TABLE agent_runs
                        ADD COLUMN usage_complete INTEGER NOT NULL DEFAULT 0;
                    UPDATE schema_meta SET value = '2' WHERE key = 'schema_version';
                    COMMIT;
                    """
                )
            elif "usage_complete" in agent_run_columns:
                connection.execute(
                    "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
                )
            else:
                raise RuntimeError("Current database schema is missing required columns")
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def set_max_gap_seconds(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        with self._lock:
            self.max_gap_seconds = seconds
            self._machine.max_gap_seconds = seconds
            self._machine.reset_continuity()

    def _write_evidence(self, jpeg: bytes | None) -> tuple[str, str, str, int] | None:
        if not jpeg:
            return None
        evidence_id = uuid4().hex
        relative_path = f"evidence/{evidence_id}.jpg"
        final_path = self.data_dir / relative_path
        temporary_path = self._evidence_dir / f".{evidence_id}.tmp"
        try:
            with temporary_path.open("xb") as stream:
                stream.write(jpeg)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, final_path)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return evidence_id, relative_path, hashlib.sha256(jpeg).hexdigest(), len(jpeg)

    def ingest(self, observation: SceneObservation, jpeg: bytes | None = None) -> list[VisualEvent]:
        """Persist one observation and atomically commit any resulting events."""
        with self._lock:
            return self._ingest_locked(observation, jpeg)

    def _ingest_locked(
        self, observation: SceneObservation, jpeg: bytes | None
    ) -> list[VisualEvent]:
        events, candidate_machine = self._machine.preview(observation)
        should_store_image = bool(observation.detections or events)
        evidence = self._write_evidence(jpeg if should_store_image else None)
        evidence_id = evidence[0] if evidence else None
        persisted_events = [
            event.model_copy(update={"evidence_id": evidence_id}) for event in events
        ]
        stored_observation = observation.model_copy(update={"evidence_id": evidence_id})
        detections_json = json.dumps(
            [item.model_dump(mode="json") for item in stored_observation.detections],
            separators=(",", ":"),
        )

        try:
            obsolete_paths: list[Path] = []
            with self._transaction(immediate=True) as connection:
                if evidence:
                    connection.execute(
                        "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                        (evidence[0], evidence[1], _iso(self.clock()), evidence[2], evidence[3]),
                    )
                cursor = connection.execute(
                    """INSERT INTO observations(
                        observed_at, monotonic_at, status, fresh, detections_json, evidence_id,
                        error, inference_ms, width, height, source, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _iso(stored_observation.observed_at),
                        stored_observation.monotonic_at,
                        stored_observation.status,
                        int(stored_observation.fresh),
                        detections_json,
                        evidence_id,
                        stored_observation.error,
                        stored_observation.inference_ms,
                        stored_observation.width,
                        stored_observation.height,
                        stored_observation.source,
                        _iso(self.clock()),
                    ),
                )
                observation_id = cursor.lastrowid
                by_category: dict[str, list[dict[str, Any]]] = {}
                for item in stored_observation.detections:
                    by_category.setdefault(item.category, []).append(item.model_dump(mode="json"))
                if stored_observation.status == "running" and stored_observation.fresh:
                    for category, candidates in by_category.items():
                        regions = list(dict.fromkeys(item["region"] for item in candidates))
                        connection.execute(
                            """INSERT INTO last_seen(
                                category, observed_at, regions_json, candidates_json,
                                evidence_id, source
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(category) DO UPDATE SET
                                observed_at=excluded.observed_at,
                                regions_json=excluded.regions_json,
                                candidates_json=excluded.candidates_json,
                                evidence_id=excluded.evidence_id,
                                source=excluded.source""",
                            (
                                category,
                                _iso(stored_observation.observed_at),
                                json.dumps(regions, separators=(",", ":")),
                                json.dumps(candidates, separators=(",", ":")),
                                evidence_id,
                                stored_observation.source,
                            ),
                        )
                for event in persisted_events:
                    connection.execute(
                        """INSERT INTO visual_events(
                            event_id, kind, category, regions_json, observed_at, confirmed_at,
                            evidence_id, last_seen_at, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id,
                            event.kind,
                            event.category,
                            json.dumps(event.regions, separators=(",", ":")),
                            _iso(event.observed_at),
                            _iso(event.confirmed_at),
                            event.evidence_id,
                            _iso(event.last_seen_at) if event.last_seen_at else None,
                            event.source,
                        ),
                    )
                connection.execute(
                    """UPDATE observations SET evidence_id = NULL
                       WHERE evidence_id IS NOT NULL AND observation_id != ?""",
                    (observation_id,),
                )
                obsolete = connection.execute(
                    """SELECT evidence_id, relative_path FROM evidence
                       WHERE evidence_id NOT IN (
                           SELECT evidence_id FROM observations WHERE evidence_id IS NOT NULL
                       ) AND evidence_id NOT IN (
                           SELECT evidence_id FROM visual_events WHERE evidence_id IS NOT NULL
                       ) AND evidence_id NOT IN (
                           SELECT evidence_id FROM last_seen WHERE evidence_id IS NOT NULL
                       )"""
                ).fetchall()
                if obsolete:
                    connection.executemany(
                        "DELETE FROM evidence WHERE evidence_id = ?",
                        [(row["evidence_id"],) for row in obsolete],
                    )
                    obsolete_paths = [self.data_dir / row["relative_path"] for row in obsolete]
        except BaseException:
            if evidence:
                try:
                    with self._read_connection() as connection:
                        committed = connection.execute(
                            "SELECT 1 FROM evidence WHERE evidence_id = ?", (evidence[0],)
                        ).fetchone()
                    if committed is None:
                        (self.data_dir / evidence[1]).unlink(missing_ok=True)
                except (OSError, sqlite3.Error):
                    # On an ambiguous commit, retaining a possible orphan is safer than
                    # deleting a file that the committed database might reference.
                    pass
            raise
        self._machine.adopt(candidate_machine)
        self._has_session_observation = True
        for path in obsolete_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return persisted_events

    def _latest_observation(self) -> sqlite3.Row | None:
        with self._read_connection() as connection:
            return connection.execute(
                "SELECT * FROM observations ORDER BY observation_id DESC LIMIT 1"
            ).fetchone()

    def get_current_scene(self) -> ToolResult:
        row = self._latest_observation()
        if row is None:
            return ToolResult(ok=True, data={"current": False, "observation": None})
        observed_at = _datetime(row["observed_at"])
        assert observed_at is not None
        now = self.clock()
        raw_age_seconds = (now.astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()
        clock_skew = raw_age_seconds < 0
        age_seconds = max(0.0, raw_age_seconds)
        current = (
            self._has_session_observation
            and bool(row["fresh"])
            and row["status"] == "running"
            and not clock_skew
            and age_seconds <= self.max_gap_seconds
        )
        effective_status = (
            row["status"] if row["status"] != "running" else ("running" if current else "stale")
        )
        return ToolResult(
            ok=True,
            data={
                "current": current,
                "effective_status": effective_status,
                "age_seconds": age_seconds,
                "clock_skew": clock_skew,
                "observation": {
                    "observed_at": row["observed_at"],
                    "status": row["status"],
                    "fresh": bool(row["fresh"]),
                    "detections": json.loads(row["detections_json"]),
                    "evidence_id": row["evidence_id"],
                    "error": row["error"],
                    "source": row["source"],
                },
            },
        )

    def find_object(self, category: str) -> ToolResult:
        try:
            valid_category = _category(category)
        except ValueError as error:
            return ToolResult(ok=False, error=str(error))
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM last_seen WHERE category = ?", (valid_category,)
            ).fetchone()
        if row is None:
            return ToolResult(ok=True, data={"category": valid_category, "found": False})
        return ToolResult(
            ok=True,
            data={
                "category": valid_category,
                "found": True,
                "last_seen_at": row["observed_at"],
                "regions": json.loads(row["regions_json"]),
                "candidates": json.loads(row["candidates_json"]),
                "evidence_id": row["evidence_id"],
                "evidence_available": self.evidence_path(row["evidence_id"]) is not None,
                "source": row["source"],
            },
        )

    def search_events(
        self, category: str, start: datetime, end: datetime, limit: int = 20
    ) -> ToolResult:
        try:
            valid_category = _category(category)
            start_iso, end_iso = _iso(start), _iso(end)
        except ValueError as error:
            return ToolResult(ok=False, error=str(error))
        if start >= end:
            return ToolResult(ok=False, error="start must be before end")
        if not 1 <= limit <= 20:
            return ToolResult(ok=False, error="limit must be between 1 and 20")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM visual_events
                   WHERE category = ? AND confirmed_at >= ? AND confirmed_at < ?
                   ORDER BY confirmed_at DESC LIMIT ?""",
                (valid_category, start_iso, end_iso, limit + 1),
            ).fetchall()
        truncated = len(rows) > limit
        events = [self._event_from_row(row).model_dump(mode="json") for row in rows[:limit]]
        return ToolResult(ok=True, data={"events": events, "truncated": truncated})

    def _event_from_row(self, row: sqlite3.Row) -> VisualEvent:
        return VisualEvent(
            event_id=row["event_id"],
            kind=row["kind"],
            category=row["category"],
            regions=json.loads(row["regions_json"]),
            observed_at=_datetime(row["observed_at"]),
            confirmed_at=_datetime(row["confirmed_at"]),
            evidence_id=row["evidence_id"],
            last_seen_at=_datetime(row["last_seen_at"]),
            source=row["source"],
        )

    def get_event(self, event_id: str) -> VisualEvent | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM visual_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return self._event_from_row(row) if row else None

    def evidence_path(self, evidence_id: str | None) -> Path | None:
        if not evidence_id:
            return None
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT relative_path FROM evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        if row is None:
            return None
        path = (self.data_dir / row["relative_path"]).resolve()
        if self.data_dir.resolve() not in path.parents or not path.is_file():
            return None
        return path

    def cleanup(self, retention_days: int = 7, now: datetime | None = None) -> dict[str, int]:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        cutoff = _iso((now or self.clock()) - timedelta(days=retention_days))
        removed_files: list[Path] = []
        with self._lock, self._transaction(immediate=True) as connection:
            observations = connection.execute(
                "DELETE FROM observations WHERE observed_at < ?", (cutoff,)
            ).rowcount
            events = connection.execute(
                """DELETE FROM visual_events
                   WHERE confirmed_at < ?
                     AND event_id NOT IN (
                         SELECT event_id FROM watches WHERE event_id IS NOT NULL
                     )
                     AND event_id NOT IN (SELECT event_id FROM notifications)""",
                (cutoff,),
            ).rowcount
            stale_evidence = connection.execute(
                """SELECT evidence_id, relative_path FROM evidence
                   WHERE evidence_id NOT IN (
                       SELECT evidence_id FROM observations WHERE evidence_id IS NOT NULL
                   ) AND evidence_id NOT IN (
                       SELECT evidence_id FROM visual_events WHERE evidence_id IS NOT NULL
                   ) AND evidence_id NOT IN (
                       SELECT evidence_id FROM last_seen WHERE evidence_id IS NOT NULL
                   )"""
            ).fetchall()
            if stale_evidence:
                connection.executemany(
                    "DELETE FROM evidence WHERE evidence_id = ?",
                    [(row["evidence_id"],) for row in stale_evidence],
                )
                removed_files = [self.data_dir / row["relative_path"] for row in stale_evidence]
            indexed_paths = {
                row["relative_path"]
                for row in connection.execute("SELECT relative_path FROM evidence").fetchall()
            }
        for candidate in self._evidence_dir.iterdir():
            relative_path = candidate.relative_to(self.data_dir).as_posix()
            if candidate.is_file() and relative_path not in indexed_paths:
                removed_files.append(candidate)
        removed_count = 0
        for path in set(removed_files):
            try:
                path.unlink(missing_ok=True)
                removed_count += 1
            except OSError:
                pass
        return {"observations": observations, "events": events, "evidence": removed_count}

    def append_chat_interaction(
        self,
        request_id: str,
        user_message: str,
        assistant_message: str,
        created_at: datetime | None = None,
    ) -> None:
        if not request_id.strip():
            raise ValueError("request_id is required")
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO chat_interactions VALUES (?, ?, ?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET
                       user_message=excluded.user_message,
                       assistant_message=excluded.assistant_message,
                       created_at=excluded.created_at""",
                (request_id, user_message, assistant_message, _iso(created_at or self.clock())),
            )
            connection.execute(
                """DELETE FROM chat_interactions WHERE request_id NOT IN (
                       SELECT request_id FROM chat_interactions
                       ORDER BY created_at DESC, rowid DESC LIMIT 5
                   )"""
            )

    def list_chat_interactions(self, limit: int = 5) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT request_id, user_message, assistant_message, created_at FROM (
                       SELECT rowid AS sequence, * FROM chat_interactions
                       ORDER BY created_at DESC, rowid DESC LIMIT ?
                   ) ORDER BY created_at ASC, sequence ASC""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reserve_event_agent_run(
        self,
        run_id: str,
        request_id: str,
        local_date: date,
        daily_limit: int,
        started_at: datetime | None = None,
        model: str | None = None,
    ) -> bool:
        if daily_limit < 0:
            raise ValueError("daily_limit cannot be negative")
        with self._transaction(immediate=True) as connection:
            duplicate = connection.execute(
                "SELECT 1 FROM agent_runs WHERE run_id = ? OR request_id = ?",
                (run_id, request_id),
            ).fetchone()
            if duplicate:
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE kind = 'event' AND local_date = ?",
                (local_date.isoformat(),),
            ).fetchone()[0]
            if count >= daily_limit:
                return False
            connection.execute(
                """INSERT INTO agent_runs(
                    run_id, request_id, kind, local_date, status, started_at, model
                ) VALUES (?, ?, 'event', ?, 'running', ?, ?)""",
                (
                    run_id,
                    request_id,
                    local_date.isoformat(),
                    _iso(started_at or self.clock()),
                    model,
                ),
            )
        return True

    def record_agent_run(
        self,
        *,
        run_id: str,
        request_id: str,
        kind: Literal["user", "event"],
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        model: str | None,
        request_attempts: int,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        error: str | None,
        tool_summary: list[dict[str, Any]],
        local_date: date | None = None,
        usage_complete: bool = False,
    ) -> None:
        if request_attempts < 0:
            raise ValueError("request_attempts cannot be negative")
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO agent_runs(
                    run_id, request_id, kind, local_date, status, started_at, finished_at,
                    model, request_attempts, input_tokens, output_tokens, total_tokens,
                    usage_complete, error, tool_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    local_date=COALESCE(excluded.local_date, agent_runs.local_date),
                    status=excluded.status,
                    finished_at=excluded.finished_at,
                    model=COALESCE(excluded.model, agent_runs.model),
                    request_attempts=excluded.request_attempts,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    total_tokens=excluded.total_tokens,
                    usage_complete=excluded.usage_complete,
                    error=excluded.error,
                    tool_summary_json=excluded.tool_summary_json""",
                (
                    run_id,
                    request_id,
                    kind,
                    local_date.isoformat() if local_date else None,
                    status,
                    _iso(started_at),
                    _iso(finished_at) if finished_at else None,
                    model,
                    request_attempts,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    int(usage_complete),
                    error,
                    json.dumps(tool_summary, separators=(",", ":")),
                ),
            )

    def count_agent_runs(
        self,
        *,
        kind: Literal["user", "event"] = "event",
        local_date: date,
        timezone: str = "Asia/Shanghai",
        statuses: Sequence[str] | None = None,
    ) -> int:
        ZoneInfo(timezone)
        query = "SELECT COUNT(*) FROM agent_runs WHERE kind = ? AND local_date = ?"
        parameters: list[Any] = [kind, local_date.isoformat()]
        if statuses is not None:
            if not statuses:
                return 0
            query += f" AND status IN ({','.join('?' for _ in statuses)})"
            parameters.extend(statuses)
        with self._read_connection() as connection:
            return int(connection.execute(query, parameters).fetchone()[0])

    def list_agent_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT run_id, request_id, kind, local_date, status, started_at,
                          finished_at, model, request_attempts, input_tokens,
                          output_tokens, total_tokens, usage_complete,
                          error IS NOT NULL AS has_error, tool_summary_json
                   FROM agent_runs ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["has_error"] = bool(item["has_error"])
            item["usage_complete"] = bool(item["usage_complete"])
            item["tool_summary"] = json.loads(item.pop("tool_summary_json"))
            results.append(item)
        return results

    def get_usage_summary(
        self, local_date: date, timezone: str = "Asia/Shanghai"
    ) -> dict[str, Any]:
        ZoneInfo(timezone)
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT kind, request_attempts, input_tokens, output_tokens, total_tokens,
                          usage_complete
                   FROM agent_runs WHERE local_date = ?""",
                (local_date.isoformat(),),
            ).fetchall()
        result: dict[str, Any] = {
            "local_date": local_date.isoformat(),
            "timezone": timezone,
            "user_runs": sum(row["kind"] == "user" for row in rows),
            "event_runs": sum(row["kind"] == "event" for row in rows),
            "total_runs": len(rows),
            "total_request_attempts": sum(row["request_attempts"] for row in rows),
        }
        usage_fields = ("input_tokens", "output_tokens", "total_tokens")
        complete = all(
            bool(row["usage_complete"]) and all(row[field] is not None for field in usage_fields)
            for row in rows
        )
        for field in usage_fields:
            known = sum(row[field] for row in rows if row[field] is not None)
            result[f"known_{field}"] = known
            result[field] = known if complete else None
        result["usage_complete"] = complete
        return result

    def record_tool_run(
        self,
        *,
        run_id: str,
        tool_name: str,
        ok: bool,
        summary: dict[str, Any],
        called_at: datetime | None = None,
    ) -> None:
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO tool_runs(
                    run_id, tool_name, ok, summary_json, called_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    run_id,
                    tool_name,
                    int(ok),
                    json.dumps(summary, separators=(",", ":")),
                    _iso(called_at or self.clock()),
                ),
            )
