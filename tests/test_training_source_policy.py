import hashlib
import json

import pytest

from scripts.training_source_policy import verify_development_sources


def test_renamed_holdout_cannot_become_training(tmp_path):
    development = tmp_path / "dev.mkv"
    development.write_bytes(b"development")
    renamed = tmp_path / "looks_like_training.mkv"
    renamed.write_bytes(b"holdout")
    registry = tmp_path / "sources.json"
    registry.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "sha256": hashlib.sha256(b"development").hexdigest(),
                        "purpose": "development",
                    },
                    {"sha256": hashlib.sha256(b"holdout").hexdigest(), "purpose": "holdout"},
                ]
            }
        )
    )
    assert len(verify_development_sources([development], registry)) == 1
    with pytest.raises(ValueError, match="held out"):
        verify_development_sources([renamed], registry)
    renamed.write_bytes(b"unregistered")
    with pytest.raises(ValueError, match="unregistered"):
        verify_development_sources([renamed], registry)
