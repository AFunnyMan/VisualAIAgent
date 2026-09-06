from __future__ import annotations

import numpy as np
import pytest

from visual_ai_agent.models import Detection
from visual_ai_agent.vision import annotate_frame, inverse_letterbox, letterbox, region_for_box


def test_letterbox_round_trip_for_landscape_frame() -> None:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    tensor, transform = letterbox(frame, 640)

    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert transform.scale == 1
    assert transform.pad_left == 0
    assert transform.pad_top == 80

    source_boxes = np.asarray([[10, 20, 300, 400]], dtype=np.float32)
    model_boxes = source_boxes.copy()
    model_boxes[:, [1, 3]] += 80
    np.testing.assert_allclose(inverse_letterbox(model_boxes, transform), source_boxes)


def test_inverse_letterbox_clips_padding_to_frame() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    _, transform = letterbox(frame, 640)
    mapped = inverse_letterbox(np.asarray([[-50, -50, 700, 700]]), transform)
    np.testing.assert_array_equal(mapped, [[0, 0, 200, 100]])


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ((0, 0, 20, 20), "left"),
        ((300, 0, 340, 20), "center"),
        ((600, 0, 640, 20), "right"),
    ],
)
def test_region_uses_non_mirrored_box_center(box: tuple[int, int, int, int], expected: str) -> None:
    assert region_for_box(box, 640) == expected


def test_letterbox_rejects_invalid_frame() -> None:
    with pytest.raises(ValueError, match="HxWx3"):
        letterbox(np.zeros((10, 10), dtype=np.uint8))


def test_supervision_annotation_preserves_input_and_draws_candidates() -> None:
    frame = np.zeros((120, 180, 3), dtype=np.uint8)
    detections = [
        Detection(category="cup", confidence=0.9, bbox=(10, 10, 60, 90), region="left"),
        Detection(category="cup", confidence=0.8, bbox=(90, 20, 160, 100), region="right"),
    ]

    annotated = annotate_frame(frame, detections)

    assert np.count_nonzero(frame) == 0
    assert np.count_nonzero(annotated) > 0
