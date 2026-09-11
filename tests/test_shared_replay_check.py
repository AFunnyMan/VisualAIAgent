import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.check_shared_replay import run_replay


def test_holdout_rejected_before_runtime_or_output(tmp_path):
    video = tmp_path / "renamed.mkv"
    video.write_bytes(b"independent-video")
    registry = tmp_path / "sources.json"
    registry.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                        "purpose": "holdout",
                        "path": str(video),
                    }
                ]
            }
        )
    )
    args = argparse.Namespace(video=video, registry=registry, output=tmp_path / "out")
    with patch("scripts.check_shared_replay.run") as runner:
        with pytest.raises(ValueError, match="held out or unregistered"):
            run_replay(args)
        runner.assert_not_called()
    assert not args.output.exists()


def test_replay_transport_patch_restored_after_runtime_failure(tmp_path):
    from visual_ai_agent import runtime

    original = runtime.CameraSource
    video = tmp_path / "dev.mkv"
    video.write_bytes(b"development")
    registry = tmp_path / "sources.json"
    registry.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                        "purpose": "development",
                        "path": str(video),
                    }
                ]
            }
        )
    )
    args = argparse.Namespace(video=video, registry=registry, output=Path(tmp_path / "out"))
    with patch("scripts.check_shared_replay.run", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError, match="failed"):
            run_replay(args)
    assert runtime.CameraSource is original


def test_shared_consumers_preserve_replay_provenance(tmp_path):
    from visual_ai_agent.vision import ReplaySource, SharedCamera

    camera = SharedCamera(ReplaySource(tmp_path / "not-opened.mkv"))
    assert camera.subscribe().source_name == "replay"
    assert camera.subscribe().source_name == "replay"
