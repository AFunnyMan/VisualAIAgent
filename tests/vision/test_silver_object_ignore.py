import numpy as np
import pytest

from visual_ai_agent.models import Detection
from visual_ai_agent.vision import SilverObjectIgnoreDetector


class Stub:
    def __init__(self, detections):
        self.detections = detections

    def detect(self, frame):
        return self.detections


def box(category, xyxy):
    return Detection(category=category, bbox=xyxy, confidence=0.8, region="center")


def test_only_silver_corner_phone_removed_without_mutating_input():
    silver = box("cell phone", (760, 980, 990, 1080))
    normal = box("cell phone", (940, 760, 1280, 1005))
    other_bottom = box("cell phone", (1250, 930, 1510, 1080))
    cup = box("cup", silver.bbox)
    bottle = box("bottle", silver.bbox)
    original = [silver, normal, other_bottom, cup, bottle]
    result = SilverObjectIgnoreDetector(Stub(original)).detect(np.zeros((1080, 1920, 3)))
    assert result == [normal, other_bottom, cup, bottle]
    assert original == [silver, normal, other_bottom, cup, bottle]
    assert result[0] is normal


@pytest.mark.parametrize("shape", [(480, 640, 3), (720, 1280, 3), (1080, 1919, 3)])
def test_other_dimensions_do_not_apply_fixed_view_exclusion(shape):
    candidates = [box("cell phone", (760, 980, 990, 1080))]
    assert SilverObjectIgnoreDetector(Stub(candidates)).detect(np.zeros(shape)) is candidates


@pytest.mark.parametrize(
    "xyxy",
    [
        (760, 980, 990, 1077.99),
        (719.9, 980, 990, 1080),
        (760, 959.9, 990, 1080),
        (760, 980, 1180.1, 1080),
        (1000, 980, 1180, 1080),
        (0, 980, 200, 1080),
    ],
)
def test_nearby_or_other_border_candidates_retained(xyxy):
    candidate = box("cell phone", xyxy)
    assert SilverObjectIgnoreDetector(Stub([candidate])).detect(np.zeros((1080, 1920, 3))) == [
        candidate
    ]


def test_boundary_and_wide_corner_candidates_removed():
    candidates = [box("cell phone", (720, 960, 1180, 1078))]
    assert SilverObjectIgnoreDetector(Stub(candidates)).detect(np.zeros((1080, 1920, 3))) == []
