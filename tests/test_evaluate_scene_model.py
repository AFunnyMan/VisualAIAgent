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
