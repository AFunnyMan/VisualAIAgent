"""Experimental temporal interpretation, separate from production object events."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class StableLabel:
    value: str = "unknown"
    pending: str = "unknown"
    since: float = 0.0

    def observe(self, value: str, timestamp: float, seconds: float) -> tuple[str, str] | None:
        if value == "unknown":
            self.value = self.pending = "unknown"
            self.since = timestamp
            return None
        if value != self.pending:
            self.pending, self.since = value, timestamp
        if timestamp - self.since >= seconds and value != self.value:
            old, self.value = self.value, value
            return old, value
        return None


class BehaviorTimeline:
    """Only confirmed continuous observations may produce experimental events.

    Empty after seated produces left_seat, never stood_up without a standing
    observation. Unknown resets continuity, so gaps cannot create movements.
    """

    def __init__(self, confirm_seconds: float = 0.5, max_gap: float = 1.0):
        if not all(math.isfinite(v) and v > 0 for v in (confirm_seconds, max_gap)):
            raise ValueError("Positive finite temporal parameters required")
        self.confirm_seconds, self.max_gap = confirm_seconds, max_gap
        self.posture, self.drinking = StableLabel(), StableLabel()
        self.last_timestamp: float | None = None

    def observe(self, timestamp: float, posture: str, drinking: str, *, fresh: bool = True) -> dict:
        if not math.isfinite(timestamp):
            raise ValueError("Nonfinite timestamp")
        if posture not in {"seated", "standing", "empty", "unknown"}:
            raise ValueError("Invalid posture")
        if drinking not in {"drinking", "not_drinking", "unknown"}:
            raise ValueError("Invalid drinking state")
        gap = self.last_timestamp is not None and (
            timestamp <= self.last_timestamp or timestamp - self.last_timestamp > self.max_gap
        )
        if gap or not fresh:
            self.posture, self.drinking = StableLabel(), StableLabel()
        self.last_timestamp = timestamp
        if not fresh:
            posture = drinking = "unknown"
        # Contradictory or missing person evidence cannot confirm drinking.
        if posture in {"empty", "unknown"}:
            drinking = "unknown"
        transitions = self.posture.observe(posture, timestamp, self.confirm_seconds)
        drink_transition = self.drinking.observe(drinking, timestamp, self.confirm_seconds)
        events = []
        if transitions:
            old, new = transitions
            kind = {
                ("seated", "standing"): "stood_up",
                ("standing", "seated"): "sat_down",
                ("seated", "empty"): "left_seat",
                ("standing", "empty"): "left_seat",
                ("empty", "seated"): "seat_occupied",
            }.get((old, new))
            if kind:
                events.append({"kind": kind, "timestamp": timestamp})
        if drink_transition == ("not_drinking", "drinking"):
            events.append({"kind": "suspected_drink", "timestamp": timestamp})
        return {
            "posture": self.posture.value,
            "drinking": self.drinking.value,
            "events": events,
            "experimental": True,
        }
