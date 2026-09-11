"""Explicit environment configuration. Loading never opens devices or networks."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _strict_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    model_path: Path = Path("models/yolo26n-e2e.onnx")
    model_sha256: str = ""
    api_key: str = field(default="", repr=False)
    api_base_url: str = ""
    agent_model: str = ""
    api_mode: str = "responses"
    api_timeout_seconds: float = 30.0
    daily_auto_limit: int = 20
    timezone: str = "Asia/Shanghai"
    sample_interval: float = 1.0
    confidence: float = 0.35
    camera_index: int = 0
    camera_width: int = 640
    camera_height: int = 480
    observation_region: tuple[float, float, float, float] | None = None
    cup_scale_recheck: bool = False
    behavior_enabled: bool = False
    behavior_posture_manifest: Path | None = None
    behavior_drinking_manifest: Path | None = None
    behavior_person_model: Path | None = None
    behavior_model_version: str = "behavior-r03"
    behavior_scene_id: str = "default"
    behavior_seat_roi: tuple[float, float, float, float] = (0.2, 0.2, 0.95, 1.0)
    laptop_enabled: bool = False
    laptop_capability_manifest: Path | None = None
    laptop_model_version: str = "laptop-unvalidated"

    def __post_init__(self):
        ZoneInfo(self.timezone)
        if not isinstance(self.cup_scale_recheck, bool):
            raise ValueError("cup_scale_recheck must be a boolean")
        if not isinstance(self.behavior_enabled, bool):
            raise ValueError("behavior_enabled must be a boolean")
        if not isinstance(self.laptop_enabled, bool):
            raise ValueError("laptop_enabled must be a boolean")
        if not self.behavior_model_version.strip() or not self.behavior_scene_id.strip():
            raise ValueError("Behavior model version and scene id must be non-empty")
        if not self.laptop_model_version.strip():
            raise ValueError("Laptop model version must be non-empty")
        if self.api_mode not in ("responses", "chat_completions"):
            raise ValueError("VAA_API_MODE must be responses or chat_completions")
        if self.api_base_url:
            parsed = urlparse(self.api_base_url)
            if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username:
                raise ValueError("Invalid API base URL")
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Remote API base URL requires HTTPS")
        if not 1 <= self.api_timeout_seconds <= 120:
            raise ValueError("API timeout must be 1–120 seconds")
        if not 0 <= self.daily_auto_limit <= 1000:
            raise ValueError("Daily automatic limit must be 0–1000")
        if self.sample_interval not in (1.0, 2.0):
            raise ValueError("Sample interval must be 1 or 2 seconds")
        if not 0 < self.confidence < 1 or self.camera_index < 0:
            raise ValueError("Invalid confidence or camera index")
        if (self.camera_width, self.camera_height) not in {
            (640, 480),
            (1280, 720),
            (1920, 1080),
        }:
            raise ValueError("Camera resolution must be 640x480, 1280x720, or 1920x1080")
        region = self.observation_region
        if region is not None:
            if not isinstance(region, tuple) or len(region) != 4:
                raise ValueError("Observation region must contain four values")
            values = tuple(float(value) for value in region)
            left, top, right, bottom = values
            if not all(math.isfinite(value) for value in values) or not (
                0 <= left < right <= 1 and 0 <= top < bottom <= 1
            ):
                raise ValueError("Observation region must be finite and inside the frame")
            object.__setattr__(
                self,
                "observation_region",
                None if values == (0.0, 0.0, 1.0, 1.0) else values,
            )
        seat_roi = tuple(float(value) for value in self.behavior_seat_roi)
        if len(seat_roi) != 4 or not all(math.isfinite(value) for value in seat_roi):
            raise ValueError("Behavior seat ROI must contain four finite values")
        left, top, right, bottom = seat_roi
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("Behavior seat ROI must be a non-empty normalized rectangle")
        object.__setattr__(self, "behavior_seat_roi", seat_roi)

    @property
    def agent_connected(self) -> bool:
        return bool(self.api_key and self.api_base_url and self.agent_model)

    @classmethod
    def from_env(cls, *, dotenv_path: str | Path | None = ".env") -> "Config":
        if dotenv_path is not None:
            load_dotenv(dotenv_path, override=False)
        raw_region = os.getenv("VAA_OBSERVATION_REGION", "").strip()
        try:
            observation_region = (
                tuple(float(value.strip()) for value in raw_region.split(","))
                if raw_region
                else None
            )
        except ValueError as exc:
            raise ValueError("VAA_OBSERVATION_REGION must contain four numbers") from exc
        raw_seat_roi = os.getenv("VAA_BEHAVIOR_SEAT_ROI", "0.2,0.2,0.95,1.0")
        try:
            behavior_seat_roi = tuple(float(value.strip()) for value in raw_seat_roi.split(","))
        except ValueError as exc:
            raise ValueError("VAA_BEHAVIOR_SEAT_ROI must contain four numbers") from exc
        return cls(
            data_dir=Path(os.getenv("VAA_DATA_DIR", "data")),
            model_path=Path(os.getenv("VAA_MODEL_PATH", "models/yolo26n-e2e.onnx")),
            model_sha256=os.getenv("VAA_MODEL_SHA256", ""),
            api_key=os.getenv("VAA_API_KEY", ""),
            api_base_url=os.getenv("VAA_API_BASE_URL", ""),
            agent_model=os.getenv("VAA_AGENT_MODEL", ""),
            api_mode=os.getenv("VAA_API_MODE", "responses"),
            api_timeout_seconds=float(os.getenv("VAA_API_TIMEOUT_SECONDS", "30")),
            daily_auto_limit=int(os.getenv("VAA_DAILY_AUTO_LIMIT", "20")),
            timezone=os.getenv("VAA_TIMEZONE", "Asia/Shanghai"),
            sample_interval=float(os.getenv("VAA_SAMPLE_INTERVAL", "1")),
            confidence=float(os.getenv("VAA_CONFIDENCE", "0.35")),
            camera_index=int(os.getenv("VAA_CAMERA_INDEX", "0")),
            camera_width=int(os.getenv("VAA_CAMERA_WIDTH", "640")),
            camera_height=int(os.getenv("VAA_CAMERA_HEIGHT", "480")),
            observation_region=observation_region,  # type: ignore[arg-type]
            cup_scale_recheck=_strict_bool_env("VAA_CUP_SCALE_RECHECK", False),
            behavior_enabled=_strict_bool_env("VAA_BEHAVIOR_ENABLED", False),
            behavior_posture_manifest=(
                Path(value)
                if (value := os.getenv("VAA_BEHAVIOR_POSTURE_MANIFEST", "").strip())
                else None
            ),
            behavior_drinking_manifest=(
                Path(value)
                if (value := os.getenv("VAA_BEHAVIOR_DRINKING_MANIFEST", "").strip())
                else None
            ),
            behavior_person_model=(
                Path(value)
                if (value := os.getenv("VAA_BEHAVIOR_PERSON_MODEL", "").strip())
                else None
            ),
            behavior_model_version=os.getenv("VAA_BEHAVIOR_MODEL_VERSION", "behavior-r03"),
            behavior_scene_id=os.getenv("VAA_BEHAVIOR_SCENE_ID", "default"),
            behavior_seat_roi=behavior_seat_roi,  # type: ignore[arg-type]
            laptop_enabled=_strict_bool_env("VAA_LAPTOP_ENABLED", False),
            laptop_capability_manifest=(
                Path(value)
                if (value := os.getenv("VAA_LAPTOP_CAPABILITY_MANIFEST", "").strip())
                else None
            ),
            laptop_model_version=os.getenv("VAA_LAPTOP_MODEL_VERSION", "laptop-unvalidated"),
        )
