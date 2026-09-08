import numpy as np

from visual_ai_agent.models import Detection
from visual_ai_agent.vision import CupScaleRecheckDetector


def detection(category, bbox=(1.0, 1.0, 3.0, 3.0), confidence=0.8, region="left"):
    return Detection(category=category, bbox=bbox, confidence=confidence, region=region)


class RecordingDetector:
    def __init__(self, results):
        self.results = iter(results)
        self.frames = []

    def detect(self, frame):
        self.frames.append(frame.copy())
        return next(self.results)


def test_existing_cup_skips_retry_and_preserves_primary_results():
    expected = [detection("bottle"), detection("cup", confidence=0.7)]
    base = RecordingDetector([expected])
    actual = CupScaleRecheckDetector(base).detect(np.zeros((8, 10, 3), dtype=np.uint8))
    assert actual == expected
    assert len(base.frames) == 1


def test_retry_runs_once_and_only_adds_cups_without_mutating_source():
    primary = [detection("cell phone", confidence=0.9)]
    base = RecordingDetector(
        [primary, [detection("bottle", confidence=0.95), detection("cup", (3, 3, 6, 6), 0.7)]]
    )
    frame = np.arange(9 * 11 * 3, dtype=np.uint8).reshape(9, 11, 3)
    original = frame.copy()
    actual = CupScaleRecheckDetector(base).detect(frame)
    assert len(base.frames) == 2
    assert [item.category for item in actual] == ["cell phone", "cup"]
    np.testing.assert_array_equal(frame, original)


def test_retry_odd_dimensions_maps_clipped_content_edges_and_region():
    # 11x9 scales to 8x7 with offsets (1, 1); the box crosses every content edge.
    base = RecordingDetector([[], [detection("cup", (-5, 0, 20, 10), 0.6, "right")]])
    actual = CupScaleRecheckDetector(base).detect(np.zeros((9, 11, 3), dtype=np.uint8))
    assert actual[0].bbox == (0.0, 0.0, 11.0, 9.0)
    assert actual[0].region == "center"
    retry_frame = base.frames[1]
    assert retry_frame.shape == (9, 11, 3)
    assert np.all(retry_frame[0] == 114)


def test_padding_only_and_degenerate_retry_boxes_are_dropped():
    base = RecordingDetector([[], [detection("cup", (0, 0, 1, 1)), detection("cup", (2, 2, 2, 4))]])
    assert CupScaleRecheckDetector(base).detect(np.zeros((8, 10, 3), dtype=np.uint8)) == []


def test_results_do_not_carry_into_next_frame():
    base = RecordingDetector([[], [detection("cup", (2, 2, 4, 4))], [], []])
    detector = CupScaleRecheckDetector(base)
    frame = np.zeros((8, 10, 3), dtype=np.uint8)
    assert detector.detect(frame)
    assert detector.detect(frame) == []
    assert len(base.frames) == 4
