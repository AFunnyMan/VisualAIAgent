import numpy as np
import pytest

from scripts.evaluate_scene_model import compare_export, decode, expected_names
from visual_ai_agent.vision import letterbox


def test_experimental_class_ids_are_not_coco_ids():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0] = [10, 20, 50, 70, 0.9, 1]
    result = decode(raw, transform, 0.35)
    assert result[0].category == "cup"
    raw[0, 0, 5] = 41
    with pytest.raises(ValueError, match="class ID"):
        decode(raw, transform, 0.35)


def test_experimental_decode_suppresses_near_duplicate_but_keeps_distinct_boxes():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0] = [10, 20, 110, 120, 0.9, 1]
    raw[0, 1] = [10.5, 20.5, 110.5, 120.5, 0.8, 1]
    raw[0, 2] = [60, 20, 160, 120, 0.7, 1]

    result = decode(raw, transform, 0.35)

    assert [item.confidence for item in result] == pytest.approx([0.9, 0.7])


def test_raw_decode_exposes_difference_that_postprocessing_would_hide():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    reference_raw = np.zeros((1, 300, 6), dtype=np.float32)
    reference_raw[0, 0] = [10, 20, 110, 120, 0.9, 1]
    reference_raw[0, 1] = [10.5, 20.5, 110.5, 120.5, 0.8, 1]
    candidate_raw = reference_raw.copy()
    candidate_raw[0, 1, 4] = 0.0

    reference_post = decode(reference_raw, transform, 0.35)
    candidate_post = decode(candidate_raw, transform, 0.35)
    reference_unfiltered = decode(reference_raw, transform, 0.35, suppress_duplicates=False)
    candidate_unfiltered = decode(candidate_raw, transform, 0.35, suppress_duplicates=False)

    assert compare_export(reference_post, candidate_post)["passed"]
    assert not compare_export(reference_unfiltered, candidate_unfiltered)["passed"]


def test_export_comparison_rejects_duplicate_or_wrong_class():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0] = [10, 20, 50, 70, 0.9, 1]
    cup = decode(raw, transform, 0.35)
    assert compare_export(cup, cup)["passed"]
    assert not compare_export(cup, cup + cup)["passed"]
    raw[0, 0, 5] = 0
    bottle = decode(raw, transform, 0.35)
    assert not compare_export(cup, bottle)["passed"]


def test_candidate_rejects_nonfinite_output():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        decode(raw, transform, 0.35)


def test_preserved_head_keeps_coco_indices_and_filters_nonbusiness_classes():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0] = [10, 20, 50, 70, 0.9, 41]
    raw[0, 1] = [100, 100, 200, 200, 0.9, 0]
    result = decode(raw, transform, 0.35, expected_names(True))
    assert len(result) == 1
    assert result[0].category == "cup"


def test_custom_single_class_decode_keeps_key():
    _, transform = letterbox(np.zeros((640, 640, 3), dtype=np.uint8), 640)
    raw = np.zeros((1, 300, 6), dtype=np.float32)
    raw[0, 0] = [10, 20, 50, 70, 0.9, 0]
    result = decode(raw, transform, 0.35, {0: "key"}, {"key"})
    assert len(result) == 1
    assert result[0].category == "key"
