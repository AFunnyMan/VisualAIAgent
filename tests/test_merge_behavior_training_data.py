import hashlib
import json

import pytest

from scripts.merge_behavior_training_data import merge


def write_dataset(root, name, *, source_sha="source-a", sample_path=None):
    root.mkdir()
    image = root / f"{name}.png"
    image.write_bytes(name.encode())
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = {
        "source_groups": {source_sha: "train"},
        "samples": [
            {
                "path": sample_path or image.name,
                "task": "posture",
                "label": "seated",
                "split": "train",
                "source_sha256": source_sha,
                "sha256": digest,
            }
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_merge_preserves_verified_sample_provenance(tmp_path):
    first = write_dataset(tmp_path / "first", "one", source_sha="source-a")
    second = write_dataset(tmp_path / "second", "two", source_sha="source-b")
    result = merge([first, second], tmp_path / "merged")
    assert result["counts"] == {"posture/train/seated": 2}
    assert {sample["source_sha256"] for sample in result["samples"]} == {
        "source-a",
        "source-b",
    }


def test_merge_rejects_sample_with_unrecorded_source_group(tmp_path):
    first = write_dataset(tmp_path / "first", "one", source_sha="source-a")
    second = write_dataset(tmp_path / "second", "two", source_sha="source-b")
    record = json.loads(second.read_text())
    record["samples"][0]["source_sha256"] = "forged-source"
    second.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="not recorded as train"):
        merge([first, second], tmp_path / "merged")


def test_merge_rejects_path_escape(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    first = write_dataset(tmp_path / "first", "one", source_sha="source-a")
    second = write_dataset(
        tmp_path / "second", "two", source_sha="source-b", sample_path="../outside.png"
    )
    record = json.loads(second.read_text())
    record["samples"][0]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    second.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="escapes dataset root"):
        merge([first, second], tmp_path / "merged")
