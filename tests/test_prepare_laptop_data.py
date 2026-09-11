import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.prepare_laptop_data import build, label_at, validate_intervals


def test_unknown_and_unannotated_frames_are_excluded():
    intervals = [
        {"start": 1, "end": 2, "label": "open"},
        {"start": 2, "end": 3, "label": "unknown"},
    ]
    assert label_at(intervals, 0.5) is None
    assert label_at(intervals, 1.5) == "open"
    assert label_at(intervals, 2.5) is None


def test_partial_is_no_longer_an_accepted_training_label():
    with pytest.raises(ValueError, match="Unrecognized"):
        validate_intervals([{"start": 0, "end": 1, "label": "partial"}], 2)
    with pytest.raises(ValueError, match="Unrecognized"):
        validate_intervals([{"start": 0, "end": 1, "label": "half_open"}], 2)


def test_source_policy_runs_before_output_creation(tmp_path: Path):
    video = tmp_path / "development.mkv"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"), 1, (8, 8))
    writer.write(np.zeros((8, 8, 3), dtype=np.uint8))
    writer.release()
    annotations = tmp_path / "annotations.json"
    annotations.write_text(
        json.dumps(
            {
                "videos": [
                    {
                        "path": str(video),
                        "sha256": "0" * 64,
                        "duration_seconds": 1,
                        "split": "train",
                        "intervals": [{"start": 0, "end": 1, "label": "closed"}],
                    }
                ]
            }
        )
    )
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"sources": []}))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="held out or unregistered"):
        build(annotations, registry, output)
    assert not output.exists()


def test_derived_annotation_requires_exact_base_hash(tmp_path: Path):
    annotations = tmp_path / "derived.json"
    annotations.write_text(
        json.dumps(
            {
                "base_annotations": "base.json",
                "base_annotations_sha256": "0" * 64,
                "force_split": "train",
            }
        )
    )
    (tmp_path / "base.json").write_text(json.dumps({"videos": []}))
    with pytest.raises(ValueError, match="Base annotation SHA mismatch"):
        build(annotations, tmp_path / "registry.json", tmp_path / "output")
