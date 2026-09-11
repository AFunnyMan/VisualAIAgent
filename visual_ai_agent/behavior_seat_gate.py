"""Seat-ROI person association with bounded support from weak detections."""

from __future__ import annotations

import math
from collections import deque
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
        exit_edge_margin: float = 0.03,
        exit_motion_distance: float = 0.04,
        exit_history_seconds: float = 0.5,
        exit_grace_seconds: float = 0.8,
    ) -> None:
        values = (
            strong_confidence,
            weak_confidence,
            weak_seconds,
            max_gap,
            min_iou,
            max_center_distance,
            exit_edge_margin,
            exit_motion_distance,
            exit_history_seconds,
            exit_grace_seconds,
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
            or not 0 < exit_edge_margin <= 0.25
            or not 0 < exit_motion_distance <= 1
            or exit_history_seconds <= 0
            or exit_grace_seconds <= 0
        ):
            raise ValueError("Invalid association thresholds")
        self.roi = _validate_roi(roi)
        self.strong_confidence = strong_confidence
        self.weak_confidence = weak_confidence
        self.weak_seconds = weak_seconds
        self.max_gap = max_gap
        self.min_iou = min_iou
        self.max_center_distance = max_center_distance
        self.exit_edge_margin = exit_edge_margin
        self.exit_motion_distance = exit_motion_distance
        self.exit_history_seconds = exit_history_seconds
        self.exit_grace_seconds = exit_grace_seconds
        self.reset()

    def reset(self) -> None:
        self._box: tuple[float, float, float, float] | None = None
        self._last_timestamp: float | None = None
        self._last_detection_timestamp: float | None = None
        self._last_strong_timestamp: float | None = None
        self._frame_size: tuple[int, int] | None = None
        self._history: deque[tuple[float, float, float]] = deque()

    @staticmethod
    def _candidate_dict(candidate: PersonCandidate) -> dict[str, object]:
        return {"confidence": candidate.confidence, "bbox": list(candidate.bbox)}

    def _base_result(self, raw: list[PersonCandidate]) -> dict[str, object]:
        return {
            "person_track_supported": False,
            "exit_evidence": False,
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
            self._last_detection_timestamp = timestamp
            self._frame_size = frame_size
            self._record_motion(timestamp, chosen.bbox, width, height)
            result["person_candidate"] = self._candidate_dict(chosen)
            result["reason"] = "needs_previous_frame"
            return result

        eligible = [item for item in in_roi if item.confidence >= self.weak_confidence]
        matches = [item for item in eligible if self._spatial_match(item.bbox, width, height)]
        if len(matches) != 1:
            reason = None
            if len(matches) > 1:
                reason = "multiple_spatial_matches"
            elif not eligible:
                reason = "zero_eligible_in_roi"
            else:
                reason = "spatial_mismatch"
            exit_edge = self._supported_exit_edge(timestamp, width, height)
            compatible_ambiguity = (
                exit_edge is not None
                and len(matches) > 1
                and len(matches) == len(eligible)
                and all(
                    self._candidate_at_exit_edge(item, width, height, exit_edge)
                    for item in matches
                )
                and self._duplicate_candidates(matches)
            )
            if exit_edge is not None and (not eligible or compatible_ambiguity):
                self._last_timestamp = timestamp
                result["exit_evidence"] = True
                result["reason"] = f"exit_edge_grace_after_{reason}"
                return result
            self.reset()
            result["reason"] = reason
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
        self._last_detection_timestamp = timestamp
        self._record_motion(timestamp, chosen.bbox, frame_size[0], frame_size[1])
        if strong:
            self._last_strong_timestamp = timestamp
        result["person_track_supported"] = True
        result["person_candidate"] = self._candidate_dict(chosen)
        result["reason"] = "chosen_strong_detection" if strong else "chosen_weak_continuation"
        return result

    def _record_motion(self, timestamp, box, width, height) -> None:
        center_x = (box[0] + box[2]) / (2 * width)
        center_y = (box[1] + box[3]) / (2 * height)
        self._history.append((timestamp, center_x, center_y))
        cutoff = timestamp - self.exit_history_seconds
        while len(self._history) > 2 and self._history[0][0] < cutoff:
            self._history.popleft()

    def _supported_exit_edge(
        self, timestamp: float, width: int, height: int
    ) -> str | None:
        """Keep continuity briefly only after measured motion out through a frame edge."""
        if self._box is None or self._last_detection_timestamp is None or len(self._history) < 2:
            return None
        if timestamp - self._last_detection_timestamp > self.exit_grace_seconds:
            return None
        x1, y1, x2, y2 = self._box
        first_x, first_y = self._history[0][1:]
        last_x, last_y = self._history[-1][1:]
        margin = self.exit_edge_margin
        distance = self.exit_motion_distance
        if x1 / width <= margin and first_x - last_x >= distance:
            return "left"
        if x2 / width >= 1 - margin and last_x - first_x >= distance:
            return "right"
        if y1 / height <= margin and first_y - last_y >= distance:
            return "top"
        if y2 / height >= 1 - margin and last_y - first_y >= distance:
            return "bottom"
        return None

    def _candidate_at_exit_edge(
        self, candidate: PersonCandidate, width: int, height: int, edge: str
    ) -> bool:
        cx1, cy1, cx2, cy2 = candidate.bbox
        margin = self.exit_edge_margin
        return {
            "left": cx1 / width <= margin,
            "right": cx2 / width >= 1 - margin,
            "top": cy1 / height <= margin,
            "bottom": cy2 / height >= 1 - margin,
        }[edge]

    @staticmethod
    def _duplicate_candidates(candidates: list[PersonCandidate]) -> bool:
        """Require ambiguous boxes to be nested duplicates, not merely nearby people."""
        for index, first in enumerate(candidates):
            for second in candidates[index + 1 :]:
                left = max(first.bbox[0], second.bbox[0])
                top = max(first.bbox[1], second.bbox[1])
                right = min(first.bbox[2], second.bbox[2])
                bottom = min(first.bbox[3], second.bbox[3])
                intersection = max(0.0, right - left) * max(0.0, bottom - top)
                first_area = (first.bbox[2] - first.bbox[0]) * (first.bbox[3] - first.bbox[1])
                second_area = (second.bbox[2] - second.bbox[0]) * (
                    second.bbox[3] - second.bbox[1]
                )
                if intersection / min(first_area, second_area) < 0.9:
                    return False
        return True
