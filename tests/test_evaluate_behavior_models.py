import cv2
import numpy as np
import pytest

import scripts.evaluate_behavior_models as evaluator
from scripts.evaluate_behavior_models import (
    accepted_label,
    evaluate_samples,
    model_source_groups,
    score_expected_events,
    sha256,
    verified_source_groups,
)


def test_probability_gate_requires_confidence_and_margin() -> None:
    names = {0: "empty", 1: "seated", 2: "standing"}
    assert accepted_label(np.array([0.05, 0.85, 0.10]), names)[0] == "seated"
    assert accepted_label(np.array([0.10, 0.70, 0.20]), names)[0] == "unknown"
    assert accepted_label(np.array([0.02, 0.55, 0.43]), names)[0] == "unknown"


def test_probability_gate_rejects_nonfinite_or_wrong_width() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(np.array([float("nan"), 0.5]), {0: "a", 1: "b"})
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(np.array([1.0]), {0: "a", 1: "b"})


@pytest.mark.parametrize(
    "probabilities",
    [
        np.array([-0.01, 1.01]),
        np.array([0.2, 0.2]),
        np.array([0.8, 0.3]),
    ],
)
def test_probability_gate_rejects_values_that_are_not_probabilities(probabilities) -> None:
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(probabilities, {0: "a", 1: "b"})


def test_event_scoring_pairs_strictly_one_to_one() -> None:
    expected = [
        {"kind": "stood_up", "start": 2.0, "end": 2.2},
        {"kind": "stood_up", "start": 5.0, "end": 5.2},
        {"kind": "sat_down", "start": 8.0, "end": 8.2},
    ]
    actual = [
        {"kind": "stood_up", "timestamp": 2.1},
        {"kind": "stood_up", "timestamp": 2.15},
        {"kind": "sat_down", "timestamp": 8.6},
    ]
    score = score_expected_events(expected, actual, tolerance=0.25)
    assert (score["tp"], score["fp"], score["fn"]) == (1, 2, 2)
    assert score["per_kind"]["stood_up"] == {"tp": 1, "fp": 1, "fn": 1}


def test_sample_evaluation_does_not_read_another_split(tmp_path) -> None:
    image = tmp_path / "train.png"
    cv2.imwrite(str(image), np.zeros((8, 8, 3), dtype=np.uint8))

    class FakeModel:
        names = {0: "empty", 1: "seated"}

        @staticmethod
        def predict(frame):
            return np.array([0.9, 0.1], dtype=np.float32), 0.0

    manifest = {
        "samples": [
            {
                "split": "train",
                "task": "posture",
                "label": "empty",
                "path": image.name,
                "sha256": sha256(image),
            },
            {
                "split": "test",
                "task": "posture",
                "label": "seated",
                "path": "must-not-be-read.png",
                "sha256": "0" * 64,
            },
        ]
    }
    result, _ = evaluate_samples(tmp_path, manifest, {"posture": FakeModel()}, "train")
    assert result["posture"]["correct"] == 1
    assert result["posture"]["fixed_gate_by_class"]["empty"] == {
        "total": 1,
        "accepted_correct": 1,
        "accepted_wrong": 0,
        "unknown": 0,
    }


def test_source_groups_are_verified_from_sample_records() -> None:
    manifest = {
        "samples": [
            {"source_sha256": "a", "split": "val"},
            {"source_sha256": "a", "split": "val"},
        ],
        "source_groups": {"a": "val"},
    }
    assert verified_source_groups(manifest, context="test") == {"a": "val"}
    manifest["source_groups"] = {"a": "test"}
    with pytest.raises(ValueError, match="disagree"):
        verified_source_groups(manifest, context="test")


def test_source_sample_cannot_cross_splits() -> None:
    manifest = {
        "samples": [
            {"source_sha256": "a", "split": "train"},
            {"source_sha256": "a", "split": "val"},
        ],
        "source_groups": {"a": "train"},
    }
    with pytest.raises(ValueError, match="crosses splits"):
        verified_source_groups(manifest, context="test")


def test_model_sources_come_only_from_fingerprinted_sample_files(tmp_path, monkeypatch) -> None:
    root = tmp_path / "posture"
    image = root / "train/seated/frame.png"
    image.parent.mkdir(parents=True)
    cv2.imwrite(str(image), np.zeros((5, 7, 3), dtype=np.uint8))
    dataset_record = {
        "root": str(root),
        "classes": {0: "empty", 1: "seated", 2: "standing"},
        "counts": {"train": {"empty": 0, "seated": 1, "standing": 0}},
        "sha256": "fingerprint",
    }
    (tmp_path / "manifest.json").write_text(
        __import__("json").dumps(
            {
                "samples": [
                    {
                        "path": "posture/train/seated/frame.png",
                        "task": "posture",
                        "split": "train",
                        "source_sha256": "used-source",
                        "sha256": sha256(image),
                    }
                ],
                "source_groups": {
                    "used-source": "train",
                    "unreferenced-source": "train",
                },
            }
        )
    )
    monkeypatch.setattr(evaluator, "inspect_dataset", lambda *args, **kwargs: dataset_record)
    model_record = {
        "_path": tmp_path / "training-manifest.json",
        "dataset": dataset_record,
        "validation": {"independent_validation_performed": False},
    }
    assert model_source_groups(model_record) == {"used-source": "train"}


def test_model_source_rejects_training_fingerprint_change(tmp_path, monkeypatch) -> None:
    root = tmp_path / "posture"
    root.mkdir()
    monkeypatch.setattr(
        evaluator,
        "inspect_dataset",
        lambda *args, **kwargs: {
            "root": str(root),
            "classes": {0: "empty"},
            "counts": {"train": {"empty": 1}},
            "sha256": "changed",
        },
    )
    record = {
        "_path": tmp_path / "training-manifest.json",
        "dataset": {
            "root": str(root),
            "classes": {0: "empty"},
            "counts": {"train": {"empty": 1}},
            "sha256": "original",
        },
        "validation": {"independent_validation_performed": False},
    }
    with pytest.raises(ValueError, match="fingerprint"):
        model_source_groups(record)
