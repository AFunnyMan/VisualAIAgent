import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_reproducibility_bundle import build_bundle, select_files


def write(root: Path, name: str, content: str = "sample") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_selection_excludes_secrets_environments_and_runtime_databases(tmp_path):
    for name in [
        "data/training-inbox/development/clip.mkv",
        "data/training-inbox/.env",
        "harness/artifacts/behavior-20260909-r01/.venv/pkg.py",
        "data/scene-r04-20260917/runtime/memory.sqlite3",
        "data/scene-r04-20260917/annotations-a.json",
        ".env.example",
    ]:
        write(tmp_path, name, "{}" if name.endswith(".json") else "sample")
    result = select_files(tmp_path, {"datasets": []}, [".env.example"])
    assert "data/training-inbox/development/clip.mkv" in result["included"]
    assert ".env.example" in result["included"]
    for name in [
        "data/training-inbox/.env",
        "harness/artifacts/behavior-20260909-r01/.venv/pkg.py",
        "data/scene-r04-20260917/runtime/memory.sqlite3",
    ]:
        assert name not in result["included"]


def test_referenced_raw_evidence_is_preserved_without_other_runtime_data(tmp_path):
    image = write(tmp_path, "data/acceptance/old/evidence/raw.jpg")
    write(tmp_path, "data/acceptance/old/evidence/unrelated.jpg")
    write(
        tmp_path,
        "harness/artifacts/finetune-20260909/scene/manifest.json",
        json.dumps({"samples": [{"image_path": str(image)}]}),
    )
    result = select_files(tmp_path, {"datasets": []}, [])
    assert "data/acceptance/old/evidence/raw.jpg" in result["included"]
    assert "data/acceptance/old/evidence/unrelated.jpg" not in result["included"]


def test_different_sources_with_same_filename_keep_separate_destinations(tmp_path):
    for parent in ["development-20260909", "development-supplement-20260909"]:
        write(tmp_path, f"harness/artifacts/{parent}/annotations-a.json", "{}")
    result = select_files(tmp_path, {"datasets": []}, [])
    assert len(set(result["included"].values())) == 2


def test_metadata_notes_are_not_treated_as_external_or_long_file_paths(tmp_path):
    outside = write(tmp_path.parent, "not_bundle_data.txt", "outside")
    write(
        tmp_path,
        "harness/artifacts/finetune-20260909/scene/manifest.json",
        json.dumps({"notes": "explanation " * 500, "bad": f"../{outside.name}"}),
    )
    result = select_files(tmp_path, {"datasets": []}, [])
    assert not any(name.startswith("../") for name in result["included"])


def test_existing_output_is_never_overwritten(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    marker = write(output, "keep.txt", "untouched")
    with pytest.raises(FileExistsError):
        build_bundle(tmp_path, output, {"datasets": [], "recipes": []}, source_files=[])
    assert marker.read_text() == "untouched"


def test_build_makes_independent_files_and_records_hash(tmp_path):
    source = tmp_path / "origin"
    source.mkdir()
    original = write(source, "data/training-inbox/development/clip.mkv", "private-video")
    output = tmp_path / "bundle"
    result = build_bundle(source, output, {"datasets": [], "recipes": []}, source_files=[])
    row = next(r for r in result["files"] if r["source"] == str(original.relative_to(source)))
    copied = output / row["destination"]
    assert not copied.is_symlink()
    assert copied.stat().st_ino != original.stat().st_ino
    assert row["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert copied.read_bytes() == original.read_bytes()
    assert (output / "reports/differences.md").is_file()
