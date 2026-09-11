"""Durable, locally evaluated behavior-context rules.

The service deliberately stops at a claimed job.  A runtime may pass that job to
the product's existing seven-tool Agent, but camera/behavior waiting never calls
the model from here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .memory import MemoryStore, _datetime, _iso
from .models import CATEGORIES, ToolResult, utcnow

TRIGGERS = {
    "stood_up",
    "sat_down",
    "left_seat",
    "seat_occupied",
    "suspected_drink",
    "seated_duration",
}
REGIONS = {"left", "center", "right", "any"}


def create_schema(connection: sqlite3.Connection) -> None:
    """Create rule-owned tables; safe for MemoryStore migrations to call repeatedly."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS context_rules (
            rule_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL CHECK(version > 0),
            enabled INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS context_rule_versions (
            rule_id TEXT NOT NULL REFERENCES context_rules(rule_id),
            version INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            after_time TEXT,
            object_category TEXT,
            region TEXT NOT NULL,
            seated_minutes INTEGER,
            message TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            PRIMARY KEY(rule_id, version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS context_rule_jobs (
            job_id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL,
            rule_version INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            event_at TEXT NOT NULL,
            fact_observed_at TEXT,
            evidence_id TEXT REFERENCES evidence(evidence_id),
            trigger_snapshot_json TEXT NOT NULL,
            fact_snapshot_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(rule_id, rule_version, event_id),
            FOREIGN KEY(rule_id, rule_version)
                REFERENCES context_rule_versions(rule_id, version)
        )
        """,
        """CREATE INDEX IF NOT EXISTS idx_context_rule_jobs_status
            ON context_rule_jobs(status, created_at)""",
        """
        CREATE TABLE IF NOT EXISTS context_rule_notifications (
            notification_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE REFERENCES context_rule_jobs(job_id),
            message TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS context_rule_state (
            rule_id TEXT PRIMARY KEY REFERENCES context_rules(rule_id),
            continuous_since TEXT,
            last_valid_at TEXT,
            notified INTEGER NOT NULL DEFAULT 0,
            crossed INTEGER NOT NULL DEFAULT 0,
            continuity_key TEXT,
            updated_at TEXT NOT NULL
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(context_rule_state)")}
    if "crossed" not in columns:
        connection.execute(
            "ALTER TABLE context_rule_state ADD COLUMN crossed INTEGER NOT NULL DEFAULT 0"
        )
    if "continuity_key" not in columns:
        connection.execute("ALTER TABLE context_rule_state ADD COLUMN continuity_key TEXT")
    job_columns = {row[1] for row in connection.execute("PRAGMA table_info(context_rule_jobs)")}
    if "fact_snapshot_json" not in job_columns:
        connection.execute(
            """ALTER TABLE context_rule_jobs
               ADD COLUMN fact_snapshot_json TEXT NOT NULL DEFAULT '{}'"""
        )


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, sqlite3.Row):
        return item[name] if name in item.keys() else default
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return _datetime(value) if isinstance(value, str) else None


class ContextRuleService:
    def __init__(
        self,
        store: MemoryStore,
        behavior: object | None = None,
        timezone: str | ZoneInfo = "Asia/Shanghai",
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.behavior = behavior
        self.timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
        self.clock = clock
        with store._transaction(immediate=True) as connection:
            create_schema(connection)
            # A wall-clock interval cannot safely continue over a process restart.
            connection.execute(
                """UPDATE context_rule_state SET continuous_since=NULL,last_valid_at=NULL,
                   continuity_key=NULL,updated_at=?""",
                (_iso(self.clock()),),
            )

    @staticmethod
    def _validate(
        trigger: str,
        after_time: str | None,
        object_category: str | None,
        region: str,
        seated_minutes: int | None,
        message: str,
    ) -> str | None:
        if trigger not in TRIGGERS:
            return f"Unsupported trigger: {trigger}"
        if after_time is not None:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", after_time):
                return "after_time must be HH:MM"
        if object_category is not None and object_category not in CATEGORIES:
            return f"Unsupported category: {object_category}"
        if region not in REGIONS:
            return f"Unsupported region: {region}"
        if object_category is None and region != "any":
            return "region requires an object category"
        if trigger == "seated_duration":
            if (
                isinstance(seated_minutes, bool)
                or not isinstance(seated_minutes, int)
                or not 1 <= seated_minutes <= 1440
            ):
                return "seated_minutes must be an integer between 1 and 1440"
        elif seated_minutes is not None:
            return "seated_minutes is only valid for seated_duration"
        if not message.strip() or len(message) > 500:
            return "message must contain 1 to 500 characters"
        return None

    def create_rule(
        self,
        trigger: str,
        *,
        request_id: str,
        message: str,
        after_time: str | None = None,
        object_category: str | None = None,
        region: str = "any",
        seated_minutes: int | None = None,
        enabled: bool = True,
    ) -> ToolResult:
        error = self._validate(
            trigger, after_time, object_category, region, seated_minutes, message
        )
        if error or not request_id.strip():
            return ToolResult(ok=False, error=error or "request_id is required")
        if not isinstance(enabled, bool):
            return ToolResult(ok=False, error="enabled must be a boolean")
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            old = connection.execute(
                "SELECT rule_id, version FROM context_rule_versions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if old:
                return ToolResult(
                    ok=True,
                    data={"rule": self._rule(connection, old["rule_id"]), "deduplicated": True},
                )
            rule_id = uuid4().hex
            connection.execute(
                "INSERT INTO context_rules VALUES (?, 1, ?, 'active', ?, ?)",
                (rule_id, int(enabled), _iso(now), _iso(now)),
            )
            connection.execute(
                "INSERT INTO context_rule_versions VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    trigger,
                    after_time,
                    object_category,
                    region,
                    seated_minutes,
                    message.strip(),
                    _iso(now),
                    request_id,
                ),
            )
        return ToolResult(
            ok=True, data={"rule": self.get_rule(rule_id).data["rule"], "deduplicated": False}
        )

    @staticmethod
    def _rule(connection: sqlite3.Connection, rule_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT r.*, v.trigger, v.after_time, v.object_category, v.region,
                      v.seated_minutes, v.message, v.effective_at, v.request_id
               FROM context_rules r JOIN context_rule_versions v
                 ON v.rule_id=r.rule_id AND v.version=r.version WHERE r.rule_id=?""",
            (rule_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_rule(self, rule_id: str) -> ToolResult:
        with self.store._read_connection() as connection:
            rule = self._rule(connection, rule_id)
        return ToolResult(
            ok=rule is not None,
            data={"rule": rule} if rule else {},
            error=None if rule else "rule not found",
        )

    def list_rules(self) -> ToolResult:
        with self.store._read_connection() as connection:
            ids = connection.execute(
                "SELECT rule_id FROM context_rules ORDER BY created_at"
            ).fetchall()
            rules = [self._rule(connection, row[0]) for row in ids]
        return ToolResult(ok=True, data={"rules": rules})

    def update_rule(self, rule_id: str, *, request_id: str, **changes: Any) -> ToolResult:
        allowed = {
            "trigger",
            "after_time",
            "object_category",
            "region",
            "seated_minutes",
            "message",
            "enabled",
        }
        if not request_id.strip() or set(changes) - allowed:
            return ToolResult(ok=False, error="invalid update")
        if "enabled" in changes and not isinstance(changes["enabled"], bool):
            return ToolResult(ok=False, error="enabled must be a boolean")
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            duplicate = connection.execute(
                "SELECT rule_id FROM context_rule_versions WHERE request_id=?", (request_id,)
            ).fetchone()
            if duplicate:
                if duplicate[0] != rule_id:
                    return ToolResult(ok=False, error="request_id already used")
                return ToolResult(
                    ok=True, data={"rule": self._rule(connection, rule_id), "deduplicated": True}
                )
            current = self._rule(connection, rule_id)
            if current is None or current["status"] == "cancelled":
                return ToolResult(ok=False, error="rule not found or cancelled")
            values = {
                key: current[key]
                for key in (
                    "trigger",
                    "after_time",
                    "object_category",
                    "region",
                    "seated_minutes",
                    "message",
                )
            }
            values.update({key: value for key, value in changes.items() if key != "enabled"})
            error = self._validate(**values)
            if error:
                return ToolResult(ok=False, error=error)
            version = current["version"] + 1
            enabled = int(changes.get("enabled", bool(current["enabled"])))
            connection.execute(
                "UPDATE context_rules SET version=?, enabled=?, updated_at=? WHERE rule_id=?",
                (version, enabled, _iso(now), rule_id),
            )
            connection.execute(
                "INSERT INTO context_rule_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    version,
                    values["trigger"],
                    values["after_time"],
                    values["object_category"],
                    values["region"],
                    values["seated_minutes"],
                    values["message"],
                    _iso(now),
                    request_id,
                ),
            )
            connection.execute(
                """UPDATE context_rule_jobs SET status='superseded'
                   WHERE rule_id=? AND status IN ('queued','processing')""",
                (rule_id,),
            )
            connection.execute("DELETE FROM context_rule_state WHERE rule_id=?", (rule_id,))
        return ToolResult(
            ok=True, data={"rule": self.get_rule(rule_id).data["rule"], "deduplicated": False}
        )

    def cancel_rule(self, rule_id: str) -> ToolResult:
        with self.store._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT status FROM context_rules WHERE rule_id=?", (rule_id,)
            ).fetchone()
            if not row:
                return ToolResult(ok=False, error="rule not found")
            changed = row[0] != "cancelled"
            connection.execute(
                """UPDATE context_rules SET enabled=0,status='cancelled',updated_at=?
                   WHERE rule_id=?""",
                (_iso(self.clock()), rule_id),
            )
            connection.execute(
                """UPDATE context_rule_jobs SET status='superseded'
                   WHERE rule_id=? AND status IN ('queued','processing')""",
                (rule_id,),
            )
        return ToolResult(ok=True, data={"changed": changed})

    def _time_matches(self, after_time: str | None, event_at: datetime) -> bool:
        if after_time is None:
            return True
        local = event_at.astimezone(self.timezone)
        threshold = datetime.strptime(after_time, "%H:%M").time()
        return local.time().replace(tzinfo=None) > threshold

    def _stable_fact(
        self, connection: sqlite3.Connection, rule: sqlite3.Row, event: object, event_at: datetime
    ) -> dict[str, Any] | None:
        category = rule["object_category"]
        if category is None:
            return self._event_fact(event, event_at)
        source = _value(event, "source")
        scene_id = _value(event, "scene_id")
        processing_at = _value(event, "trigger_ingested_at", self.clock())
        if not isinstance(processing_at, datetime) or processing_at.tzinfo is None:
            return None
        supplied = _value(event, "object_facts")
        if supplied is not None:
            rows = sorted(
                list(supplied), key=lambda item: _as_datetime(_value(item, "observed_at"))
            )[-3:]
        else:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(observations)").fetchall()
            }
            if "scene_id" not in columns or scene_id is None:
                return None
            rows = connection.execute(
                """SELECT * FROM observations WHERE observed_at <= ? AND ingested_at <= ?
                   ORDER BY observation_id DESC LIMIT 3""",
                (_iso(event_at), _iso(processing_at)),
            ).fetchall()
        if len(rows) != 3:
            return None
        previous: datetime | None = None
        matched = []
        chronological = rows if supplied is not None else list(reversed(rows))
        for row in chronological:
            observed = _as_datetime(_value(row, "observed_at"))
            assert observed is not None
            ingested = _as_datetime(_value(row, "ingested_at"))
            if observed > event_at or (ingested is not None and ingested > processing_at):
                return None
            if (
                not bool(_value(row, "fresh"))
                or _value(row, "status") != "running"
                or not scene_id
                or _value(row, "scene_id") != scene_id
                or (source and _value(row, "source") != source)
            ):
                return None
            if previous and not (
                0 < (observed - previous).total_seconds() <= self.store.max_gap_seconds
            ):
                return None
            previous = observed
            raw_detections = _value(row, "detections", _value(row, "detections_json", []))
            detections = (
                json.loads(raw_detections) if isinstance(raw_detections, str) else raw_detections
            )
            candidates = [
                d
                for d in detections
                if d.get("category") == category
                and (rule["region"] == "any" or d.get("region") == rule["region"])
            ]
            if not candidates:
                return None
            matched.append(candidates)
        latest = rows[-1] if supplied is not None else rows[0]
        latest_at = _as_datetime(_value(latest, "observed_at"))
        assert latest_at is not None
        if not 0 <= (event_at - latest_at).total_seconds() <= self.store.max_gap_seconds:
            return None
        return {
            **self._event_fact(event, event_at),
            "fact_observed_at": _iso(latest_at),
            "evidence_id": _value(latest, "evidence_id") or _value(event, "evidence_id"),
            "detections": matched[-1],
            "object_facts": [dict(row) for row in rows] if supplied is not None else [],
        }

    @staticmethod
    def _event_fact(event: object, event_at: datetime) -> dict[str, Any]:
        return {
            "fact_observed_at": _iso(event_at),
            "evidence_id": _value(event, "evidence_id"),
            "behavior": {
                key: _value(event, key)
                for key in (
                    "kind",
                    "posture",
                    "drinking",
                    "scene_id",
                    "source",
                    "model_version",
                    "session_id",
                    "evidence_id",
                    "continuous_since",
                    "continuous_until",
                    "continuous_seated_seconds",
                    "threshold_minutes",
                )
            },
        }

    def process_event(self, event: object) -> list[dict[str, Any]]:
        kind = _value(event, "kind")
        event_at = _value(
            event, "confirmed_at", _value(event, "timestamp", _value(event, "observed_at"))
        )
        if (
            kind not in TRIGGERS - {"seated_duration"}
            or not isinstance(event_at, datetime)
            or event_at.tzinfo is None
            or event_at > self.clock()
        ):
            return []
        event_id = str(_value(event, "event_id", "") or f"{kind}:{_iso(event_at)}")
        jobs = []
        with self.store._transaction(immediate=True) as connection:
            rules = connection.execute(
                """SELECT r.enabled,r.status,r.version,v.* FROM context_rules r
                   JOIN context_rule_versions v ON v.rule_id=r.rule_id AND v.version=r.version
                   WHERE r.enabled=1 AND r.status='active' AND v.trigger=?
                     AND v.effective_at < ?""",
                (kind, _iso(event_at)),
            ).fetchall()
            for rule in rules:
                if not self._time_matches(rule["after_time"], event_at):
                    continue
                fact = self._stable_fact(connection, rule, event, event_at)
                if fact is None:
                    continue
                job = self._insert_job(connection, rule, event_id, kind, event_at, fact)
                if job:
                    jobs.append(job)
            if kind == "left_seat":
                connection.execute(
                    """UPDATE context_rule_state SET continuous_since=NULL,
                       last_valid_at=NULL,notified=0,crossed=0,
                       continuity_key=NULL,updated_at=?""",
                    (_iso(self.clock()),),
                )
        return jobs

    def process_observation(self, observation: object) -> list[dict[str, Any]]:
        observed_at = _value(observation, "observed_at", _value(observation, "timestamp"))
        posture = _value(observation, "posture", _value(observation, "state"))
        fresh = _value(observation, "fresh", True) is True
        status = _value(observation, "status", "running")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            return []
        processing_at = self.clock()
        if observed_at > processing_at:
            with self.store._transaction(immediate=True) as connection:
                connection.execute(
                    """UPDATE context_rule_state SET continuous_since=NULL,
                       last_valid_at=NULL,continuity_key=NULL,updated_at=?
                       WHERE continuous_since IS NOT NULL OR last_valid_at IS NOT NULL""",
                    (_iso(processing_at),),
                )
            return []
        valid = fresh and status == "running" and posture in {"seated", "seat_occupied"}
        continuity_key = json.dumps(
            [
                _value(observation, "scene_id"),
                _value(observation, "source"),
                _value(observation, "model_version"),
                _value(observation, "session_id"),
            ],
            separators=(",", ":"),
        )
        jobs = []
        with self.store._transaction(immediate=True) as connection:
            rules = connection.execute(
                """SELECT r.enabled,r.status,r.version,v.* FROM context_rules r
                   JOIN context_rule_versions v ON v.rule_id=r.rule_id AND v.version=r.version
                   WHERE r.enabled=1 AND r.status='active' AND v.trigger='seated_duration'"""
            ).fetchall()
            for rule in rules:
                state = connection.execute(
                    "SELECT * FROM context_rule_state WHERE rule_id=?", (rule["rule_id"],)
                ).fetchone()
                since = _datetime(state["continuous_since"]) if state else None
                notified = bool(state["notified"]) if state else False
                crossed = bool(state["crossed"]) if state else False
                last = _datetime(state["last_valid_at"]) if state else None
                old_key = state["continuity_key"] if state else None
                confirmed_empty = fresh and status == "running" and posture == "empty"
                if confirmed_empty:
                    since, last, notified, crossed, old_key = None, None, False, False, None
                elif (
                    not valid
                    or (last and not 0 < (observed_at - last).total_seconds() <= 0.25)
                    or (old_key is not None and old_key != continuity_key)
                ):
                    since, last = None, None
                elif last is not None and observed_at <= last:
                    # Duplicate/out-of-order samples cannot extend or rewrite an interval.
                    continue
                elif since is None:
                    since = observed_at
                    last = observed_at
                else:
                    last = observed_at
                just_crossed = bool(
                    since
                    and not crossed
                    and (observed_at - since).total_seconds() >= rule["seated_minutes"] * 60
                )
                if just_crossed:
                    crossed = True
                connection.execute(
                    """INSERT INTO context_rule_state(
                           rule_id,continuous_since,last_valid_at,notified,crossed,
                           continuity_key,updated_at
                       ) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(rule_id) DO UPDATE SET
                           continuous_since=excluded.continuous_since,
                           last_valid_at=excluded.last_valid_at,
                           notified=excluded.notified,crossed=excluded.crossed,
                           continuity_key=excluded.continuity_key,
                           updated_at=excluded.updated_at""",
                    (
                        rule["rule_id"],
                        _iso(since) if since else None,
                        _iso(last) if last else None,
                        int(notified),
                        int(crossed),
                        continuity_key if valid else old_key,
                        _iso(self.clock()),
                    ),
                )
                if (
                    just_crossed
                    and not notified
                    and self._time_matches(rule["after_time"], observed_at)
                ):
                    event_id = f"seated-duration:{rule['rule_id']}:{_iso(since)}"
                    duration_event = {
                        key: _value(observation, key)
                        for key in (
                            "posture",
                            "drinking",
                            "scene_id",
                            "source",
                            "model_version",
                            "session_id",
                            "evidence_id",
                        )
                    }
                    duration_event.update(
                        {
                            "kind": "seated_duration",
                            "continuous_since": since,
                            "continuous_until": observed_at,
                            "continuous_seated_seconds": (observed_at - since).total_seconds(),
                            "threshold_minutes": rule["seated_minutes"],
                        }
                    )
                    fact = self._stable_fact(connection, rule, duration_event, observed_at)
                    if fact is not None:
                        job = self._insert_job(
                            connection, rule, event_id, "seated_duration", observed_at, fact
                        )
                        if job:
                            jobs.append(job)
                            connection.execute(
                                "UPDATE context_rule_state SET notified=1 WHERE rule_id=?",
                                (rule["rule_id"],),
                            )
        return jobs

    def _insert_job(
        self,
        connection: sqlite3.Connection,
        rule: sqlite3.Row,
        event_id: str,
        kind: str,
        event_at: datetime,
        fact: dict[str, Any],
    ) -> dict[str, Any] | None:
        job_id = uuid4().hex
        snapshot = {
            key: rule[key]
            for key in (
                "trigger",
                "after_time",
                "object_category",
                "region",
                "seated_minutes",
                "message",
            )
        }
        changed = connection.execute(
            """INSERT OR IGNORE INTO context_rule_jobs(
                   job_id,rule_id,rule_version,event_id,event_kind,event_at,
                   fact_observed_at,evidence_id,trigger_snapshot_json,fact_snapshot_json,
                   status,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?)""",
            (
                job_id,
                rule["rule_id"],
                rule["version"],
                event_id,
                kind,
                _iso(event_at),
                fact.get("fact_observed_at"),
                fact.get("evidence_id"),
                json.dumps(snapshot, separators=(",", ":")),
                json.dumps(fact, separators=(",", ":"), default=str),
                _iso(self.clock()),
            ),
        ).rowcount
        if not changed:
            return None
        return {
            "job_id": job_id,
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "event_id": event_id,
            "event_kind": kind,
            "event_at": _iso(event_at),
            "fact_observed_at": fact.get("fact_observed_at"),
            "evidence_id": fact.get("evidence_id"),
            "message": rule["message"],
            "status": "queued",
        }

    def get_job(self, rule_id: str, event_id: str) -> ToolResult:
        with self.store._read_connection() as connection:
            row = connection.execute(
                """SELECT j.*,r.version AS active_version,r.enabled,
                          r.status AS rule_status
                   FROM context_rule_jobs j JOIN context_rules r ON r.rule_id=j.rule_id
                   WHERE j.rule_id=? AND j.event_id=?
                   ORDER BY rule_version DESC LIMIT 1""",
                (rule_id, event_id),
            ).fetchone()
        if not row:
            return ToolResult(ok=False, error="rule event not found")
        item = dict(row)
        active = (
            item["rule_version"] == item.pop("active_version")
            and bool(item.pop("enabled"))
            and item.pop("rule_status") == "active"
            and item["status"] in {"queued", "processing", "completed"}
        )
        item["trigger_snapshot"] = json.loads(item.pop("trigger_snapshot_json"))
        item["fact_snapshot"] = json.loads(item.pop("fact_snapshot_json"))
        item["active"] = active
        return ToolResult(ok=True, data={"events": [item] if active else []})

    def claim_job(self, rule_id: str, version: int, event_id: str) -> ToolResult:
        """Atomically grant one worker ownership of a queued current-version job."""
        with self.store._transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT j.*,r.version AS active_version,r.enabled,
                          r.status AS rule_status,v.message
                   FROM context_rule_jobs j
                   JOIN context_rules r ON r.rule_id=j.rule_id
                   JOIN context_rule_versions v
                     ON v.rule_id=j.rule_id AND v.version=j.rule_version
                   WHERE j.rule_id=? AND j.rule_version=? AND j.event_id=?""",
                (rule_id, version, event_id),
            ).fetchone()
            if (
                row is None
                or row["active_version"] != version
                or not row["enabled"]
                or row["rule_status"] != "active"
            ):
                return ToolResult(ok=False, error="rule job is no longer active")
            changed = connection.execute(
                """UPDATE context_rule_jobs SET status='processing'
                   WHERE job_id=? AND status='queued'""",
                (row["job_id"],),
            ).rowcount
            if not changed:
                return ToolResult(
                    ok=False,
                    data={"claimed": False, "status": row["status"]},
                    error="rule job is not queued",
                )
            job = dict(row)
            for key in ("active_version", "enabled", "rule_status"):
                job.pop(key, None)
            job["status"] = "processing"
            job["trigger_snapshot"] = json.loads(job.pop("trigger_snapshot_json"))
            job["fact_snapshot"] = json.loads(job.pop("fact_snapshot_json"))
        return ToolResult(ok=True, data={"claimed": True, "job": job})

    def fallback(self, job: Mapping[str, Any]) -> ToolResult:
        rule_id = str(job.get("rule_id", ""))
        event_id = str(job.get("event_id", ""))
        version = job.get("rule_version")
        with self.store._read_connection() as connection:
            row = connection.execute(
                """SELECT j.event_at,j.evidence_id,v.message
                   FROM context_rule_jobs j JOIN context_rule_versions v
                     ON v.rule_id=j.rule_id AND v.version=j.rule_version
                   WHERE j.rule_id=? AND j.rule_version=? AND j.event_id=?""",
                (rule_id, version, event_id),
            ).fetchone()
        if row is None or not isinstance(version, int):
            return ToolResult(ok=False, error="rule job not found")
        evidence = row["evidence_id"] or "无图像证据"
        message = f"{row['message']}（触发时间：{row['event_at']}；证据：{evidence}）"
        return self.notify(rule_id, version, event_id, message, source="fallback")

    def notify(
        self,
        rule_id: str,
        version: int,
        event_id: str,
        message: str,
        source: str = "agent",
    ) -> ToolResult:
        if not message.strip():
            return ToolResult(ok=False, error="message is required")
        with self.store._transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT j.*,r.version AS active_version,r.enabled,r.status AS rule_status
                   FROM context_rule_jobs j JOIN context_rules r ON r.rule_id=j.rule_id
                   WHERE j.rule_id=? AND j.rule_version=? AND j.event_id=?""",
                (rule_id, version, event_id),
            ).fetchone()
            if (
                not row
                or row["active_version"] != version
                or not row["enabled"]
                or row["rule_status"] != "active"
                or row["status"] == "superseded"
            ):
                return ToolResult(ok=False, error="rule job is no longer active")
            old = connection.execute(
                "SELECT * FROM context_rule_notifications WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            if old:
                return ToolResult(ok=True, data={"notification": dict(old), "deduplicated": True})
            notification_id = uuid4().hex
            connection.execute(
                "INSERT INTO context_rule_notifications VALUES (?,?,?,?,?)",
                (notification_id, row["job_id"], message.strip(), source, _iso(self.clock())),
            )
            connection.execute(
                "UPDATE context_rule_jobs SET status='completed' WHERE job_id=?", (row["job_id"],)
            )
        return ToolResult(
            ok=True,
            data={
                "notification": {
                    "notification_id": notification_id,
                    "job_id": row["job_id"],
                    "message": message.strip(),
                    "source": source,
                },
                "deduplicated": False,
            },
        )

    def list_notifications(self, limit: int = 100) -> ToolResult:
        if not 1 <= limit <= 100:
            return ToolResult(ok=False, error="limit must be between 1 and 100")
        with self.store._read_connection() as connection:
            rows = connection.execute(
                """SELECT n.notification_id,n.created_at,n.source,n.message,
                          j.event_id,j.evidence_id,j.rule_id,j.rule_version
                   FROM context_rule_notifications n
                   JOIN context_rule_jobs j ON j.job_id=n.job_id
                   ORDER BY n.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return ToolResult(ok=True, data={"notifications": [dict(row) for row in rows]})

    def recover(self) -> list[dict[str, Any]]:
        with self.store._transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE context_rule_jobs SET status='completed'
                   WHERE EXISTS (
                       SELECT 1 FROM context_rule_notifications n
                       WHERE n.job_id=context_rule_jobs.job_id
                   )"""
            )
            connection.execute(
                """UPDATE context_rule_jobs SET status='superseded'
                   WHERE status IN ('queued','processing') AND NOT EXISTS (
                       SELECT 1 FROM context_rules r
                       WHERE r.rule_id=context_rule_jobs.rule_id
                         AND r.version=context_rule_jobs.rule_version
                         AND r.enabled=1 AND r.status='active'
                   )"""
            )
            connection.execute(
                """UPDATE context_rule_jobs SET status='queued'
                   WHERE status='processing' AND EXISTS (
                       SELECT 1 FROM context_rules r
                       WHERE r.rule_id=context_rule_jobs.rule_id
                         AND r.version=context_rule_jobs.rule_version
                         AND r.enabled=1 AND r.status='active'
                   )"""
            )
            rows = connection.execute(
                """SELECT j.*,v.message FROM context_rule_jobs j
                   JOIN context_rule_versions v
                     ON v.rule_id=j.rule_id AND v.version=j.rule_version
                   WHERE j.status IN ('queued','processing') ORDER BY j.created_at"""
            ).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job["trigger_snapshot"] = json.loads(job.pop("trigger_snapshot_json"))
            job["fact_snapshot"] = json.loads(job.pop("fact_snapshot_json"))
            jobs.append(job)
        return jobs
