"""Conservative v2 temporal interpretation for experimental behavior models.

This module stays separate from ``behavior_events`` so the r02 baseline remains
replayable.  ``continuous_visible`` is external evidence supplied by a caller;
fresh frames alone never bridge an unknown observation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class _Candidate:
    label: str | None = None
    since: float = 0.0
    samples: int = 0

    def clear(self) -> None:
        self.label = None
        self.since = 0.0
        self.samples = 0

    def add(self, label: str, timestamp: float) -> None:
        if label != self.label:
            self.label = label
            self.since = timestamp
            self.samples = 1
        else:
            self.samples += 1


class BehaviorTimelineV2:
    """Confirm posture and drinking on independent clocks.

    A short unknown may retain transition context only when the caller supplies
    continuous visibility evidence.  The current result is still ``unknown``
    during that interval, and recovery starts a new confirmation candidate.
    """

    POSTURES = frozenset({"seated", "standing", "empty", "unknown"})
    DRINKING_STATES = frozenset({"drinking", "not_drinking", "unknown"})
    _TIME_EPSILON = 1e-9

    def __init__(
        self,
        *,
        posture_confirm_seconds: float = 0.5,
        drinking_confirm_seconds: float = 0.1,
        drinking_min_samples: int = 2,
        drinking_end_seconds: float = 0.3,
        unknown_bridge_seconds: float = 1.0,
        max_gap: float = 1.0,
    ) -> None:
        durations = (
            posture_confirm_seconds,
            drinking_confirm_seconds,
            drinking_end_seconds,
            unknown_bridge_seconds,
            max_gap,
        )
        if not all(math.isfinite(value) and value > 0 for value in durations):
            raise ValueError("Positive finite temporal parameters required")
        if (
            isinstance(drinking_min_samples, bool)
            or not isinstance(drinking_min_samples, (int, float))
            or not math.isfinite(drinking_min_samples)
            or drinking_min_samples < 2
            or int(drinking_min_samples) != drinking_min_samples
        ):
            raise ValueError("drinking_min_samples must be an integer of at least two")

        self.posture_confirm_seconds = posture_confirm_seconds
        self.drinking_confirm_seconds = drinking_confirm_seconds
        self.drinking_min_samples = int(drinking_min_samples)
        self.drinking_end_seconds = drinking_end_seconds
        self.unknown_bridge_seconds = unknown_bridge_seconds
        self.max_gap = max_gap
        self.last_timestamp: float | None = None
        self._reset()

    def _reset(self) -> None:
        self._posture_context = "unknown"
        self._drinking_context = "unknown"
        self._posture_candidate = _Candidate()
        self._drinking_candidate = _Candidate()
        self._posture_unknown_since: float | None = None
        self._drinking_unknown_since: float | None = None

    @staticmethod
    def _event(old: str, new: str) -> str | None:
        return {
            ("seated", "standing"): "stood_up",
            ("standing", "seated"): "sat_down",
            ("seated", "empty"): "left_seat",
            ("standing", "empty"): "left_seat",
            ("empty", "seated"): "seat_occupied",
        }.get((old, new))

    def _unknown(
        self,
        task: str,
        timestamp: float,
        continuous_visible: bool,
    ) -> None:
        candidate: _Candidate = getattr(self, f"_{task}_candidate")
        candidate.clear()
        since_name = f"_{task}_unknown_since"
        since: float | None = getattr(self, since_name)
        if since is None:
            since = timestamp
            setattr(self, since_name, timestamp)
        if not continuous_visible or timestamp - since > self.unknown_bridge_seconds:
            setattr(self, f"_{task}_context", "unknown")

    def _prepare_recovery(
        self,
        task: str,
        timestamp: float,
        continuous_visible: bool,
    ) -> bool:
        """Return whether this sample is still awaiting recovery confirmation."""
        since: float | None = getattr(self, f"_{task}_unknown_since")
        if since is None:
            return False
        if not continuous_visible or timestamp - since > self.unknown_bridge_seconds:
            setattr(self, f"_{task}_context", "unknown")
        return True

    def _confirm_posture(self, label: str, timestamp: float) -> tuple[bool, str | None]:
        self._posture_candidate.add(label, timestamp)
        if (
            timestamp - self._posture_candidate.since + self._TIME_EPSILON
            < self.posture_confirm_seconds
        ):
            return False, None
        old = self._posture_context
        self._posture_context = label
        self._posture_candidate.clear()
        self._posture_unknown_since = None
        return True, self._event(old, label)

    def _confirm_drinking(self, label: str, timestamp: float) -> tuple[bool, bool]:
        self._drinking_candidate.add(label, timestamp)
        seconds = (
            self.drinking_confirm_seconds if label == "drinking" else self.drinking_end_seconds
        )
        enough_samples = (
            self._drinking_candidate.samples >= self.drinking_min_samples
            if label == "drinking"
            else True
        )
        if (
            timestamp - self._drinking_candidate.since + self._TIME_EPSILON < seconds
            or not enough_samples
        ):
            return False, False
        old = self._drinking_context
        self._drinking_context = label
        self._drinking_candidate.clear()
        self._drinking_unknown_since = None
        return True, old == "not_drinking" and label == "drinking"

    def observe(
        self,
        timestamp: float,
        posture: str,
        drinking: str,
        *,
        fresh: bool = True,
        continuous_visible: bool = False,
    ) -> dict:
        """Consume one paired observation and return confirmed current states.

        ``continuous_visible`` must come from explicit visibility evidence.  It
        has no effect on gaps or stale frames, which always reset all context.
        """
        if not math.isfinite(timestamp):
            raise ValueError("Nonfinite timestamp")
        if posture not in self.POSTURES:
            raise ValueError("Invalid posture")
        if drinking not in self.DRINKING_STATES:
            raise ValueError("Invalid drinking state")

        fault = self.last_timestamp is not None and (
            timestamp <= self.last_timestamp or timestamp - self.last_timestamp > self.max_gap
        )
        if fault or not fresh:
            self._reset()
        self.last_timestamp = timestamp

        unknown_reasons: dict[str, str] = {}
        if not fresh:
            posture = drinking = "unknown"
            unknown_reasons = {"posture": "not_fresh", "drinking": "not_fresh"}
        elif posture == "unknown":
            unknown_reasons["posture"] = "classifier_unknown"
        if posture in {"empty", "unknown"} and drinking != "unknown":
            drinking = "unknown"
            unknown_reasons["drinking"] = "no_confirmed_person"
        elif drinking == "unknown":
            unknown_reasons["drinking"] = "classifier_unknown"

        events: list[dict[str, float | str]] = []
        posture_recovering = False
        if posture == "unknown":
            self._unknown("posture", timestamp, continuous_visible and fresh and not fault)
        else:
            posture_recovering = self._prepare_recovery(
                "posture", timestamp, continuous_visible and fresh and not fault
            )
            posture_confirmed, event = self._confirm_posture(posture, timestamp)
            if event:
                events.append({"kind": event, "timestamp": timestamp})
            if posture_confirmed:
                posture_recovering = False

        drinking_recovering = False
        if drinking == "unknown":
            # Empty is definite evidence that drinking continuity is impossible.
            bridge_drinking = continuous_visible and posture != "empty" and fresh and not fault
            self._unknown("drinking", timestamp, bridge_drinking)
        else:
            drinking_recovering = self._prepare_recovery(
                "drinking", timestamp, continuous_visible and fresh and not fault
            )
            drinking_confirmed, drink_event = self._confirm_drinking(drinking, timestamp)
            if drink_event:
                events.append({"kind": "suspected_drink", "timestamp": timestamp})
            if drinking_confirmed:
                drinking_recovering = False

        result = {
            "posture": (
                "unknown" if posture == "unknown" or posture_recovering else self._posture_context
            ),
            "drinking": (
                "unknown"
                if drinking == "unknown" or drinking_recovering
                else self._drinking_context
            ),
            "events": events,
            "experimental": True,
        }
        if unknown_reasons:
            result["unknown_reasons"] = unknown_reasons
        return result


# A convenient module-local name for callers migrating from behavior_events.
BehaviorTimeline = BehaviorTimelineV2
