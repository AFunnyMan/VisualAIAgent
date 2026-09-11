"""Seat-ROI person association with bounded support from weak detections."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from visual_ai_agent.behavior_visibility import PersonCandidate, _iou

DEFAULT_SEAT_ROI = (0.2, 0.2, 0.95, 1.0)


def parse_roi(value: str) -> tuple[float, float, float, float]:
    """Parse a normalized ``x1,y1,x2,y2`` ROI for command-line callers."""
    try:
        roi = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise ValueError("ROI must contain four numbers") from error
    return _validate_roi(roi)


def _validate_roi(roi: Sequence[float]) -> tuple[float, float, float, float]:
    if len(roi) != 4 or not all(math.isfinite(value) for value in roi):
        raise ValueError("ROI must contain four finite values")
    x1, y1, x2, y2 = roi
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("ROI must be a non-empty normalized rectangle")
    return x1, y1, x2, y2


class SeatPersonGate:
    """Associate a real person detection with one person in a fixed seat ROI."""

    def __init__(
        self,
        roi: Sequence[float] = DEFAULT_SEAT_ROI,
        *,
        strong_confidence: float = 0.5,
        weak_confidence: float = 0.25,
        weak_seconds: float = 0.4,
        max_gap: float = 0.25,
        min_iou: float = 0.3,
        max_center_distance: float = 0.15,
    ) -> None:
        values = (
            strong_confidence,
            weak_confidence,
            weak_seconds,
            max_gap,
            min_iou,
            max_center_distance,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Gate parameters must be finite")
        if not (0 <= weak_confidence <= strong_confidence <= 1):
            raise ValueError("Invalid confidence thresholds")
        if (
            weak_seconds <= 0
            or max_gap <= 0
            or not 0 <= min_iou <= 1
            or not 0 <= max_center_distance <= 1
        ):
            raise ValueError("Invalid association thresholds")
        self.roi = _validate_roi(roi)
        self.strong_confidence = strong_confidence
        self.weak_confidence = weak_confidence
        self.weak_seconds = weak_seconds
        self.max_gap = max_gap
        self.min_iou = min_iou
        self.max_center_distance = max_center_distance
        self.reset()

    def reset(self) -> None:
        self._box: tuple[float, float, float, float] | None = None
        self._last_timestamp: float | None = None
        self._last_strong_timestamp: float | None = None
        self._frame_size: tuple[int, int] | None = None

    @staticmethod
    def _candidate_dict(candidate: PersonCandidate) -> dict[str, object]:
        return {"confidence": candidate.confidence, "bbox": list(candidate.bbox)}

    def _base_result(self, raw: list[PersonCandidate]) -> dict[str, object]:
        return {
            "person_track_supported": False,
            "person_candidate": None,
            "all_candidates": [self._candidate_dict(item) for item in raw],
            "strong_count": 0,
            "in_roi_count": 0,
            "reason": "invalid_frame",
        }

    def observe(
        self,
        timestamp: float,
        candidates: Iterable[PersonCandidate],
        frame_size: tuple[int, int] | None,
        *,
        valid_frame: bool = True,
    ) -> dict[str, object]:
        raw = list(candidates)
        result = self._base_result(raw)
        if not valid_frame or frame_size is None or not math.isfinite(timestamp):
            self.reset()
            return result
        width, height = frame_size
        if width <= 0 or height <= 0:
            self.reset()
            return result
        if self._frame_size is not None and frame_size != self._frame_size:
            self.reset()
            result["reason"] = "frame_size_changed"
            return result
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            self.reset()
            result["reason"] = "timestamp_not_increasing"
            return result
        if self._last_timestamp is not None and timestamp - self._last_timestamp > self.max_gap:
            self.reset()
            result["reason"] = "timestamp_gap"
            return result
        if not all(self._valid_candidate(item, width, height) for item in raw):
            self.reset()
            result["reason"] = "invalid_candidate"
            return result

        in_roi = [item for item in raw if self._center_in_roi(item, width, height)]
        strong = [item for item in in_roi if item.confidence >= self.strong_confidence]
        result["in_roi_count"], result["strong_count"] = len(in_roi), len(strong)

        if self._box is None:
            if len(strong) != 1:
                self.reset()
                result["reason"] = "zero_strong_in_roi" if not strong else "multiple_strong_in_roi"
                return result
            chosen = strong[0]
            self._box, self._last_timestamp, self._last_strong_timestamp = (
                chosen.bbox,
                timestamp,
                timestamp,
            )
            self._frame_size = frame_size
            result["person_candidate"] = self._candidate_dict(chosen)
            result["reason"] = "needs_previous_frame"
            return result

        eligible = [item for item in in_roi if item.confidence >= self.weak_confidence]
        matches = [item for item in eligible if self._spatial_match(item.bbox, width, height)]
        if len(matches) != 1:
            self.reset()
            if len(matches) > 1:
                result["reason"] = "multiple_spatial_matches"
            elif not eligible:
                result["reason"] = "zero_eligible_in_roi"
            else:
                result["reason"] = "spatial_mismatch"
            return result
        chosen = matches[0]
        is_strong = chosen.confidence >= self.strong_confidence
        if not is_strong and timestamp - self._last_strong_timestamp > self.weak_seconds:
            self.reset()
            result["reason"] = "weak_detection_timeout"
            return result
        return self._accept(timestamp, chosen, frame_size, result, strong=is_strong)

    @staticmethod
    def _valid_candidate(candidate: PersonCandidate, width: int, height: int) -> bool:
        box = candidate.bbox
        return (
            math.isfinite(candidate.confidence)
            and 0 <= candidate.confidence <= 1
            and len(box) == 4
            and all(math.isfinite(value) for value in box)
            and 0 <= box[0] < box[2] <= width
            and 0 <= box[1] < box[3] <= height
        )

    def _center_in_roi(self, candidate: PersonCandidate, width: int, height: int) -> bool:
        x1, y1, x2, y2 = candidate.bbox
        cx, cy = (x1 + x2) / (2 * width), (y1 + y2) / (2 * height)
        rx1, ry1, rx2, ry2 = self.roi
        return rx1 <= cx <= rx2 and ry1 <= cy <= ry2

    def _spatial_match(
        self, box: tuple[float, float, float, float], width: int, height: int
    ) -> bool:
        assert self._box is not None
        old_cx, old_cy = (self._box[0] + self._box[2]) / 2, (self._box[1] + self._box[3]) / 2
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        distance = math.hypot(cx - old_cx, cy - old_cy) / math.hypot(width, height)
        return _iou(self._box, box) >= self.min_iou and distance <= self.max_center_distance

    def _accept(self, timestamp, chosen, frame_size, result, *, strong):
        self._box, self._last_timestamp, self._frame_size = chosen.bbox, timestamp, frame_size
        if strong:
            self._last_strong_timestamp = timestamp
        result["person_track_supported"] = True
        result["person_candidate"] = self._candidate_dict(chosen)
        result["reason"] = "chosen_strong_detection" if strong else "chosen_weak_continuation"
        return result
