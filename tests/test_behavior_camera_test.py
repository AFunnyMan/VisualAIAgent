from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import behavior_camera_test as target


class Meta:
    def __init__(self, name: str, shape: list[int], kind: str = "tensor(float)") -> None:
        self.name, self.shape, self.type = name, shape, kind


class FakeSession:
    def __init__(self, *_args, **_kwargs) -> None:
        self.inputs = [Meta("images", [1, 3, 4, 4])]
        self.outputs = [Meta("scores", [1, 2])]

    def get_inputs(self):
        return self.inputs

    def get_outputs(self):
        return self.outputs

    def get_modelmeta(self):
        return SimpleNamespace(custom_metadata_map={"names": "{0: 'drinking', 1: 'not_drinking'}"})

    def run(self, _outputs, feed):
        assert feed["images"].shape == (1, 3, 4, 4)
        return [np.array([[0.9, 0.1]], dtype=np.float32)]


def test_onnx_classifier_is_manifest_backed_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(target, "preprocess_bgr", lambda *_args: np.zeros((1, 3, 4, 4), np.float32))
    record = {
        "_onnx": Path("model.onnx"),
        "_names": {0: "drinking", 1: "not_drinking"},
        "preprocessing": {"imgsz": 4},
    }
    result = target.OnnxClassifier(record).predict(np.zeros((8, 8, 3), dtype=np.uint8))
    assert result["label"] == "drinking"
    assert result["probabilities"] == pytest.approx({"drinking": 0.9, "not_drinking": 0.1})


def test_classifier_rejects_camera_size_that_invalidates_roi_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    record = {
        "_onnx": Path("model.onnx"),
        "_names": {0: "drinking", 1: "not_drinking"},
        "preprocessing": {
            "imgsz": 4,
            "roi": [0.25, 0, 0.75, 0.85],
            "expected_source_frame_size": [1920, 1080],
        },
    }
    with pytest.raises(ValueError, match="ROI recalibration"):
        target.OnnxClassifier(record).predict(np.zeros((720, 1280, 3), dtype=np.uint8))


def test_state_public_marks_person_gate_as_weak_auxiliary(tmp_path: Path) -> None:
    state = target.TestState(tmp_path, 30)
    public = state.public()
    assert public["experimental"] is True
    assert "弱辅助" in public["notice"]
    assert "无遮挡" in public["notice"]


def test_event_images_are_bounded_and_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(target, "MAX_EVENT_IMAGES", 1)
    state = target.TestState(tmp_path, 30)
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    saved = target._save_event_image(state, frame, {"kind": "stood_up"})
    assert saved and len(saved["sha256"]) == 64
    assert (tmp_path / saved["path"]).is_file()
    assert target._save_event_image(state, frame, {"kind": "sat_down"}) is None


def test_person_manifest_mismatch_stops_before_output_or_camera(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posture = {
        "_path": tmp_path / "p.json",
        "_onnx": tmp_path / "p.onnx",
        "onnx_sha256": "p",
        "preprocessing": {},
    }
    drinking = {
        "_path": tmp_path / "d.json",
        "_onnx": tmp_path / "d.onnx",
        "onnx_sha256": "d",
        "preprocessing": {},
    }
    monkeypatch.setattr(
        target,
        "load_manifest",
        lambda _path, task: posture if task == "posture" else drinking,
    )
    model = tmp_path / "person.onnx"
    model.write_bytes(b"model")
    manifest = tmp_path / "person.json"
    manifest.write_text(json.dumps({"sha256": "0" * 64}))
    args = SimpleNamespace(
        output=tmp_path / "new-output",
        posture_manifest=tmp_path / "p.json",
        drinking_manifest=tmp_path / "d.json",
        person_model=model,
        person_manifest=manifest,
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        target.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("age", [0.26, -0.1, float("nan"), float("inf")])
def test_expired_or_future_frames_never_count_as_fresh(age):
    observation = SimpleNamespace(status="running", fresh=True, monotonic_at=1.0)
    pair = {"sequence": 2, "completed_monotonic": 1.05}
    assert not target.fresh_pair(observation, pair, 1, age)


def test_duplicate_fault_and_mismatched_pairs_are_rejected():
    observation = SimpleNamespace(status="running", fresh=True, monotonic_at=1.0)
    pair = {"sequence": 2, "completed_monotonic": 1.05}
    assert target.fresh_pair(observation, pair, 1, 0.1)
    assert not target.fresh_pair(observation, pair, 2, 0.1)
    observation.fresh = False
    assert not target.fresh_pair(observation, pair, 1, 0.1)
    observation.fresh = True
    pair["completed_monotonic"] = 0.9
    assert not target.fresh_pair(observation, pair, 1, 0.1)
