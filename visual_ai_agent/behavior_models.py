"""Validated records exchanged by behavior inference, storage, and agent tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BehaviorPosture = Literal["seated", "standing", "empty", "unknown"]
BehaviorEventKind = Literal["stood_up", "sat_down", "left_seat", "seat_occupied", "suspected_drink"]
BehaviorSource = Literal["camera", "replay", "test"]
BehaviorStatus = Literal["running", "stopped", "paused", "disconnected", "error", "stale"]
LaptopLidState = Literal["open", "closed", "unknown"]
LaptopEventKind = Literal["laptop_closed", "laptop_opened"]


class BehaviorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return value


class BehaviorEvent(BehaviorRecord):
    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    kind: BehaviorEventKind
    observed_at: datetime
    confirmed_at: datetime
    evidence_id: str | None = None
    model_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source: BehaviorSource = "camera"


class BehaviorObservation(BehaviorRecord):
    observed_at: datetime
    monotonic_at: float = Field(ge=0)
    status: BehaviorStatus
    fresh: bool
    posture: BehaviorPosture = "unknown"
    drinking: bool | None = None
    events: list[BehaviorEvent] = Field(default_factory=list)
    evidence_id: str | None = None
    model_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source: BehaviorSource = "camera"
    error: str | None = None


class LaptopEvent(BehaviorRecord):
    """One confirmed lid transition, separate from posture-duration facts."""

    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    kind: LaptopEventKind
    state: Literal["open", "closed"]
    observed_at: datetime
    confirmed_at: datetime
    evidence_id: str | None = None
    model_version: str = Field(min_length=1)
    presence_model_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source: BehaviorSource = "camera"

    @model_validator(mode="after")
    def event_matches_state(self):
        expected = "closed" if self.kind == "laptop_closed" else "open"
        if self.state != expected:
            raise ValueError("laptop event kind and state differ")
        return self


class LaptopObservation(BehaviorRecord):
    """Current lid fact with explicit, independently established presence evidence."""

    observed_at: datetime
    monotonic_at: float = Field(ge=0)
    status: BehaviorStatus
    fresh: bool
    presence_verified: bool = False
    presence_confidence: float | None = Field(default=None, ge=0, le=1)
    occluded: bool = False
    state: LaptopLidState = "unknown"
    state_reason: str | None = None
    events: list[LaptopEvent] = Field(default_factory=list)
    evidence_id: str | None = None
    model_version: str = Field(min_length=1)
    presence_model_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source: BehaviorSource = "camera"
    error: str | None = None

    @model_validator(mode="after")
    def unsafe_state_requires_current_presence(self):
        if self.state != "unknown" and (
            not self.presence_verified
            or self.occluded
            or not self.fresh
            or self.status != "running"
        ):
            raise ValueError("open/closed state requires fresh, unoccluded laptop presence")
        return self
