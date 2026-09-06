from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from visual_ai_agent.vision import YoloOnnxDetector


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
