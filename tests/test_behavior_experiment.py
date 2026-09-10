import json

import numpy as np
import pytest

from scripts.behavior_experiment import load_person_evidence, sample_rows
from scripts.evaluate_behavior_models import ModelPair, sha256


def test_temporal_ablation_selects_same_frames_without_upsampling():
    rows = [{"timestamp": i / 10, "frame_index": i * 6} for i in range(11)]
    assert [row["frame_index"] for row in sample_rows(rows, 2, 10)] == [0, 30, 60]
    assert [row["frame_index"] for row in sample_rows(rows, 5, 10)] == [0, 12, 24, 36, 48, 60]
    with pytest.raises(ValueError, match="exceeds"):
        sample_rows(rows, 20, 10)


def test_roi_runtime_rejects_changed_source_size_before_model_inference():
    pair = object.__new__(ModelPair)
    pair.expected_source_size = [1920, 1080]
    with pytest.raises(ValueError, match="recalibration"):
        pair.predict(np.zeros((720, 1280, 3), dtype=np.uint8))


@pytest.mark.parametrize("failure", [None, "nan", "time", "key", "duplicate", "boolean", "hash"])
def test_person_evidence_requires_integrity_and_exact_finite_join(tmp_path, failure):
    path = tmp_path / "person.jsonl"
    row = {
        "source_sha256": "source",
        "frame_index": 6,
        "timestamp_s": 0.1,
        "person_track_supported": True,
    }
    if failure == "nan":
        row["timestamp_s"] = float("nan")
    if failure == "time":
        row["timestamp_s"] = 0.2
    if failure == "key":
        row["frame_index"] = 12
    if failure == "boolean":
        row["person_track_supported"] = "true"
    path.write_text((json.dumps(row) + "\n") * (2 if failure == "duplicate" else 1))
    (tmp_path / "metadata.json").write_text(
        json.dumps(
            {
                "predictions_file": path.name,
                "predictions_sha256": "wrong" if failure == "hash" else sha256(path),
            }
        )
    )
    rows = [{"source_sha256": "source", "frame_index": 6, "timestamp": 0.1}]
    if failure is None:
        assert load_person_evidence(path, rows)[("source", 6)]["person_track_supported"]
    else:
        with pytest.raises(ValueError):
            load_person_evidence(path, rows)
