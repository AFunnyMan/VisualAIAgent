from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

from visual_ai_agent.models import Detection

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "finetuned_camera_test.py"
SPEC = importlib.util.spec_from_file_location("finetuned_camera_test", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def detection(*, bbox=(1.0, 2.0, 3.0, 4.0), region="left") -> Detection:
    return Detection(category="cup", confidence=0.8, bbox=bbox, region=region)


def test_paired_detector_preserves_order_and_exact_input_frame() -> None:
    calls: list[tuple[str, int]] = []

    class Detector:
        def __init__(self, name: str, result: list[Detection]) -> None:
            self.name = name
            self.result = result

        def detect(self, frame):
            calls.append((self.name, id(frame)))
            return self.result

    frame = np.zeros((8, 10, 3), dtype=np.uint8)
    candidate_result = [detection()]
    paired = module.PairedDetector(
        Detector("candidate", candidate_result), Detector("baseline", [])
    )

    assert paired.detect(frame) == candidate_result
    snapshot = paired.snapshot()

    assert calls == [("candidate", id(frame)), ("baseline", id(frame))]
    assert snapshot is not None
    assert snapshot.sequence == 1
    assert np.array_equal(snapshot.frame, frame)
    assert snapshot.candidate == candidate_result
    assert snapshot.baseline == []


def test_disagreement_ignores_box_jitter_but_tracks_observable_difference() -> None:
    assert not module.disagreement(
        [detection(bbox=(1.0, 2.0, 3.0, 4.0))],
        [detection(bbox=(1.5, 2.5, 3.5, 4.5))],
    )
    assert module.disagreement([detection(region="left")], [detection(region="right")])
    assert module.disagreement([detection()], [])
