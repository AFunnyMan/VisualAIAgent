"""Durable laptop lid facts, kept separate from posture-duration accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from .behavior_models import LaptopEvent, LaptopObservation
from .memory import MemoryStore, _datetime, _iso
from .models import ToolResult


class LaptopStore:
    def __init__(self, store: MemoryStore, *, max_interval_seconds: float = 0.25) -> None:
        if max_interval_seconds <= 0:
            raise ValueError("max_interval_seconds must be positive")
        self.store = store
        self.max_interval_seconds = max_interval_seconds
        self._lock = RLock()
        self._has_session_observation = False
        self._confirmed_state: str | None = None
        self._visible_uncertain_bridge = False
        self._configured = False
        self._available = False
        self._capability_reason = "笔记本开合实验未启用。"

    def set_capability(self, *, configured: bool, available: bool, reason: str) -> None:
        with self._lock:
            self._configured = bool(configured)
            self._available = bool(available)
            self._capability_reason = reason.strip() or "笔记本开合能力状态未知。"

    def ingest(
        self, observation: LaptopObservation, jpeg: bytes | None = None
    ) -> list[LaptopEvent]:
        for event in observation.events:
            if event.scene_id != observation.scene_id:
                raise ValueError("event scene_id must match observation")
            if event.model_version != observation.model_version:
                raise ValueError("event model_version must match observation")
            if event.presence_model_version != observation.presence_model_version:
                raise ValueError("event presence model must match observation")
            if event.state != observation.state:
                raise ValueError("event state must match observation")
            if event.source != observation.source:
                raise ValueError("event source must match observation")
            if (
                event.observed_at > event.confirmed_at
                or event.confirmed_at > observation.observed_at
            ):
                raise ValueError("invalid laptop event timestamp")
        with self._lock:
            return self._ingest_locked(observation, jpeg)

    def _ingest_locked(
        self, observation: LaptopObservation, jpeg: bytes | None
    ) -> list[LaptopEvent]:
        evidence = self.store._write_evidence(jpeg if observation.events else None)
        evidence_id = evidence[0] if evidence else None
        obsolete_path: Path | None = None
        persisted: list[LaptopEvent] = []
        now = self.store.clock()
        observed_utc = observation.observed_at.astimezone(UTC)
        future = observed_utc > now.astimezone(UTC) and observation.source != "replay"
        adopted = False
        next_confirmed_state = self._confirmed_state
        next_visible_uncertain_bridge = self._visible_uncertain_bridge
        try:
            with self.store._transaction(immediate=True) as connection:
                previous = connection.execute(
                    "SELECT * FROM laptop_observations WHERE singleton_id=1"
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
                    and previous["presence_model_version"] == observation.presence_model_version
                    and previous["source"] == observation.source
                )
                adjacent = bool(
                    self._has_session_observation
                    and same_stream
                    and wall_delta is not None
                    and mono_delta is not None
                    and 0 < wall_delta <= self.max_interval_seconds
                    and 0 < mono_delta <= self.max_interval_seconds
                )
                current_valid = bool(
                    not future
                    and observation.status == "running"
                    and observation.fresh
                    and observation.presence_verified
                    and not observation.occluded
                )
                if evidence:
                    connection.execute(
                        "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                        (evidence[0], evidence[1], _iso(now), evidence[2], evidence[3]),
                    )
                visible_uncertain_bridge = bool(
                    observation.state_reason == "visible_transition_uncertain"
                    and adjacent
                    and self._confirmed_state in {"open", "closed"}
                    and not self._visible_uncertain_bridge
                )
                chain_valid = bool(
                    current_valid
                    and (
                        observation.state in {"open", "closed"}
                        or observation.state_reason == "confirming"
                        or visible_uncertain_bridge
                    )
                )
                working_confirmed_state = self._confirmed_state
                if not adjacent or not chain_valid:
                    working_confirmed_state = None
                expected_kind = None
                if (
                    working_confirmed_state in {"open", "closed"}
                    and observation.state in {"open", "closed"}
                    and working_confirmed_state != observation.state
                ):
                    expected_kind = (
                        "laptop_closed" if observation.state == "closed" else "laptop_opened"
                    )
                safe_events = (
                    [
                        event
                        for event in observation.events
                        if previous_at is not None
                        and event.confirmed_at.astimezone(UTC) > previous_at.astimezone(UTC)
                        and event.kind == expected_kind
                        and bool(event.evidence_id or evidence_id)
                    ]
                    if adjacent and chain_valid and expected_kind
                    else []
                )
                for event in safe_events:
                    event_evidence = event.evidence_id or evidence_id
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO laptop_events(
                               event_id,kind,observed_at,confirmed_at,evidence_id,state,
                               model_version,presence_model_version,scene_id,source
                           ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event.event_id,
                            event.kind,
                            _iso(event.observed_at),
                            _iso(event.confirmed_at),
                            event_evidence,
                            event.state,
                            event.model_version,
                            event.presence_model_version,
                            event.scene_id,
                            event.source,
                        ),
                    )
                    if cursor.rowcount:
                        persisted.append(event.model_copy(update={"evidence_id": event_evidence}))
                if not future and (
                    previous_at is None or observed_utc > previous_at.astimezone(UTC)
                ):
                    connection.execute(
                        """INSERT INTO laptop_observations(
                               singleton_id,observed_at,monotonic_at,status,fresh,state,state_reason,
                               presence_verified,presence_confidence,occluded,model_version,
                               presence_model_version,scene_id,source,error,ingested_at
                           ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(singleton_id) DO UPDATE SET
                               observed_at=excluded.observed_at,
                               monotonic_at=excluded.monotonic_at,
                               status=excluded.status,fresh=excluded.fresh,state=excluded.state,
                               state_reason=excluded.state_reason,
                               presence_verified=excluded.presence_verified,
                               presence_confidence=excluded.presence_confidence,
                               occluded=excluded.occluded,model_version=excluded.model_version,
                               presence_model_version=excluded.presence_model_version,
                               scene_id=excluded.scene_id,source=excluded.source,
                               error=excluded.error,ingested_at=excluded.ingested_at""",
                        (
                            _iso(observation.observed_at),
                            observation.monotonic_at,
                            observation.status,
                            int(observation.fresh),
                            observation.state,
                            observation.state_reason,
                            int(observation.presence_verified),
                            observation.presence_confidence,
                            int(observation.occluded),
                            observation.model_version,
                            observation.presence_model_version,
                            observation.scene_id,
                            observation.source,
                            observation.error,
                            _iso(now),
                        ),
                    )
                    adopted = True
                    if chain_valid and observation.state in {"open", "closed"}:
                        next_confirmed_state = observation.state
                        next_visible_uncertain_bridge = False
                    elif adjacent and chain_valid:
                        next_confirmed_state = working_confirmed_state
                        if visible_uncertain_bridge:
                            next_visible_uncertain_bridge = True
                    else:
                        next_confirmed_state = None
                        next_visible_uncertain_bridge = False
                evidence_used = any(event.evidence_id == evidence_id for event in persisted)
                if evidence and not evidence_used:
                    connection.execute("DELETE FROM evidence WHERE evidence_id=?", (evidence_id,))
                    obsolete_path = self.store.data_dir / evidence[1]
        except BaseException:
            if evidence:
                try:
                    (self.store.data_dir / evidence[1]).unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        if adopted:
            self._confirmed_state = next_confirmed_state
            self._visible_uncertain_bridge = next_visible_uncertain_bridge
            self._has_session_observation = True
        if obsolete_path:
            try:
                obsolete_path.unlink(missing_ok=True)
            except OSError:
                pass
        return persisted

    def current(self) -> ToolResult:
        with self._lock:
            configured = self._configured
            available = self._available
            reason = self._capability_reason
            session = self._has_session_observation
        with self.store._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM laptop_observations WHERE singleton_id=1"
            ).fetchone()
        if row is None:
            return ToolResult(
                ok=True,
                data={
                    "configured": configured,
                    "available": available,
                    "capability_reason": reason,
                    "current": False,
                    "state": "unknown",
                    "observation": None,
                },
            )
        observed_at = _datetime(row["observed_at"])
        assert observed_at is not None
        age = (self.store.clock().astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()
        current = bool(
            available
            and session
            and row["status"] == "running"
            and row["fresh"]
            and row["presence_verified"]
            and not row["occluded"]
            and row["state"] in {"open", "closed"}
            and 0 <= age <= self.max_interval_seconds
        )
        observation = {
            key: row[key]
            for key in (
                "observed_at",
                "status",
                "state",
                "state_reason",
                "presence_confidence",
                "model_version",
                "presence_model_version",
                "scene_id",
                "source",
                "error",
            )
        }
        observation.update(
            fresh=bool(row["fresh"]),
            presence_verified=bool(row["presence_verified"]),
            occluded=bool(row["occluded"]),
        )
        return ToolResult(
            ok=True,
            data={
                "configured": configured,
                "available": available,
                "capability_reason": reason,
                "current": current,
                "state": row["state"] if current else "unknown",
                "observation": observation,
                "age_seconds": max(0.0, age),
                "clock_skew": age < 0,
            },
        )

    def status(self) -> ToolResult:
        return self.current()

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
        allowed = {"laptop_closed", "laptop_opened"}
        if start >= end:
            return ToolResult(ok=False, error="start must be before end")
        if not 1 <= limit <= 100:
            return ToolResult(ok=False, error="limit must be between 1 and 100")
        if kinds is not None and (not kinds or any(kind not in allowed for kind in kinds)):
            return ToolResult(ok=False, error="unsupported laptop event kind")
        query = "SELECT * FROM laptop_events WHERE confirmed_at>=? AND confirmed_at<?"
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
    def _event_from_row(row: Any) -> LaptopEvent:
        return LaptopEvent(
            event_id=row["event_id"],
            kind=row["kind"],
            state=row["state"],
            observed_at=_datetime(row["observed_at"]),
            confirmed_at=_datetime(row["confirmed_at"]),
            evidence_id=row["evidence_id"],
            model_version=row["model_version"],
            presence_model_version=row["presence_model_version"],
            scene_id=row["scene_id"],
            source=row["source"],
        )
