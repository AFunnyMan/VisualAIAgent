from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from visual_ai_agent.models import Detection
from visual_ai_agent.vision import YoloOnnxDetector, suppress_near_duplicate_detections


def detection(category, confidence, bbox):
    return Detection(category=category, confidence=confidence, bbox=bbox, region="left")


def test_near_duplicate_suppression_is_conservative_and_stable() -> None:
    high = detection("cup", 0.9, (0, 0, 100, 100))
    duplicate = detection("cup", 0.8, (0.5, 0.5, 100.5, 100.5))
    adjacent = detection("cup", 0.7, (50, 0, 150, 100))
    cross_class = detection("bottle", 0.6, (0, 0, 100, 100))

    assert suppress_near_duplicate_detections([adjacent, duplicate, cross_class, high]) == [
        high,
        adjacent,
        cross_class,
    ]


def test_near_duplicate_suppression_does_not_chain_through_removed_box() -> None:
    # At a lower explicit threshold: A overlaps B and B overlaps C enough, while
    # A and C do not. B is removed, but it must not transitively remove C.
    first = detection("cup", 0.9, (0, 0, 100, 100))
    bridge = detection("cup", 0.8, (5, 0, 105, 100))
    last = detection("cup", 0.7, (10, 0, 110, 100))

    assert suppress_near_duplicate_detections([last, bridge, first], iou_threshold=0.9) == [
        first,
        last,
    ]


def test_near_duplicate_suppression_keeps_input_order_for_equal_scores() -> None:
    first = detection("cup", 0.8, (0, 0, 20, 20))
    second = detection("cup", 0.8, (30, 0, 50, 20))

    assert suppress_near_duplicate_detections([second, first]) == [second, first]


class FakeSession:
    output = np.asarray(
        [
            [
                [0, 80, 200, 300, 0.91, 39],
                [220, 100, 420, 400, 0.88, 41],
                [430, 80, 639, 550, 0.82, 67],
                [20, 80, 60, 120, 0.99, 0],
                [100, 100, 101, 101, 0.1, 39],
            ]
        ],
        dtype=np.float32,
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.input = SimpleNamespace(name="images", shape=[1, 3, 640, 640])
        self.output_info = SimpleNamespace(name="output0", shape=[1, 300, 6])

    def get_inputs(self) -> list[object]:
        return [self.input]

    def get_outputs(self) -> list[object]:
        return [self.output_info]

    def get_modelmeta(self) -> object:
        names = {39: "bottle", 41: "cup", 67: "cell phone"}
        return SimpleNamespace(custom_metadata_map={"names": str(names)})

    def run(self, _outputs: object, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert inputs["images"].shape == (1, 3, 640, 640)
        return [self.output]


def test_detector_filters_targets_and_preserves_same_class_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("visual_ai_agent.vision.ort.InferenceSession", FakeSession)
    FakeSession.output = np.asarray(
        [
            [
                [0, 80, 200, 300, 0.91, 39],
                [20, 90, 160, 250, 0.89, 39],
                [220, 100, 420, 400, 0.88, 41],
                [430, 80, 639, 550, 0.82, 67],
                [20, 80, 60, 120, 0.99, 0],
            ]
        ],
        dtype=np.float32,
    )
    detector = YoloOnnxDetector("unused.onnx", verify_manifest=False)
    detections = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))

    assert [detection.category for detection in detections] == [
        "bottle",
        "bottle",
        "cup",
        "cell phone",
    ]
    assert [detection.region for detection in detections] == [
        "left",
        "left",
        "center",
        "right",
    ]
    assert detections[0].bbox[1] == pytest.approx(0)


def test_detector_rejects_non_end_to_end_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("visual_ai_agent.vision.ort.InferenceSession", FakeSession)
    FakeSession.output = np.zeros((1, 84, 8400), dtype=np.float32)
    detector = YoloOnnxDetector("unused.onnx", verify_manifest=False)

    with pytest.raises(ValueError, match="end-to-end output"):
        detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))


def test_detector_requires_matching_manifest_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("visual_ai_agent.vision.ort.InferenceSession", FakeSession)
    model = tmp_path / "fake.onnx"
    model.write_bytes(b"model bytes")
    manifest = tmp_path / "fake.json"
    manifest.write_text(
        json.dumps(
            {
                "file": model.name,
                "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "end_to_end": True,
                "input_size": 640,
                "target_classes": {"39": "bottle", "41": "cup", "67": "cell phone"},
            }
        ),
        encoding="utf-8",
    )
    YoloOnnxDetector(model, manifest_path=manifest)

    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        YoloOnnxDetector(model, manifest_path=manifest)
