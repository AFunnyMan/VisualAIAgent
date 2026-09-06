"""Deterministic local event state machine.

Only fresh, running observations advance time.  Wall-clock timestamps are facts
for display and persistence; monotonic timestamps decide continuity.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from .models import CATEGORIES, Category, Region, SceneObservation, VisualEvent


@dataclass
class _CategoryState:
    present: bool = False
    appearance_count: int = 0
    appearance_started_at: datetime | None = None
    appearance_regions: set[Region] = field(default_factory=set)
    missing_started_at: datetime | None = None
    missing_started_monotonic: float | None = None
    last_regions: list[Region] = field(default_factory=list)
    last_seen_at: datetime | None = None


class EventStateMachine:
    """Confirm appearances and sustained valid absence without inventing events."""

    def __init__(
        self,
        *,
        appearance_samples: int = 3,
        missing_seconds: float = 5.0,
        max_gap_seconds: float = 3.0,
    ) -> None:
        if appearance_samples < 1:
            raise ValueError("appearance_samples must be positive")
        if missing_seconds <= 0:
            raise ValueError("missing_seconds must be positive")
        if max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        self.appearance_samples = appearance_samples
        self.missing_seconds = missing_seconds
        self.max_gap_seconds = max_gap_seconds
        self._states = {category: _CategoryState() for category in CATEGORIES}
        self._last_valid_monotonic: float | None = None

    def reset_continuity(self) -> None:
        """Discard pending confirmation intervals while retaining confirmed presence."""
        for state in self._states.values():
            state.appearance_count = 0
            state.appearance_started_at = None
            state.appearance_regions.clear()
            state.missing_started_at = None
            state.missing_started_monotonic = None
        self._last_valid_monotonic = None

    def preview(self, observation: SceneObservation) -> tuple[list[VisualEvent], object]:
        """Evaluate without committing, so storage and state can commit together."""
        clone = deepcopy(self)
        events = clone.process(observation)
        return events, clone

    def adopt(self, candidate: object) -> None:
        if not isinstance(candidate, EventStateMachine):
            raise TypeError("candidate must be an EventStateMachine")
        self._states = candidate._states
        self._last_valid_monotonic = candidate._last_valid_monotonic

    def process(self, observation: SceneObservation) -> list[VisualEvent]:
        if observation.status != "running" or not observation.fresh:
            self.reset_continuity()
            return []

        if self._last_valid_monotonic is not None:
            elapsed = observation.monotonic_at - self._last_valid_monotonic
            if elapsed <= 0 or elapsed > self.max_gap_seconds:
                self.reset_continuity()

        events: list[VisualEvent] = []
        detected: dict[Category, list[Region]] = {category: [] for category in CATEGORIES}
        for item in observation.detections:
            if item.region not in detected[item.category]:
                detected[item.category].append(item.region)

        for category in CATEGORIES:
            state = self._states[category]
            regions = detected[category]
            if regions:
                state.last_seen_at = observation.observed_at
                state.last_regions = regions
                state.missing_started_at = None
                state.missing_started_monotonic = None
                if state.present:
                    continue
                if state.appearance_count == 0:
                    state.appearance_started_at = observation.observed_at
                state.appearance_count += 1
                state.appearance_regions.update(regions)
                if state.appearance_count >= self.appearance_samples:
                    events.append(
                        VisualEvent(
                            kind="appeared",
                            category=category,
                            regions=sorted(state.appearance_regions),
                            observed_at=state.appearance_started_at or observation.observed_at,
                            confirmed_at=observation.observed_at,
                            last_seen_at=observation.observed_at,
                            source=observation.source,
                        )
                    )
                    state.present = True
                    state.appearance_count = 0
                    state.appearance_started_at = None
                    state.appearance_regions.clear()
                continue

            state.appearance_count = 0
            state.appearance_started_at = None
            state.appearance_regions.clear()
            if not state.present:
                continue
            if state.missing_started_monotonic is None:
                state.missing_started_monotonic = observation.monotonic_at
                state.missing_started_at = observation.observed_at
                continue
            if observation.monotonic_at - state.missing_started_monotonic >= self.missing_seconds:
                events.append(
                    VisualEvent(
                        kind="missing",
                        category=category,
                        regions=state.last_regions,
                        observed_at=state.missing_started_at or observation.observed_at,
                        confirmed_at=observation.observed_at,
                        last_seen_at=state.last_seen_at,
                        source=observation.source,
                    )
                )
                state.present = False
                state.missing_started_at = None
                state.missing_started_monotonic = None

        self._last_valid_monotonic = observation.monotonic_at
        return events

    def is_present(self, category: Category) -> bool:
        return self._states[category].present
