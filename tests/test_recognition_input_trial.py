import numpy as np

from scripts.recognition_input_trial import (
    SlicedDetector,
    choose_threshold,
    class_nms,
    tile_boxes,
)
from visual_ai_agent.models import Detection


def detection(box, score=0.8, category="cup"):
    return Detection(category=category, bbox=box, confidence=score, region="left")


def test_slice_mapping_restores_coordinates_and_original_region():
    class FakeDetector:
        calls = 0

        def detect(self, frame):
            self.calls += 1
            # Return a box only in the bottom-right tile; full image pass is empty.
            return [detection((30, 0, 45, 10))] if self.calls == 5 else []

    result = SlicedDetector(FakeDetector()).detect(np.zeros((100, 180, 3), dtype=np.uint8))
    assert len(result) == 1
    assert result[0].bbox == (110, 44, 125, 54)
    assert result[0].region == "center"


def test_tiles_cover_border_pixels_without_exceeding_frame():
    tiles = tile_boxes(181, 101)
    assert len(tiles) == 4
    cover = np.zeros((101, 181), dtype=np.uint8)
    for x1, y1, x2, y2 in tiles:
        assert 0 <= x1 < x2 <= 181 and 0 <= y1 < y2 <= 101
        cover[y1:y2, x1:x2] = 1
    assert cover.all()


def test_slice_duplicates_suppressed_but_separate_objects_and_classes_retained():
    result = class_nms(
        [
            detection((0, 0, 10, 10), 0.7),
            detection((1, 1, 11, 11), 0.9),
            detection((0, 0, 10, 10), 0.8, "bottle"),
            detection((20, 20, 30, 30), 0.75),
        ]
    )
    assert len(result) == 3
    assert result[0].confidence == 0.9


def test_calibration_respects_fp_budget_and_tie_breaks_before_validation():
    curve = [
        {"threshold": 0.1, "tp": 10, "fp": 8},
        {"threshold": 0.3, "tp": 8, "fp": 2},
        {"threshold": 0.5, "tp": 8, "fp": 1},
        {"threshold": 0.7, "tp": 6, "fp": 0},
    ]
    assert choose_threshold(curve, budget=2) == 0.5
