"""Durable, bounded behavior history built on the application's SQLite store."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .behavior_models import BehaviorEvent, BehaviorObservation
from .memory import MemoryStore, _datetime, _iso
from .models import ToolResult


class BehaviorStore:
    """Persist confirmed behavior states/events without retaining frame history."""

    def __init__(
        self,
        store: MemoryStore,
        timezone: str = "Asia/Shanghai",
        *,
        max_interval_seconds: float = 0.25,
    ) -> None:
        if max_interval_seconds <= 0:
            raise ValueError("max_interval_seconds must be positive")
        self.store = store
        self.timezone = timezone
        self._zone = ZoneInfo(timezone)
        self.max_interval_seconds = max_interval_seconds
        self._lock = RLock()
        self._has_session_observation = False
        self._continuity_broken = False

    def ingest(
        self, observation: BehaviorObservation, jpeg: bytes | None = None
    ) -> list[BehaviorEvent]:
        """Store one snapshot and its already-confirmed events atomically."""
        for event in observation.events:
            if event.scene_id != observation.scene_id:
                raise ValueError("event scene_id must match observation")
            if event.model_version != observation.model_version:
                raise ValueError("event model_version must match observation")
            if event.source != observation.source:
                raise ValueError("event source must match observation")
            if event.confirmed_at > observation.observed_at:
                raise ValueError("event confirmed_at cannot be after observation")
            if event.observed_at > event.confirmed_at:
                raise ValueError("event observed_at cannot be after confirmed_at")

        with self._lock:
            return self._ingest_locked(observation, jpeg)

    def _ingest_locked(
        self, observation: BehaviorObservation, jpeg: bytes | None
    ) -> list[BehaviorEvent]:
        evidence = self.store._write_evidence(jpeg if observation.events else None)
        evidence_id = evidence[0] if evidence else None
        now = self.store.clock()
        now_utc = now.astimezone(UTC)
        observed_utc = observation.observed_at.astimezone(UTC)
        future = observed_utc > now_utc and observation.source != "replay"
        persisted: list[BehaviorEvent] = []
        obsolete_path: Path | None = None
        adopted_current = False
        try:
            with self.store._transaction(immediate=True) as connection:
                previous = connection.execute(
                    "SELECT * FROM behavior_observations WHERE singleton_id = 1"
                ).fetchone()
                previous_at = _datetime(previous["observed_at"]) if previous else None
                wall_delta = (
                    (observed_utc - previous_at.astimezone(UTC)).total_seconds()
                    if previous_at
                    else None
                )
                mono_delta = (
                    observation.monotonic_at - float(previous["monotonic_at"]) if previous else None
                )
                same_stream = bool(
                    previous
                    and previous["scene_id"] == observation.scene_id
                    and previous["model_version"] == observation.model_version
                    and previous["source"] == observation.source
                )
                previous_valid = bool(
                    previous and previous["status"] == "running" and previous["fresh"]
                )
                current_valid = observation.status == "running" and observation.fresh and not future
                ordered_same_session = bool(
                    self._has_session_observation
                    and not self._continuity_broken
                    and same_stream
                    and wall_delta is not None
                    and mono_delta is not None
                    and wall_delta > 0
                    and mono_delta > 0
                )
                adjacent = bool(
                    ordered_same_session
                    and 0 < wall_delta <= self.max_interval_seconds
                    and 0 < mono_delta <= self.max_interval_seconds
                )
                long_unknown = bool(
                    ordered_same_session
                    and wall_delta > self.max_interval_seconds
                    and mono_delta > self.max_interval_seconds
                    and abs(wall_delta - mono_delta) <= 0.05
                    and previous["status"] not in {"stopped", "paused"}
                    and observation.status not in {"stopped", "paused"}
                )
                state_continuous = bool(
                    adjacent
                    and (
                        (
                            previous_valid
                            and current_valid
                            and previous["posture"] == observation.posture
                        )
                        or (not previous_valid and not current_valid)
                    )
                )
                continuity_id = previous["continuity_id"] if state_continuous else uuid4().hex
                seated_seconds = 0.0
                last_valid_at = previous["last_valid_at"] if previous else None
                if adjacent or long_unknown:
                    interval_posture = (
                        observation.posture
                        if adjacent
                        and previous_valid
                        and current_valid
                        and previous["posture"] == observation.posture
                        else "unknown"
                    )
                    current_drinking = (
                        None if observation.drinking is None else int(observation.drinking)
                    )
                    interval_drinking = (
                        current_drinking
                        if adjacent
                        and previous_valid
                        and current_valid
                        and previous["drinking"] == current_drinking
                        else None
                    )
                    last_interval = connection.execute(
                        "SELECT * FROM behavior_intervals ORDER BY interval_id DESC LIMIT 1"
                    ).fetchone()
                    can_extend = bool(
                        last_interval
                        and last_interval["ended_at"] == previous["observed_at"]
                        and last_interval["posture"] == interval_posture
                        and last_interval["drinking"] == interval_drinking
                        and last_interval["model_version"] == observation.model_version
                        and last_interval["scene_id"] == observation.scene_id
                        and last_interval["continuity_id"] == continuity_id
                    )
                    if can_extend:
                        connection.execute(
                            """UPDATE behavior_intervals
                               SET ended_at=?, duration_seconds=duration_seconds+?
                               WHERE interval_id=?""",
                            (
                                _iso(observation.observed_at),
                                wall_delta,
                                last_interval["interval_id"],
                            ),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO behavior_intervals(
                                   started_at, ended_at, duration_seconds, posture, drinking,
                                   model_version, scene_id, continuity_id
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                previous["observed_at"],
                                _iso(observation.observed_at),
                                wall_delta,
                                interval_posture,
                                interval_drinking,
                                observation.model_version,
                                observation.scene_id,
                                continuity_id,
                            ),
                        )
                    if state_continuous and observation.posture == "seated":
                        seated_seconds = float(previous["continuous_seated_seconds"]) + wall_delta
                if current_valid:
                    last_valid_at = _iso(observation.observed_at)

                if evidence:
                    connection.execute(
                        "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                        (evidence[0], evidence[1], _iso(now), evidence[2], evidence[3]),
                    )
                safe_events = (
                    [
                        event
                        for event in observation.events
                        if previous_at is not None
                        and event.confirmed_at.astimezone(UTC) > previous_at.astimezone(UTC)
                    ]
                    if adjacent and previous_valid and current_valid
                    else []
                )
                for event in safe_events:
                    event_evidence = event.evidence_id or evidence_id
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO behavior_events(
                               event_id, kind, observed_at, confirmed_at, evidence_id,
                               model_version, scene_id, source
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id,
                            event.kind,
                            _iso(event.observed_at),
                            _iso(event.confirmed_at),
                            event_evidence,
                            event.model_version,
                            event.scene_id,
                            event.source,
                        ),
                    )
                    if cursor.rowcount:
                        persisted.append(event.model_copy(update={"evidence_id": event_evidence}))

                # Older or duplicate samples cannot roll current state backward.
                if not future and (
                    previous_at is None or observed_utc > previous_at.astimezone(UTC)
                ):
                    connection.execute(
                        """INSERT INTO behavior_observations(
                               singleton_id, observed_at, monotonic_at, status, fresh, posture,
                               drinking, model_version, scene_id, source, error, evidence_id,
                               continuity_id, continuous_seated_seconds, last_valid_at, ingested_at
                           ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                           ON CONFLICT(singleton_id) DO UPDATE SET
                               observed_at=excluded.observed_at,
                               monotonic_at=excluded.monotonic_at,
                               status=excluded.status,
                               fresh=excluded.fresh,
                               posture=excluded.posture,
                               drinking=excluded.drinking,
                               model_version=excluded.model_version,
                               scene_id=excluded.scene_id,
                               source=excluded.source,
                               error=excluded.error,
                               evidence_id=NULL,
                               continuity_id=excluded.continuity_id,
                               continuous_seated_seconds=excluded.continuous_seated_seconds,
                               last_valid_at=excluded.last_valid_at,
                               ingested_at=excluded.ingested_at""",
                        (
                            _iso(observation.observed_at),
                            observation.monotonic_at,
                            observation.status,
                            int(observation.fresh),
                            observation.posture,
                            None if observation.drinking is None else int(observation.drinking),
                            observation.model_version,
                            observation.scene_id,
                            observation.source,
                            observation.error,
                            continuity_id,
                            seated_seconds,
                            last_valid_at,
                            _iso(now),
                        ),
                    )
                    adopted_current = True
                evidence_used = any(event.evidence_id == evidence_id for event in persisted)
                if evidence and not evidence_used:
                    connection.execute("DELETE FROM evidence WHERE evidence_id = ?", (evidence_id,))
                    obsolete_path = self.store.data_dir / evidence[1]
        except BaseException:
            if evidence:
                (self.store.data_dir / evidence[1]).unlink(missing_ok=True)
            raise
        if obsolete_path:
            obsolete_path.unlink(missing_ok=True)
        if adopted_current:
            self._has_session_observation = True
            self._continuity_broken = False
        elif future or previous_at is not None:
            self._continuity_broken = True
        return persisted

    def current(self) -> ToolResult:
        with self.store._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM behavior_observations WHERE singleton_id = 1"
            ).fetchone()
        if row is None:
            return ToolResult(ok=True, data={"current": False, "observation": None})
        observed_at = _datetime(row["observed_at"])
        assert observed_at is not None
        age = (self.store.clock().astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()
        current = bool(
            self._has_session_observation
            and row["status"] == "running"
            and row["fresh"]
            and 0 <= age <= self.max_interval_seconds
        )
        observation = {
            "observed_at": row["observed_at"],
            "status": row["status"],
            "fresh": bool(row["fresh"]),
            "posture": row["posture"],
            "drinking": None if row["drinking"] is None else bool(row["drinking"]),
            "model_version": row["model_version"],
            "scene_id": row["scene_id"],
            "source": row["source"],
            "error": row["error"],
        }
        return ToolResult(
            ok=True,
            data={
                "current": current,
                "observation": observation,
                "posture": row["posture"] if current else "unknown",
                "drinking": (
                    bool(row["drinking"]) if current and row["drinking"] is not None else None
                ),
                "scene_id": row["scene_id"],
                "last_valid_at": row["last_valid_at"],
                "continuous_seated_seconds": (
                    float(row["continuous_seated_seconds"])
                    if current and row["posture"] == "seated"
                    else 0.0
                ),
                "continuity_id": row["continuity_id"],
                "age_seconds": max(0.0, age),
                "clock_skew": age < 0,
            },
        )

    def status(self) -> ToolResult:
        return self.current()

    def statistics(self, local_date: date | None = None) -> ToolResult:
        selected = local_date or self.store.clock().astimezone(self._zone).date()
        start = datetime.combine(selected, time.min, self._zone)
        end_date = date.fromordinal(selected.toordinal() + 1)
        end = datetime.combine(end_date, time.min, self._zone)
        start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
        totals = {name: 0.0 for name in ("seated", "standing", "empty", "unknown")}
        drinking_seconds = 0.0
        coverage_start: datetime | None = None
        coverage_end: datetime | None = None
        with self.store._read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM behavior_intervals
                   WHERE ended_at > ? AND started_at < ? ORDER BY started_at""",
                (_iso(start_utc), _iso(end_utc)),
            ).fetchall()
            event_rows = connection.execute(
                """SELECT kind, COUNT(*) AS count FROM behavior_events
                   WHERE confirmed_at >= ? AND confirmed_at < ? GROUP BY kind""",
                (_iso(start_utc), _iso(end_utc)),
            ).fetchall()
        for row in rows:
            row_start = max(_datetime(row["started_at"]), start_utc)
            row_end = min(_datetime(row["ended_at"]), end_utc)
            assert row_start is not None and row_end is not None
            seconds = max(0.0, (row_end - row_start).total_seconds())
            if row["posture"] in totals:
                totals[row["posture"]] += seconds
            if row["drinking"]:
                drinking_seconds += seconds
            coverage_start = row_start if coverage_start is None else min(coverage_start, row_start)
            coverage_end = row_end if coverage_end is None else max(coverage_end, row_end)
        data: dict[str, Any] = {
            "local_date": selected.isoformat(),
            "timezone": self.timezone,
            **{f"{key}_seconds": value for key, value in totals.items()},
            "drinking_seconds": drinking_seconds,
            "observed_seconds": sum(totals.values()),
            "coverage_start": _iso(coverage_start) if coverage_start else None,
            "coverage_end": _iso(coverage_end) if coverage_end else None,
            "event_counts": {row["kind"]: row["count"] for row in event_rows},
        }
        return ToolResult(ok=True, data=data)

    def search_events(
        self,
        start: datetime,
        end: datetime,
        kinds: list[str] | None = None,
        limit: int = 20,
    ) -> ToolResult:
        try:
            start_iso, end_iso = _iso(start), _iso(end)
        except ValueError as error:
            return ToolResult(ok=False, error=str(error))
        if start >= end:
            return ToolResult(ok=False, error="start must be before end")
        if not 1 <= limit <= 100:
            return ToolResult(ok=False, error="limit must be between 1 and 100")
        allowed = {"stood_up", "sat_down", "left_seat", "seat_occupied", "suspected_drink"}
        if kinds is not None and (not kinds or any(kind not in allowed for kind in kinds)):
            return ToolResult(ok=False, error="unsupported behavior event kind")
        query = "SELECT * FROM behavior_events WHERE confirmed_at >= ? AND confirmed_at < ?"
        parameters: list[Any] = [start_iso, end_iso]
        if kinds:
            query += f" AND kind IN ({','.join('?' for _ in kinds)})"
            parameters.extend(kinds)
        query += " ORDER BY confirmed_at DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.store._read_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        events = [self._event_from_row(row).model_dump(mode="json") for row in rows[:limit]]
        return ToolResult(ok=True, data={"events": events, "truncated": len(rows) > limit})

    @staticmethod
    def _event_from_row(row: Any) -> BehaviorEvent:
        return BehaviorEvent(
            event_id=row["event_id"],
            kind=row["kind"],
            observed_at=_datetime(row["observed_at"]),
            confirmed_at=_datetime(row["confirmed_at"]),
            evidence_id=row["evidence_id"],
            model_version=row["model_version"],
            scene_id=row["scene_id"],
            source=row["source"],
        )
