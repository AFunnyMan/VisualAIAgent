"""Local, evidence-bounded lid transitions for experimental laptop classifiers.

No detector, device sleep signal, or default product feature is implied here.
Only fully shut lids are closed; visibly unclosed lids are open.
Uncertain predictions remain unknown. A single brief rejection can retain only
the prior state when a separate, accepted visibility model supplies evidence.
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
        visible_transition_bridge_seconds: float = 0.15,
    ) -> None:
        if not all(
            math.isfinite(x) and x > 0
            for x in (
                confirm_seconds,
                max_gap,
                visible_transition_bridge_seconds,
            )
        ):
            raise ValueError("Positive finite timing parameters required")
        if visible_transition_bridge_seconds > max_gap:
            raise ValueError("Visibility bridge cannot exceed maximum sample gap")
        self.confirm_seconds = confirm_seconds
        self.max_gap = max_gap
        self.visible_transition_bridge_seconds = visible_transition_bridge_seconds
        self.reset()

    def reset(self) -> None:
        self.last_timestamp: float | None = None
        self.scene_id: str | None = None
        self.confirmed = "unknown"
        self.candidate: str | None = None
        self.since = 0.0
        self.samples = 0
        self.last_visibility_verified = False
        self.uncertain_at: float | None = None
        self.visibility_bridge_used = False

    def observe(
        self,
        timestamp: float,
        label: str,
        *,
        fresh: bool = True,
        scene_id: str = "default",
        visibility_verified: bool = False,
    ) -> LaptopState:
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid timestamp")
        if label not in {"open", "closed", "unknown"}:
            raise ValueError("Invalid laptop state")
        if not scene_id:
            raise ValueError("Scene id is required")
        fault = self.last_timestamp is not None and (
            timestamp <= self.last_timestamp
            or timestamp - self.last_timestamp > self.max_gap + 1e-9
        )
        changed = self.scene_id is not None and scene_id != self.scene_id
        if fault or changed or not fresh:
            self.reset()
            self.last_timestamp, self.scene_id = timestamp, scene_id
            return LaptopState("unknown", None, "discontinuity" if fault or changed else "unknown")
        if label == "unknown":
            can_bridge = (
                visibility_verified is True
                and self.last_visibility_verified
                and self.confirmed in {"open", "closed"}
                and self.uncertain_at is None
                and not self.visibility_bridge_used
                and self.last_timestamp is not None
                and timestamp - self.last_timestamp
                <= self.visible_transition_bridge_seconds + 1e-9
            )
            if can_bridge:
                self.uncertain_at = timestamp
                self.visibility_bridge_used = True
                self.candidate, self.samples = None, 0
                self.last_timestamp, self.scene_id = timestamp, scene_id
                return LaptopState("unknown", None, "visible_transition_uncertain")
            self.reset()
            self.last_timestamp, self.scene_id = timestamp, scene_id
            return LaptopState("unknown", None, "unknown")
        if self.uncertain_at is not None:
            if (
                visibility_verified is not True
                or timestamp - self.uncertain_at
                > self.visible_transition_bridge_seconds + 1e-9
            ):
                self.reset()
            self.uncertain_at = None
        self.last_timestamp, self.scene_id = timestamp, scene_id
        self.last_visibility_verified = visibility_verified is True
        if label != self.candidate:
            self.candidate, self.since, self.samples = label, timestamp, 1
        else:
            self.samples += 1
        if self.samples < 2 or timestamp - self.since + 1e-9 < self.confirm_seconds:
            return LaptopState("unknown", None, "confirming")
        previous = self.confirmed
        self.confirmed = label
        self.visibility_bridge_used = False
        event = {
            ("open", "closed"): "laptop_closed",
            ("closed", "open"): "laptop_opened",
        }.get((previous, label))
        return LaptopState(label, event, None)
