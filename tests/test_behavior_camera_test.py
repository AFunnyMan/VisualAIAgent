from __future__ import annotations

import json
import threading
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


def test_matching_classifier_contracts_preprocess_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    calls = []

    def preprocess(frame, size, roi):
        calls.append((id(frame), size, roi))
        return np.zeros((1, 3, size, size), np.float32)

    monkeypatch.setattr("visual_ai_agent.behavior.preprocess_bgr", preprocess)
    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda *_: {"candidates": [], "elapsed_ms": 0, "timings": {}},
    )
    record = {
        "_onnx": Path("model.onnx"),
        "_names": {0: "drinking", 1: "not_drinking"},
        "preprocessing": {"imgsz": 4},
    }
    posture = target.OnnxClassifier(record)
    posture.names = {0: "seated", 1: "standing"}
    drinking = target.OnnxClassifier(record)
    detector = target.BehaviorDetector(
        posture, drinking, Path("fake.onnx"), auxiliary_mode="serial"
    )
    try:
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        detector.detect(frame)
        assert calls == [(id(frame), 4, None)]
        snapshot = detector.snapshot()
        assert snapshot["posture"]["label"] == "seated"
        assert snapshot["drinking"]["label"] == "drinking"
        assert snapshot["stage_times_ms"]["drinking_preprocess"] == 0
    finally:
        detector.close()


def test_matching_contract_bounded_drinking_uses_same_prepared_frame(monkeypatch) -> None:
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    tensors = []

    def preprocess(frame, size, _roi):
        tensor = np.full((1, 3, size, size), frame[0, 0, 0], np.float32)
        tensors.append(tensor)
        return tensor

    monkeypatch.setattr("visual_ai_agent.behavior.preprocess_bgr", preprocess)
    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda *_: {"candidates": [], "elapsed_ms": 0, "timings": {}},
    )
    record = {
        "_onnx": Path("model.onnx"),
        "_names": {0: "drinking", 1: "not_drinking"},
        "preprocessing": {"imgsz": 4},
    }
    posture, drinking = target.OnnxClassifier(record), target.OnnxClassifier(record)
    seen = []
    posture.session.run = lambda _outputs, feed: (
        seen.append(("posture", id(feed["images"]))) or [np.array([[0.9, 0.1]], dtype=np.float32)]
    )
    drinking.session.run = lambda _outputs, feed: (
        seen.append(("drinking", id(feed["images"]))) or [np.array([[0.9, 0.1]], dtype=np.float32)]
    )
    detector = target.BehaviorDetector(posture, drinking, Path("fake.onnx"), person_wait_ms=100)
    try:
        detector.detect(np.ones((8, 8, 3), dtype=np.uint8))
        assert len(tensors) == 1
        assert sorted(seen) == sorted([("posture", id(tensors[0])), ("drinking", id(tensors[0]))])
        assert detector.snapshot()["drinking_auxiliary_status"] == "ready"
    finally:
        assert detector.close()


@pytest.mark.parametrize(
    ("posture_preprocessing", "drinking_preprocessing"),
    [
        ({"imgsz": 4}, {"imgsz": 6}),
        ({"imgsz": 4}, {"imgsz": 4, "roi": [0.0, 0.0, 0.5, 1.0]}),
        (
            {"imgsz": 4, "expected_source_frame_size": [8, 8]},
            {"imgsz": 4, "expected_source_frame_size": [16, 8]},
        ),
    ],
)
def test_different_classifier_contracts_preprocess_independently(
    monkeypatch: pytest.MonkeyPatch, posture_preprocessing, drinking_preprocessing
) -> None:
    class FlexibleSession(FakeSession):
        def __init__(self, size):
            super().__init__()
            self.inputs[0].shape = [1, 3, size, size]

        def run(self, _outputs, feed):
            return [np.array([[0.9, 0.1]], dtype=np.float32)]

    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda *_: {"candidates": [], "elapsed_ms": 0, "timings": {}},
    )
    calls = []
    monkeypatch.setattr(
        "visual_ai_agent.behavior.preprocess_bgr",
        lambda frame, size, roi: (
            calls.append((size, roi)) or np.zeros((1, 3, size, size), np.float32)
        ),
    )

    def classifier(preprocessing):
        monkeypatch.setattr(
            target.ort,
            "InferenceSession",
            lambda *_args, **_kwargs: FlexibleSession(preprocessing["imgsz"]),
        )
        record = {
            "_onnx": Path("model.onnx"),
            "_names": {0: "drinking", 1: "not_drinking"},
            "preprocessing": preprocessing,
        }
        return target.OnnxClassifier(record)

    # Source-size mismatch is a contract check; use direct identity comparison
    # because deliberately running it would correctly reject one classifier.
    posture, drinking = classifier(posture_preprocessing), classifier(drinking_preprocessing)
    detector = target.BehaviorDetector(
        posture, drinking, Path("fake.onnx"), auxiliary_mode="serial"
    )
    try:
        assert detector.shared_classifier_preprocessing is False
        if posture.expected_source_size != drinking.expected_source_size:
            return
        detector.detect(np.zeros((8, 8, 3), dtype=np.uint8))
        assert len(calls) == 2
    finally:
        detector.close()


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


def test_failed_inference_has_timings_without_becoming_fresh_or_reusing_old_attempt():
    observation = SimpleNamespace(status="stale", fresh=False, monotonic_at=1.0, inference_ms=560.0)
    pair = {
        "sequence": 2,
        "completed_monotonic": 1.56,
        "frame_sha256": "abc",
        "stage_times_ms": {"person_onnx": 540.0},
        "total_inference_ms": 560.0,
    }
    attempt = target.inference_attempt(observation, pair, 1)
    assert attempt["stage_times_ms"]["person_onnx"] == 540.0
    assert not target.fresh_pair(observation, pair, 1, 0.56)
    assert target.inference_attempt(observation, pair, 2) is None
    observation.monotonic_at = 2.0
    assert target.inference_attempt(observation, pair, 1) is None
    observation.inference_ms = None
    assert target.inference_attempt(observation, pair, 1) is None


def test_slow_auxiliary_never_blocks_primary_or_reuses_previous_frame(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_person(_self, frame):
        entered.set()
        release.wait(2)
        return {
            "candidates": [int(frame[0, 0, 0])],
            "elapsed_ms": 500,
            "timings": {"person_onnx": 500},
        }

    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(target.BehaviorDetector, "_predict_person", slow_person)
    classifier = SimpleNamespace(
        predict=lambda frame: {
            "label": "standing" if frame[0, 0, 0] == 1 else "seated",
            "preprocess_ms": 0,
            "inference_ms": 0,
        }
    )
    detector = target.BehaviorDetector(classifier, classifier, Path("fake.onnx"), person_wait_ms=20)
    try:
        detector.detect(np.ones((4, 4, 3), dtype=np.uint8))
        assert entered.is_set() and not release.is_set()
        first = detector.snapshot()
        assert first["auxiliary_status"] == "timeout"
        assert first["posture"]["label"] == "standing"
        assert first["person_candidates"] == []
        detector.detect(np.full((4, 4, 3), 2, dtype=np.uint8))
        second = detector.snapshot()
        assert second["auxiliary_status"] == "busy"
        assert second["posture"]["label"] == "seated"
        assert second["person_candidates"] == []
        assert second["sequence"] == first["sequence"] + 1
    finally:
        release.set()
        assert detector.close()


def test_slow_drinking_becomes_unknown_without_losing_current_posture(monkeypatch):
    release = threading.Event()

    def classify(frame):
        return {
            "label": "standing" if frame[0, 0, 0] == 1 else "seated",
            "preprocess_ms": 0,
            "inference_ms": 0,
        }

    def slow_drinking(frame):
        release.wait(2)
        return {"label": "drinking", "preprocess_ms": 0, "inference_ms": 500}

    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda *_: {"candidates": [], "elapsed_ms": 1, "timings": {}},
    )
    detector = target.BehaviorDetector(
        SimpleNamespace(predict=classify),
        SimpleNamespace(predict=slow_drinking),
        Path("fake.onnx"),
        person_wait_ms=20,
    )
    try:
        detector.detect(np.ones((4, 4, 3), dtype=np.uint8))
        first = detector.snapshot()
        assert first["drinking_auxiliary_status"] == "timeout"
        assert first["drinking"]["label"] == "unknown"
        assert first["posture"]["label"] == "standing"
        detector.detect(np.full((4, 4, 3), 2, dtype=np.uint8))
        second = detector.snapshot()
        assert second["drinking_auxiliary_status"] == "busy"
        assert second["drinking"]["label"] == "unknown"
        assert second["posture"]["label"] == "seated"
    finally:
        release.set()
        assert detector.close()


def test_optional_diagnostic_frame_does_not_change_classification(monkeypatch):
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda self, frame: {"candidates": [], "elapsed_ms": 0, "timings": {}},
    )
    classifier = SimpleNamespace(
        predict=lambda frame: {
            "label": "seated",
            "preprocess_ms": 0,
            "inference_ms": 0,
        }
    )
    results = []
    for retain in (True, False):
        detector = target.BehaviorDetector(
            classifier,
            classifier,
            Path("fake.onnx"),
            auxiliary_mode="serial",
            retain_diagnostic_frame=retain,
        )
        try:
            frame = np.ones((4, 4, 3), dtype=np.uint8)
            detector.detect(frame)
            frame[:] = 0
            snapshot = detector.snapshot()
            results.append(snapshot["posture"])
            if retain:
                assert snapshot["frame"].all()
                assert snapshot["frame_sha256"]
            else:
                assert "frame" not in snapshot
                assert snapshot["frame_sha256"] is None
        finally:
            detector.close()
    assert results[0] == results[1]


def test_observe_returns_statuses_and_timings_from_same_detection(monkeypatch):
    monkeypatch.setattr(target.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(
        target.BehaviorDetector,
        "_predict_person",
        lambda *_: {
            "candidates": [],
            "elapsed_ms": 3,
            "timings": {"person_onnx": 2},
        },
    )
    posture = SimpleNamespace(
        predict=lambda _frame: {"label": "seated", "preprocess_ms": 1, "inference_ms": 4}
    )
    drinking = SimpleNamespace(
        predict=lambda _frame: {
            "label": "not_drinking",
            "preprocess_ms": 1,
            "inference_ms": 4,
        }
    )
    detector = target.BehaviorDetector(
        posture, drinking, Path("fake.onnx"), auxiliary_mode="serial"
    )
    try:
        result = detector.observe(np.ones((4, 4, 3), dtype=np.uint8), 1.0)
        assert result["auxiliary_status"] == "ready"
        assert result["drinking_auxiliary_status"] == "ready"
        assert result["stage_times_ms"]["posture_preprocess"] == 1
        assert result["stage_times_ms"]["person_onnx"] == 2
    finally:
        detector.close()
