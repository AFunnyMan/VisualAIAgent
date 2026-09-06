"""Explicit environment configuration. Loading never opens devices or networks."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    model_path: Path = Path("models/yolo26n.onnx")
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

    def __post_init__(self):
        ZoneInfo(self.timezone)
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

    @property
    def agent_connected(self) -> bool:
        return bool(self.api_key and self.api_base_url and self.agent_model)

    @classmethod
    def from_env(cls, *, dotenv_path: str | Path | None = ".env") -> "Config":
        if dotenv_path is not None:
            load_dotenv(dotenv_path, override=False)
        return cls(
            data_dir=Path(os.getenv("VAA_DATA_DIR", "data")),
            model_path=Path(os.getenv("VAA_MODEL_PATH", "models/yolo26n.onnx")),
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
        )
