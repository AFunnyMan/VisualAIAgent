"""Persistent watch lifecycle, event claiming, and notification idempotency."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .memory import MemoryStore, _category, _datetime, _iso
from .models import Condition, ToolResult, VisualEvent, WatchTask, utcnow


def _condition(value: str) -> Condition:
    if value not in ("appeared", "missing"):
        raise ValueError(f"Unsupported condition: {value}")
    return value  # type: ignore[return-value]


class WatchService:
    def __init__(self, store: MemoryStore, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.clock = clock

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> WatchTask:
        return WatchTask(
            watch_id=row["watch_id"],
            category=row["category"],
            condition=row["condition"],
            created_at=_datetime(row["created_at"]),
            expires_at=_datetime(row["expires_at"]),
            status=row["status"],
            request_id=row["request_id"],
            event_id=row["event_id"],
        )

    def _expire(self, connection: sqlite3.Connection, now: datetime) -> None:
        connection.execute(
            """UPDATE watches SET status = 'expired'
               WHERE status IN ('waiting', 'processing') AND expires_at <= ?""",
            (_iso(now),),
        )

    def create_watch(
        self,
        category: str,
        condition: str,
        duration_minutes: int = 30,
        *,
        request_id: str,
    ) -> ToolResult:
        try:
            valid_category = _category(category)
            valid_condition = _condition(condition)
        except ValueError as error:
            return ToolResult(ok=False, error=str(error))
        if not request_id.strip():
            return ToolResult(ok=False, error="request_id is required")
        if duration_minutes <= 0:
            return ToolResult(ok=False, error="duration_minutes must be positive")
        now = self.clock()
        expires_at = now + timedelta(minutes=duration_minutes)
        with self.store._transaction(immediate=True) as connection:
            self._expire(connection, now)
            existing = connection.execute(
                "SELECT * FROM watches WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing:
                if (
                    existing["category"] != valid_category
                    or existing["condition"] != valid_condition
                ):
                    return ToolResult(ok=False, error="request_id already used for another watch")
                task = self._task_from_row(existing)
                return ToolResult(
                    ok=True,
                    data={"watch": task.model_dump(mode="json"), "deduplicated": True},
                )
            task = WatchTask(
                watch_id=uuid4().hex,
                category=valid_category,
                condition=valid_condition,
                created_at=now,
                expires_at=expires_at,
                status="waiting",
                request_id=request_id,
            )
            connection.execute(
                "INSERT INTO watches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.watch_id,
                    task.category,
                    task.condition,
                    _iso(task.created_at),
                    _iso(task.expires_at),
                    task.status,
                    task.request_id,
                    None,
                ),
            )
        return ToolResult(
            ok=True, data={"watch": task.model_dump(mode="json"), "deduplicated": False}
        )

    def list_watches(self) -> ToolResult:
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            self._expire(connection, now)
            rows = connection.execute("SELECT * FROM watches ORDER BY created_at DESC").fetchall()
        return ToolResult(
            ok=True,
            data={"watches": [self._task_from_row(row).model_dump(mode="json") for row in rows]},
        )

    def cancel_watch(self, watch_id: str) -> ToolResult:
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            self._expire(connection, now)
            row = connection.execute(
                "SELECT * FROM watches WHERE watch_id = ?", (watch_id,)
            ).fetchone()
            if row is None:
                return ToolResult(ok=False, error="watch not found")
            previous = row["status"]
            if previous in ("waiting", "processing"):
                connection.execute(
                    "UPDATE watches SET status = 'cancelled' WHERE watch_id = ?", (watch_id,)
                )
                row = connection.execute(
                    "SELECT * FROM watches WHERE watch_id = ?", (watch_id,)
                ).fetchone()
            return ToolResult(
                ok=True,
                data={
                    "watch": self._task_from_row(row).model_dump(mode="json"),
                    "changed": previous in ("waiting", "processing"),
                    "previous_status": previous,
                },
            )

    def match_event(self, event: VisualEvent) -> list[WatchTask]:
        """Atomically claim every eligible waiting watch for a persisted event."""
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            self._expire(connection, now)
            event_row = connection.execute(
                "SELECT * FROM visual_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if event_row is None:
                return []
            if event_row["category"] != event.category or event_row["kind"] != event.kind:
                return []
            if event.kind == "missing":
                if event_row["last_seen_at"] is None:
                    return []
                if event_row["last_seen_at"] > event_row["observed_at"]:
                    return []
            rows = connection.execute(
                """SELECT * FROM watches
                   WHERE status = 'waiting' AND category = ? AND condition = ?
                     AND created_at < ? AND expires_at > ? AND expires_at > ?
                   ORDER BY created_at ASC""",
                (
                    event.category,
                    event.kind,
                    event_row["confirmed_at"],
                    event_row["confirmed_at"],
                    _iso(now),
                ),
            ).fetchall()
            claimed: list[WatchTask] = []
            for row in rows:
                changed = connection.execute(
                    """UPDATE watches SET status = 'processing', event_id = ?
                       WHERE watch_id = ? AND status = 'waiting'""",
                    (event.event_id, row["watch_id"]),
                ).rowcount
                if changed:
                    updated = dict(row)
                    updated["status"] = "processing"
                    updated["event_id"] = event.event_id
                    claimed.append(self._task_from_row(updated))
        return claimed

    def notify_user(
        self,
        watch_id: str,
        event_id: str,
        message: str,
        source: str = "agent",
    ) -> ToolResult:
        if not message.strip():
            return ToolResult(ok=False, error="message is required")
        if not source.strip():
            return ToolResult(ok=False, error="source is required")
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM notifications WHERE watch_id = ? AND event_id = ?",
                (watch_id, event_id),
            ).fetchone()
            if existing:
                return ToolResult(
                    ok=True,
                    data={"notification": dict(existing), "deduplicated": True},
                )
            self._expire(connection, now)
            row = connection.execute(
                """SELECT w.*, e.kind AS event_kind, e.category AS event_category,
                          e.confirmed_at AS event_confirmed_at,
                          e.observed_at AS event_observed_at,
                          e.last_seen_at AS event_last_seen_at,
                          e.evidence_id AS event_evidence_id
                   FROM watches w JOIN visual_events e ON e.event_id = ?
                   WHERE w.watch_id = ?""",
                (event_id, watch_id),
            ).fetchone()
            if row is None:
                return ToolResult(ok=False, error="watch or event not found")
            if row["status"] != "processing" or row["event_id"] != event_id:
                return ToolResult(ok=False, error="watch is not processing this event")
            if row["category"] != row["event_category"] or row["condition"] != row["event_kind"]:
                return ToolResult(ok=False, error="event does not match watch")
            if not (row["created_at"] < row["event_confirmed_at"] < row["expires_at"]):
                return ToolResult(ok=False, error="event is outside the watch lifetime")
            if datetime.fromisoformat(row["expires_at"]) <= now.astimezone(UTC):
                return ToolResult(ok=False, error="watch has expired")
            if row["event_kind"] == "missing" and row["event_last_seen_at"] is None:
                return ToolResult(ok=False, error="missing event has no prior appearance fact")
            if (
                row["event_kind"] == "missing"
                and row["event_last_seen_at"] > row["event_observed_at"]
            ):
                return ToolResult(ok=False, error="missing event has invalid prior appearance time")
            notification_id = uuid4().hex
            connection.execute(
                """INSERT INTO notifications(
                    notification_id, watch_id, event_id, message, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (notification_id, watch_id, event_id, message, source, _iso(now)),
            )
            connection.execute(
                "UPDATE watches SET status = 'completed' WHERE watch_id = ?",
                (watch_id,),
            )
            notification = {
                "notification_id": notification_id,
                "watch_id": watch_id,
                "event_id": event_id,
                "message": message,
                "source": source,
                "created_at": _iso(now),
                "evidence_id": row["event_evidence_id"],
            }
        return ToolResult(ok=True, data={"notification": notification, "deduplicated": False})

    def list_notifications(self, limit: int = 100) -> ToolResult:
        if not 1 <= limit <= 100:
            return ToolResult(ok=False, error="limit must be between 1 and 100")
        with self.store._read_connection() as connection:
            rows = connection.execute(
                """SELECT n.*, e.evidence_id
                   FROM notifications n JOIN visual_events e ON e.event_id = n.event_id
                   ORDER BY n.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return ToolResult(ok=True, data={"notifications": [dict(row) for row in rows]})

    def recover(self) -> list[WatchTask]:
        """Reconcile finished notifications and return work that remains active."""
        now = self.clock()
        with self.store._transaction(immediate=True) as connection:
            self._expire(connection, now)
            connection.execute(
                """UPDATE watches SET status = 'completed'
                   WHERE status = 'processing' AND EXISTS (
                       SELECT 1 FROM notifications n
                       WHERE n.watch_id = watches.watch_id
                         AND n.event_id = watches.event_id
                   )"""
            )
            rows = connection.execute(
                """SELECT * FROM watches
                   WHERE status IN ('waiting', 'processing')
                   ORDER BY created_at ASC"""
            ).fetchall()
        return [self._task_from_row(row) for row in rows]
