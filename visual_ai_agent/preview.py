"""Fast camera preview rendering with short-lived display-only box tracking.

The tracker in this module is deliberately separate from scene observations.  It
only makes the UI smoother between detector results and must never be used as a
source of business facts.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from visual_ai_agent.models import SceneObservation

Frame = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class PreviewSnapshot:
    jpeg: bytes | None
    status: str
    frame_age_seconds: float
    detection_age_seconds: float | None
    tracked: bool


@dataclass(slots=True)
class _DisplayBox:
    category: str
    confidence: float
    bbox: NDArray[np.float32]
    points: NDArray[np.float32]
    tracked: bool = False


class PreviewRenderer:
    """Render recent frames while visually propagating the last detector boxes."""

    def __init__(
        self,
        *,
        target_width: int = 960,
        jpeg_quality: int = 75,
        max_frame_age: float = 0.5,
        max_detection_age: float = 2.5,
    ) -> None:
        self.target_width = target_width
        self.jpeg_quality = jpeg_quality
        self.max_frame_age = max_frame_age
        self.max_detection_age = max_detection_age
        self._boxes: list[_DisplayBox] = []
        self._previous_gray: NDArray[np.uint8] | None = None
        self._previous_sequence: int | None = None
        self._shape: tuple[int, int] | None = None
        self._observation_monotonic: float | None = None
        self._detection_monotonic: float | None = None

    def invalidate(self, status: str = "stale") -> None:
        """Forget all display tracking state after a source discontinuity."""
        self._boxes.clear()
        self._previous_gray = None
        self._previous_sequence = None
        self._shape = None
        self._observation_monotonic = None
        self._detection_monotonic = None

    def render(
        self,
        *,
        frame: Frame,
        sequence: int,
        monotonic_at: float,
        observation: SceneObservation | None,
        now: float,
    ) -> PreviewSnapshot:
        frame_age = max(0.0, now - monotonic_at)
        detection_age = None if observation is None else max(0.0, now - observation.monotonic_at)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
            self.invalidate("invalid_frame")
            return PreviewSnapshot(None, "invalid_frame", frame_age, detection_age, False)
        if frame_age > self.max_frame_age:
            self.invalidate("stale")
            return PreviewSnapshot(None, "stale", frame_age, detection_age, False)

        source_height, source_width = frame.shape[:2]
        shape = (source_height, source_width)
        if self._shape is not None and shape != self._shape:
            self.invalidate("resolution_changed")
        self._shape = shape
        work_width = min(self.target_width, source_width)
        work_height = max(1, round(source_height * work_width / source_width))
        work_frame = (
            frame
            if (work_height, work_width) == shape
            else cv2.resize(frame, (work_width, work_height), interpolation=cv2.INTER_AREA)
        )
        gray = cv2.cvtColor(work_frame, cv2.COLOR_BGR2GRAY)

        observation_valid = (
            observation is not None
            and observation.status == "running"
            and observation.fresh
            and observation.width == source_width
            and observation.height == source_height
        )
        if observation is not None and not observation_valid:
            self._boxes.clear()
            self._detection_monotonic = None

        is_new_observation = observation_valid and (
            self._observation_monotonic is None
            or observation.monotonic_at > self._observation_monotonic
        )
        if is_new_observation:
            assert observation is not None
            self._observation_monotonic = observation.monotonic_at
            if detection_age is not None and detection_age <= self.max_detection_age:
                self._boxes = self._initialize_boxes(
                    gray,
                    observation,
                    scale_x=work_width / source_width,
                    scale_y=work_height / source_height,
                )
                self._detection_monotonic = observation.monotonic_at if self._boxes else None
            else:
                self._boxes.clear()
                self._detection_monotonic = None
        elif (
            sequence != self._previous_sequence and self._previous_gray is not None and self._boxes
        ):
            self._boxes = self._track_boxes(
                self._previous_gray, gray, self._boxes, work_width, work_height
            )
            if not self._boxes:
                self._detection_monotonic = None

        current_detection_age = (
            None if self._detection_monotonic is None else max(0.0, now - self._detection_monotonic)
        )
        if current_detection_age is not None and current_detection_age > self.max_detection_age:
            self._boxes.clear()
            self._detection_monotonic = None
            current_detection_age = None

        self._previous_gray = gray
        self._previous_sequence = sequence
        rendered = self._draw(work_frame, current_detection_age)
        ok, encoded = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        status = "running" if observation is None else observation.status
        return PreviewSnapshot(
            encoded.tobytes() if ok else None,
            status if ok else "encode_error",
            frame_age,
            current_detection_age,
            any(box.tracked for box in self._boxes),
        )

    def _initialize_boxes(
        self,
        gray: NDArray[np.uint8],
        observation: SceneObservation,
        *,
        scale_x: float,
        scale_y: float,
    ) -> list[_DisplayBox]:
        boxes: list[_DisplayBox] = []
        height, width = gray.shape
        for detection in observation.detections:
            source_x1, source_y1, source_x2, source_y2 = detection.bbox
            x1, x2 = source_x1 * scale_x, source_x2 * scale_x
            y1, y2 = source_y1 * scale_y, source_y2 * scale_y
            x1, x2 = sorted((max(0.0, x1), min(float(width - 1), x2)))
            y1, y2 = sorted((max(0.0, y1), min(float(height - 1), y2)))
            if x2 - x1 < 3 or y2 - y1 < 3:
                continue
            mask = np.zeros_like(gray)
            cv2.rectangle(mask, (round(x1), round(y1)), (round(x2), round(y2)), 255, -1)
            points = cv2.goodFeaturesToTrack(
                gray, maxCorners=40, qualityLevel=0.01, minDistance=3, mask=mask, blockSize=3
            )
            if points is None:
                points = np.empty((0, 1, 2), dtype=np.float32)
            boxes.append(
                _DisplayBox(
                    category=detection.category,
                    confidence=detection.confidence,
                    bbox=np.array((x1, y1, x2, y2), dtype=np.float32),
                    points=points.astype(np.float32),
                )
            )
        return boxes

    @staticmethod
    def _track_boxes(
        previous: NDArray[np.uint8],
        current: NDArray[np.uint8],
        boxes: list[_DisplayBox],
        width: int,
        height: int,
    ) -> list[_DisplayBox]:
        tracked: list[_DisplayBox] = []
        for box in boxes:
            if len(box.points) < 3:
                continue
            new_points, status, errors = cv2.calcOpticalFlowPyrLK(
                previous,
                current,
                box.points,
                None,
                winSize=(21, 21),
                maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
            )
            if new_points is None or status is None or errors is None:
                continue
            good = status.reshape(-1).astype(bool) & (errors.reshape(-1) < 25.0)
            old = box.points.reshape(-1, 2)[good]
            new = new_points.reshape(-1, 2)[good]
            if len(new) < 3 or len(new) < len(box.points) * 0.45:
                continue
            backward, backward_status, _ = cv2.calcOpticalFlowPyrLK(
                current,
                previous,
                new.reshape(-1, 1, 2),
                None,
                winSize=(21, 21),
                maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
            )
            if backward is None or backward_status is None:
                continue
            forward_backward_good = backward_status.reshape(-1).astype(bool)
            forward_backward_good &= np.linalg.norm(backward.reshape(-1, 2) - old, axis=1) < 1.5
            old = old[forward_backward_good]
            new = new[forward_backward_good]
            if len(new) < 3 or len(new) < len(box.points) * 0.45:
                continue
            shifts = new - old
            shift = np.median(shifts, axis=0)
            consistent = np.linalg.norm(shifts - shift, axis=1) < 5.0
            if np.count_nonzero(consistent) < 3:
                continue
            new = new[consistent]
            shift = np.median(shifts[consistent], axis=0)
            moved = box.bbox + np.array((shift[0], shift[1], shift[0], shift[1]))
            moved[[0, 2]] = np.clip(moved[[0, 2]], 0, width - 1)
            moved[[1, 3]] = np.clip(moved[[1, 3]], 0, height - 1)
            if moved[2] - moved[0] < 3 or moved[3] - moved[1] < 3:
                continue
            tracked.append(
                _DisplayBox(box.category, box.confidence, moved, new.reshape(-1, 1, 2), True)
            )
        return tracked

    def _draw(self, frame: Frame, detection_age: float | None) -> Frame:
        output = frame.copy()
        for box in self._boxes:
            x1, y1, x2, y2 = (int(round(value)) for value in box.bbox)
            color = (50, 210, 80)
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
            age = 0.0 if detection_age is None else detection_age
            source = "tracked" if box.tracked else "recent detection"
            label = f"{box.category} {box.confidence:.0%} | {source} {age:.1f}s"
            cv2.putText(
                output,
                label,
                (x1, max(18, y1 - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                color,
                2,
                cv2.LINE_AA,
            )
        return output
