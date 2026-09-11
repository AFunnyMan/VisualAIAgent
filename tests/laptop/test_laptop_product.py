import hashlib
import json
import time
from datetime import UTC, datetime
from threading import Event

import numpy as np
import pytest

from visual_ai_agent.behavior_models import LaptopObservation
from visual_ai_agent.laptop import (
    LaptopCapabilityError,
    LaptopDetector,
    LaptopWorker,
    _load_presence_manifest,
    load_laptop_capability_manifest,
)
from visual_ai_agent.vision import SharedFrame


def _preprocessing():
    return {
        "layout": "NCHW",
        "dtype": "float32",
        "color": "RGB",
        "resize": "aspect-ratio-preserving letterbox",
        "range": [0.0, 1.0],
        "fill_rgb": [114, 114, 114],
        "imgsz": 320,
        "expected_source_frame_size": [1920, 1080],
        "roi": None,
    }


def _write_capability(tmp_path):
    state_onnx = tmp_path / "state.onnx"
    presence_onnx = tmp_path / "presence.onnx"
    state_onnx.write_bytes(b"state")
    presence_onnx.write_bytes(b"presence")
    state = {
        "status": "completed",
        "preprocessing": _preprocessing(),
        "dataset": {"classes": {"0": "closed", "1": "open"}},
        "onnx": state_onnx.name,
        "onnx_sha256": hashlib.sha256(state_onnx.read_bytes()).hexdigest(),
    }
    presence = {
        "status": "completed",
        "preprocessing": _preprocessing(),
        "dataset": {"classes": {"0": "present", "1": "absent", "2": "occluded"}},
        "onnx": presence_onnx.name,
        "onnx_sha256": hashlib.sha256(presence_onnx.read_bytes()).hexdigest(),
    }
    state_path = tmp_path / "state.json"
    presence_path = tmp_path / "presence.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    presence_path.write_text(json.dumps(presence), encoding="utf-8")
    acceptance = {
        "schema_version": 1,
        "capability": "laptop_lid_state_with_presence",
        "status": "accepted",
        "model_version": "laptop-test",
        "independent": True,
        "state_transitions_accepted": True,
        "scene_id": "desk-fixed",
        "expected_source_frame_size": [1920, 1080],
        "state_roi": None,
        "presence_roi": None,
        "state_manifest_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
        "presence_manifest_sha256": hashlib.sha256(presence_path.read_bytes()).hexdigest(),
        "state_onnx_sha256": state["onnx_sha256"],
        "presence_onnx_sha256": presence["onnx_sha256"],
        "source_registry_sha256": "a" * 64,
        "truth_sha256": "b" * 64,
        "thresholds": {
            "presence_confidence": 0.75,
            "state_confidence": 0.75,
            "state_margin": 0.2,
        },
        "timing": {
            "confirm_seconds": 0.5,
            "max_gap": 0.25,
            "visible_transition_bridge_seconds": 0.15,
        },
        "results": {
            "absent_cases": 3,
            "occluded_cases": 2,
            "absent_false_present": 0,
            "occluded_false_present": 0,
        },
    }
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    capability = {
        "schema_version": 1,
        "capability": "laptop_lid_state_with_presence",
        "status": "accepted",
        "experimental": True,
        "model_version": "laptop-test",
        "scene_id": "desk-fixed",
        "expected_source_frame_size": [1920, 1080],
        "state_manifest": "state.json",
        "state_manifest_sha256": acceptance["state_manifest_sha256"],
        "presence_manifest": "presence.json",
        "presence_manifest_sha256": acceptance["presence_manifest_sha256"],
        "acceptance_report": "acceptance.json",
        "acceptance_report_sha256": hashlib.sha256(acceptance_path.read_bytes()).hexdigest(),
    }
    path = tmp_path / "capability.json"
    path.write_text(json.dumps(capability), encoding="utf-8")
    return path, capability, acceptance_path, acceptance


def test_capability_requires_independent_absent_and_occlusion_acceptance(tmp_path):
    path, record, acceptance_path, acceptance = _write_capability(tmp_path)
    loaded = load_laptop_capability_manifest(path)
    assert loaded["_model_version"] == "laptop-test"
    assert set(loaded["_presence"]["_names"].values()) == {
        "present",
        "absent",
        "occluded",
    }
    with pytest.raises(LaptopCapabilityError, match="scene geometry"):
        load_laptop_capability_manifest(path, expected_scene_id="another-desk")
    with pytest.raises(LaptopCapabilityError, match="scene geometry"):
        load_laptop_capability_manifest(path, expected_source_size=(1280, 720))

    acceptance["results"]["absent_false_present"] = 1
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    record["acceptance_report_sha256"] = hashlib.sha256(acceptance_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(LaptopCapabilityError, match="presence acceptance"):
        load_laptop_capability_manifest(path)


def test_presence_manifest_rejects_duplicate_classes_and_unbound_geometry(tmp_path):
    _path, _record, _acceptance_path, _acceptance = _write_capability(tmp_path)
    presence_path = tmp_path / "presence.json"
    presence = json.loads(presence_path.read_text(encoding="utf-8"))
    presence["dataset"]["classes"]["3"] = "present"
    presence_path.write_text(json.dumps(presence), encoding="utf-8")
    with pytest.raises(LaptopCapabilityError, match="incompatible"):
        _load_presence_manifest(presence_path)


def test_capability_acceptance_binds_runtime_timing(tmp_path):
    path, record, acceptance_path, acceptance = _write_capability(tmp_path)
    acceptance["timing"]["visible_transition_bridge_seconds"] = 0.2
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    record["acceptance_report_sha256"] = hashlib.sha256(
        acceptance_path.read_bytes()
    ).hexdigest()
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(LaptopCapabilityError, match="presence acceptance"):
        load_laptop_capability_manifest(path)


def test_current_r05_training_manifest_is_not_a_product_capability():
    path = "harness/artifacts/laptop-20260912-r05-binary/training-manifest.json"
    with pytest.raises(LaptopCapabilityError):
        load_laptop_capability_manifest(path)


class QueueClassifier:
    def __init__(self, labels):
        self.labels = list(labels)
        self.calls = 0

    def predict(self, _frame):
        self.calls += 1
        label, confidence, margin = self.labels.pop(0)
        return {"label": label, "confidence": confidence, "margin": margin}


class QueueGate:
    qualified = True
    model_version = "presence-test"

    def __init__(self, results):
        self.results = list(results)

    def predict(self, _frame):
        return self.results.pop(0)


def visible(confidence=0.95):
    return {"visible": True, "occluded": False, "confidence": confidence, "label": "present"}


def test_absent_occluded_and_low_confidence_never_reach_lid_classifier():
    classifier = QueueClassifier([("closed", 0.99, 0.9)])
    detector = LaptopDetector(
        classifier,
        QueueGate(
            [
                {"visible": False, "occluded": False, "confidence": 0.99, "label": "absent"},
                {"visible": False, "occluded": True, "confidence": 0.99, "label": "occluded"},
                visible(0.4),
            ]
        ),
    )
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    results = [detector.observe(frame, index / 10) for index in range(3)]
    assert classifier.calls == 0
    assert all(item["state"] == "unknown" and not item["events"] for item in results)


def test_presence_protocol_requires_present_label_even_if_visible_flag_is_true():
    classifier = QueueClassifier([("closed", 0.99, 0.9)])
    detector = LaptopDetector(
        classifier,
        QueueGate(
            [{"visible": True, "occluded": False, "confidence": 0.99, "label": "absent"}]
        ),
    )
    result = detector.observe(np.zeros((8, 8, 3), dtype=np.uint8), 0)
    assert result["state"] == "unknown"
    assert result["presence_verified"] is False
    assert classifier.calls == 0


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_non_finite_or_out_of_range_presence_score_fails_closed(score):
    classifier = QueueClassifier([("closed", 0.99, 0.9)])
    detector = LaptopDetector(classifier, QueueGate([visible(score)]))
    result = detector.observe(np.zeros((8, 8, 3), dtype=np.uint8), 0)
    assert result["state"] == "unknown"
    assert result["presence_verified"] is False
    assert classifier.calls == 0


@pytest.mark.parametrize(
    "prediction",
    [
        ("closed", float("nan"), 0.9),
        ("closed", float("inf"), 0.9),
        ("closed", 1.1, 0.9),
        ("closed", 0.99, float("nan")),
        ("closed", 0.99, float("inf")),
        ("closed", 0.99, -0.1),
        ("closed", 0.99, 1.1),
    ],
)
def test_non_finite_or_out_of_range_state_score_is_unknown(prediction):
    detector = LaptopDetector(QueueClassifier([prediction]), QueueGate([visible()]))
    result = detector.observe(np.zeros((8, 8, 3), dtype=np.uint8), 0)
    assert result["state"] == "unknown"
    assert not result["events"]


def test_only_continuous_visible_high_confidence_states_form_transition():
    labels = [("open", 0.95, 0.8)] * 6 + [("closed", 0.95, 0.8)] * 6
    detector = LaptopDetector(QueueClassifier(labels), QueueGate([visible()] * 12))
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    rows = [detector.observe(frame, index / 10, scene_id="desk") for index in range(12)]
    assert [event for row in rows for event in row["events"]] == ["laptop_closed"]

    interrupted = LaptopDetector(
        QueueClassifier([("open", 0.95, 0.8)] * 6 + [("closed", 0.95, 0.8)] * 6),
        QueueGate(
            [visible()] * 6
            + [{"visible": False, "occluded": False, "confidence": 0.99, "label": "absent"}]
            + [visible()] * 6
        ),
    )
    rows = [interrupted.observe(frame, index / 10, scene_id="desk") for index in range(13)]
    assert not any(row["events"] for row in rows)


def test_single_state_rejection_bridges_only_with_continuous_verified_visibility():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    verified = LaptopDetector(
        QueueClassifier(
            [("open", 0.95, 0.8)] * 6
            + [("unknown", 0.5, 0.1)]
            + [("closed", 0.95, 0.8)] * 6
        ),
        QueueGate([visible()] * 13),
    )
    rows = [verified.observe(frame, index / 10, scene_id="desk") for index in range(13)]
    assert rows[6]["state"] == "unknown"
    assert rows[6]["state_reason"] == "visible_transition_uncertain"
    assert [event for row in rows for event in row["events"]] == ["laptop_closed"]

    unverified = LaptopDetector(
        QueueClassifier(
            [("open", 0.95, 0.8)] * 6 + [("closed", 0.95, 0.8)] * 6
        ),
        QueueGate(
            [visible()] * 6
            + [{"visible": False, "occluded": False, "confidence": 0.99, "label": "absent"}]
            + [visible()] * 6
        ),
    )
    rows = [unverified.observe(frame, index / 10, scene_id="desk") for index in range(13)]
    assert rows[6]["state_reason"] == "unknown"
    assert not any(row["events"] for row in rows)


def test_laptop_record_rejects_closed_without_current_presence():
    with pytest.raises(ValueError, match="presence"):
        LaptopObservation(
            observed_at=datetime.now(UTC),
            monotonic_at=1,
            status="running",
            fresh=True,
            state="closed",
            presence_verified=False,
            model_version="state",
            presence_model_version="presence",
            scene_id="desk",
            source="test",
        )


def test_worker_rejects_stale_shared_sample_and_preserves_replay_source():
    called = []
    received = []
    ready = Event()

    class ClassifierThatMustNotRun:
        def predict(self, _frame):
            called.append(True)
            raise AssertionError("stale frames must not be classified")

    class Source:
        source_name = "replay"
        status = "stopped"
        last_error = None

        def __init__(self):
            self.once = False

        def open(self):
            pass

        def read_sample(self):
            if self.once:
                time.sleep(0.01)
                return None
            self.once = True
            return SharedFrame(
                sequence=1,
                frame=np.zeros((8, 8, 3), dtype=np.uint8),
                captured_at=datetime.now(UTC),
                monotonic_at=time.monotonic() - 1,
            )

        def close(self):
            pass

    detector = LaptopDetector(ClassifierThatMustNotRun(), QueueGate([]))

    def callback(observation, _jpeg):
        received.append(observation)
        ready.set()

    worker = LaptopWorker(
        detector,
        Source(),
        callback,
        model_version="state",
        presence_model_version="presence",
        scene_id="desk",
    )
    worker.start()
    assert ready.wait(2)
    worker.stop()
    assert not called
    assert received[0].status == "stale"
    assert received[0].state == "unknown"
    assert received[0].source == "replay"
