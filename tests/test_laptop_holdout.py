import argparse
import hashlib
import json

import pytest

from scripts import evaluate_laptop_holdout as evaluator


def test_laptop_holdout_rejects_source_used_for_training_or_selection(tmp_path, monkeypatch):
    video = tmp_path / "holdout.mkv"
    video.write_bytes(b"isolated-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"video": str(video), "video_sha256": digest}))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"sources": [{"sha256": digest, "purpose": "holdout"}]}))
    model = tmp_path / "model.json"
    model.write_text("{}")
    monkeypatch.setattr(evaluator, "load_behavior_manifest", lambda *args: {})
    monkeypatch.setattr(evaluator, "model_source_groups", lambda *args: {digest: "val"})
    output = tmp_path / "results"
    args = argparse.Namespace(
        truth=truth, truth_sha256=evaluator.sha256(truth), registry=registry,
        model=model, model_sha256=evaluator.sha256(model), output=output,
    )
    with pytest.raises(ValueError, match="training or selection"):
        evaluator.evaluate(args)
    assert not output.exists()
