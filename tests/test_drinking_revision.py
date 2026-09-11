import argparse
import hashlib
import json
from unittest.mock import patch

import pytest

from scripts.evaluate_drinking_revision import evaluate


def setup_inputs(tmp_path):
    video = tmp_path / "video.mkv"
    video.write_bytes(b"held-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    annotation = tmp_path / "truth.json"
    annotation.write_text(json.dumps({"videos": [{"path": str(video), "sha256": digest}]}))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"sources": [{"purpose": "holdout", "sha256": digest}]}))
    posture = tmp_path / "posture.json"
    posture.write_text("{}")
    model = tmp_path / "model.json"
    model.write_text("{}")
    frozen = tmp_path / "frozen.json"
    frozen.write_text(
        json.dumps(
            {
                "annotations_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
                "posture_manifest_sha256": hashlib.sha256(posture.read_bytes()).hexdigest(),
                "models": {"candidate": hashlib.sha256(model.read_bytes()).hexdigest()},
            }
        )
    )
    return argparse.Namespace(
        annotations=annotation,
        registry=registry,
        model=[["candidate", model]],
        posture_manifest=posture,
        holdout_freeze=frozen,
        output=tmp_path / "out",
    ), digest


def test_changed_frozen_truth_is_rejected_before_models(tmp_path):
    args, _ = setup_inputs(tmp_path)
    args.annotations.write_text(args.annotations.read_text() + " ")
    with patch("scripts.evaluate_drinking_revision.load_manifest") as loader:
        with pytest.raises(ValueError, match="truth SHA mismatch"):
            evaluate(args)
        loader.assert_not_called()
    assert not args.output.exists()


def test_drinking_training_overlap_is_rejected_before_prediction(tmp_path):
    args, digest = setup_inputs(tmp_path)
    with (
        patch("scripts.evaluate_drinking_revision.load_manifest", return_value={}),
        patch(
            "scripts.evaluate_drinking_revision.model_source_groups", return_value={digest: "train"}
        ),
        patch("scripts.evaluate_drinking_revision.ModelPair") as model,
    ):
        with pytest.raises(ValueError, match="overlaps drinking training"):
            evaluate(args)
        model.assert_not_called()
    assert not args.output.exists()
