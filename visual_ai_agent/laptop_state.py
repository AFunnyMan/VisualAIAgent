"""Local, evidence-bounded lid transitions for experimental laptop classifiers.

No detector, device sleep signal, or default product feature is implied here.
``partial`` requires positive visual evidence; uncertain predictions are unknown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LaptopState:
    state: str
    event: str | None
    reason: str | None


class LaptopTimeline:
    def __init__(
        self,
        *,
        confirm_seconds: float = 0.5,
        max_gap: float = 0.25,
        max_transition_seconds: float = 3.0,
    ) -> None:
        if not all(
            math.isfinite(x) and x > 0
            for x in (
                confirm_seconds,
                max_gap,
                max_transition_seconds,
            )
        ):
            raise ValueError("Positive finite timing parameters required")
        self.confirm_seconds = confirm_seconds
        self.max_gap = max_gap
        self.max_transition_seconds = max_transition_seconds
        self.reset()

    def reset(self) -> None:
        self.last_timestamp: float | None = None
        self.scene_id: str | None = None
        self.confirmed = "unknown"
        self.candidate: str | None = None
        self.since = 0.0
        self.samples = 0
        self.partial_since: float | None = None

    def observe(
        self,
        timestamp: float,
        label: str,
        *,
        fresh: bool = True,
        scene_id: str = "default",
    ) -> LaptopState:
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid timestamp")
        if label not in {"open", "closed", "partial", "unknown"}:
            raise ValueError("Invalid laptop state")
        if not scene_id:
            raise ValueError("Scene id is required")
        fault = self.last_timestamp is not None and (
            timestamp <= self.last_timestamp
            or timestamp - self.last_timestamp > self.max_gap + 1e-9
        )
        changed = self.scene_id is not None and scene_id != self.scene_id
        if fault or changed or not fresh or label == "unknown":
            self.reset()
            self.last_timestamp, self.scene_id = timestamp, scene_id
            return LaptopState("unknown", None, "discontinuity" if fault or changed else "unknown")
        self.last_timestamp, self.scene_id = timestamp, scene_id
        if label == "partial":
            self.candidate, self.samples = None, 0
            if self.partial_since is None:
                self.partial_since = timestamp
            if timestamp - self.partial_since > self.max_transition_seconds:
                self.confirmed = "unknown"
            return LaptopState("partial", None, None)
        if self.partial_since is not None:
            if timestamp - self.partial_since > self.max_transition_seconds:
                self.confirmed = "unknown"
        if label != self.candidate:
            self.candidate, self.since, self.samples = label, timestamp, 1
        else:
            self.samples += 1
        if self.samples < 2 or timestamp - self.since + 1e-9 < self.confirm_seconds:
            return LaptopState("unknown", None, "confirming")
        previous = self.confirmed
        self.confirmed, self.partial_since = label, None
        event = {
            ("open", "closed"): "laptop_closed",
            ("closed", "open"): "laptop_opened",
        }.get((previous, label))
        return LaptopState(label, event, None)
