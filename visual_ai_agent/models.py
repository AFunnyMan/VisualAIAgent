"""Shared validated domain records; no image bytes cross the agent boundary."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["cell phone", "cup", "bottle"]
Region = Literal["left", "center", "right"]
Condition = Literal["appeared", "missing"]
CameraStatus = Literal["running", "stopped", "paused", "disconnected", "error", "stale"]
CATEGORIES = ("cell phone", "cup", "bottle")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return value


class Detection(Record):
    category: Category
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    region: Region


class SceneObservation(Record):
    observed_at: datetime
    monotonic_at: float
    status: CameraStatus
    fresh: bool
    detections: list[Detection] = Field(default_factory=list)
    evidence_id: str | None = None
    error: str | None = None
    inference_ms: float | None = None
    width: int = 0
    height: int = 0
    source: Literal["camera", "replay", "test"] = "camera"


class VisualEvent(Record):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: Condition
    category: Category
    regions: list[Region] = Field(default_factory=list)
    observed_at: datetime
    confirmed_at: datetime
    evidence_id: str | None = None
    last_seen_at: datetime | None = None
    source: Literal["camera", "replay", "test"] = "camera"


class WatchTask(Record):
    watch_id: str
    category: Category
    condition: Condition
    created_at: datetime
    expires_at: datetime
    status: Literal["waiting", "processing", "completed", "cancelled", "expired"]
    request_id: str
    event_id: str | None = None


class ToolResult(Record):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
