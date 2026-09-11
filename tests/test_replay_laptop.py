import hashlib
import json

import pytest

from scripts.replay_laptop import replay


def test_lid_development_replay_rejects_renamed_holdout_before_loading_model(tmp_path):
    video = tmp_path / "looks-like-development.mkv"
    video.write_bytes(b"holdout-source")
    registry = tmp_path / "sources.json"
    registry.write_text(json.dumps({"sources": [{
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(), "purpose": "holdout",
    }]}))
    output = tmp_path / "result.json"
    with pytest.raises(ValueError, match="held out or unregistered"):
        replay(tmp_path / "nonexistent-model.json", video, output, registry)
    assert not output.exists()
