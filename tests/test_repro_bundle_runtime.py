"""Portable runtime must never depend on the original private checkout."""

import hashlib
import json
from pathlib import Path

import pytest


def fixture_bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir(parents=True)
    old = "/missing/private-original"
    files = []

    def put(source, destination, value):
        path = root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        content = value.encode() if isinstance(value, str) else value
        path.write_bytes(content)
        files.append(
            {
                "source": source,
                "destination": destination,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )

    put("pyproject.toml", "source/pyproject.toml", '[project]\nname="fixture"\nversion="1"\n')
    put(
        "uv.lock",
        "source/uv.lock",
        "version = 1\n"
        + "\n".join(
            f'[[package]]\nname = "{name}"\nversion = "1.0"\n'
            for name in ("torch", "ultralytics", "onnx", "onnxslim")
        ),
    )
    put("app.py", "source/app.py", "raise RuntimeError('must not launch')\n")
    put(
        "visual_ai_agent/object_models.py",
        "source/visual_ai_agent/object_models.py",
        """from pathlib import Path
from dataclasses import dataclass
@dataclass
class ObjectModelPreset:
    id: str
    label: str
    path: Path
    sha256: str
    experimental: bool
OBJECT_MODEL_PRESETS = ()
""",
    )
    put(
        "visual_ai_agent/ui_controls.py",
        "source/visual_ai_agent/ui_controls.py",
        """from pathlib import Path
known = Path("harness/artifacts/behavior-20260910-r02")
""",
    )
    put(
        "scripts/train_scene_model.py",
        "source/scripts/train_scene_model.py",
        "raise RuntimeError('TRAINING SHOULD NOT EXECUTE')\n",
    )
    put("data/scene/images/train/one.jpg", "datasets/scene/images/train/one.jpg", b"train image")
    put("data/scene/labels/train/one.txt", "datasets/scene/labels/train/one.txt", "0 .5 .5 .2 .2\n")
    put("data/scene/images/val/two.jpg", "datasets/scene/images/val/two.jpg", b"val image")
    put("data/scene/labels/val/two.txt", "datasets/scene/labels/val/two.txt", "0 .5 .5 .2 .2\n")
    put(
        "data/scene/dataset.yaml",
        "datasets/scene/dataset.yaml",
        f"path: {old}/data/scene\ntrain: images/train\nval: images/val\n"
        "names: [bottle, cup, cell phone]\n",
    )
    put(
        "data/scene/manifest.json",
        "datasets/scene/manifest.json",
        json.dumps({"samples": [{"image_path": f"{old}/data/scene/images/train/one.jpg"}]}),
    )
    for name in ("official", "r04", "r05", "start"):
        put(f"models/{name}.onnx", f"models/{name}.onnx", name.encode())
    put("models/start.pt", "models/start.pt", b"checkpoint")
    for name in ("posture-r02", "drinking-r02", "posture-r04", "drinking-r03"):
        put(f"runs/{name}/model.onnx", f"models/{name}/model.onnx", name.encode())
        put(
            f"runs/{name}/training-manifest.json",
            f"models/{name}/training-manifest.json",
            json.dumps({"onnx": f"{old}/runs/{name}/model.onnx"}),
        )
    profiles = {
        "official": {"object_model": "models/official.onnx"},
        "desktop-reference": {
            "object_model": "models/r05.onnx",
            "posture_manifest": "runs/posture-r02/training-manifest.json",
            "drinking_manifest": "runs/drinking-r02/training-manifest.json",
        },
        "candidates": {
            "object_model": "models/r04.onnx",
            "posture_manifest": "runs/posture-r04/training-manifest.json",
            "drinking_manifest": "runs/drinking-r03/training-manifest.json",
        },
    }
    catalog = {
        "schema": 1,
        "original_root": old,
        "files": files,
        "profiles": profiles,
        "datasets": [
            {
                "id": "scene",
                "task": "objects",
                "source_dir": "data/scene",
                "manifest": "data/scene/manifest.json",
                "counts": {"train": 1, "val": 1},
            }
        ],
        "recipes": [
            {
                "id": "scene-r01",
                "task": "objects",
                "script": "scripts/train_scene_model.py",
                "dataset_id": "scene",
                "source_weights": "models/start.pt",
                "reference_weights": "models/start.pt",
                "reference_onnx": "models/r04.onnx",
                "dependencies": [],
                "parameters": {"epochs": 1, "imgsz": 640, "batch": 1},
                "argv": [
                    "--data",
                    f"{old}/data/scene/dataset.yaml",
                    "--weights",
                    f"{old}/models/start.pt",
                    "--epochs",
                    "1",
                    "--output",
                    f"{old}/runs/reproduction",
                    "--name",
                    "fixture",
                ],
            }
        ],
    }
    (root / "provenance").mkdir()
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    return root, catalog


def runtime():
    from scripts.repro_bundle_runtime import Bundle

    return Bundle


def test_prepare_relocates_active_metadata_and_never_modifies_frozen_evidence(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    frozen = (root / "datasets/scene/manifest.json").read_bytes()
    bundle = runtime()(root)
    result = bundle.prepare()
    assert result["status"] == "prepared"
    assert (root / "datasets/scene/manifest.json").read_bytes() == frozen
    assert (root / "work/project/.env").read_text() == ""
    metadata = bundle.resolve("data/scene/manifest.json", metadata=True)
    assert json.loads(metadata.read_text())["samples"][0]["image_path"] == str(
        root / "datasets/scene/images/train/one.jpg"
    )
    assert catalog["original_root"] not in metadata.read_text()
    assert not any(p.is_symlink() for p in (root / "work").rglob("*"))


def test_missing_or_changed_payload_fails_before_prepare(tmp_path):
    root, _ = fixture_bundle(tmp_path)
    (root / "models/start.pt").unlink()
    with pytest.raises((ValueError, FileNotFoundError), match="start.pt"):
        runtime()(root).prepare()


def test_profiles_use_bundle_models_and_clean_api_environment(tmp_path):
    root, _ = fixture_bundle(tmp_path)
    bundle = runtime()(root)
    bundle.prepare()
    inherited = {
        "PATH": "/bin",
        "VAA_API_KEY": "secret",
        "OPENAI_API_KEY": "secret",
        "VAA_MODEL_PATH": "/old/model",
        "VAA_BEHAVIOR_ENABLED": "true",
    }
    official = bundle.launch_plan("official", environ=inherited)
    assert official["env"]["VAA_API_KEY"] == ""
    assert "OPENAI_API_KEY" not in official["env"]
    assert official["env"]["VAA_BEHAVIOR_ENABLED"] == "false"
    assert official["env"]["VAA_MODEL_PATH"] == str(root / "models/official.onnx")
    desktop = bundle.launch_plan("desktop-reference", environ=inherited)
    assert desktop["env"]["VAA_CAMERA_WIDTH"] == "1280"
    assert desktop["env"]["VAA_CUP_SCALE_RECHECK"] == "true"
    assert desktop["env"]["VAA_SILVER_OBJECT_IGNORE"] == "false"
    assert desktop["env"]["VAA_DATA_DIR"] == str(root / "work/runtime/desktop-reference")
    manifest = Path(desktop["env"]["VAA_BEHAVIOR_POSTURE_MANIFEST"])
    assert json.loads(manifest.read_text())["onnx"] == str(root / "models/posture-r02/model.onnx")
    candidate = bundle.launch_plan("candidates", environ=inherited)
    assert candidate["experimental"] is True
    assert candidate["env"]["VAA_LAPTOP_ENABLED"] == "false"
    assert "--locked" in candidate["argv"]
    assert candidate["env"]["VAA_MODEL_PATH"] == str(root / "models/r04.onnx")


def test_check_only_validates_recipe_without_importing_or_running_trainer(tmp_path, monkeypatch):
    root, catalog = fixture_bundle(tmp_path)
    bundle = runtime()(root)
    bundle.prepare()

    def forbidden(*args, **kwargs):
        raise AssertionError("check-only must never run a subprocess")

    monkeypatch.setattr("subprocess.run", forbidden)
    result = bundle.train("scene-r01", check_only=True)
    assert result["status"] == "not_trained"
    assert catalog["original_root"] not in json.dumps(result)
    assert result["argv"][result["argv"].index("--weights") + 1] == str(root / "models/start.pt")
    assert not (root / "work/training/scene-r01").exists()


def test_recipe_rejects_missing_input_and_unknown_dependency(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    catalog["recipes"][0]["dependencies"] = ["missing-r00"]
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    with pytest.raises(ValueError, match="dependency"):
        bundle.train("scene-r01", check_only=True)


def test_allow_api_reads_only_explicit_bundle_dotenv(tmp_path):
    root, _ = fixture_bundle(tmp_path)
    bundle = runtime()(root)
    bundle.prepare()
    (root / ".env").write_text(
        'VAA_API_KEY=own-key\nVAA_API_BASE_URL=https://example.com/v1\nVAA_AGENT_MODEL="own-model"\n'
    )
    disabled = bundle.launch_plan("official", environ={})
    assert disabled["env"]["VAA_API_KEY"] == ""
    enabled = bundle.launch_plan("official", environ={}, allow_api=True)
    assert enabled["env"]["VAA_API_KEY"] == "own-key"
    assert enabled["env"]["VAA_AGENT_MODEL"] == "own-model"
    assert (root / "work/project/.env").read_text() == ""


def test_check_only_detects_deleted_label_after_prepare(tmp_path):
    root, _ = fixture_bundle(tmp_path)
    bundle = runtime()(root)
    bundle.prepare()
    (root / "datasets/scene/labels/train/one.txt").unlink()
    with pytest.raises(FileNotFoundError, match="label"):
        bundle.train("scene-r01", check_only=True)


def test_check_only_rejects_recipe_weights_that_differ_from_record(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    recipe = catalog["recipes"][0]
    recipe["argv"][recipe["argv"].index("--weights") + 1] = "models/r04.onnx"
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    with pytest.raises(ValueError, match="weights"):
        bundle.train("scene-r01", check_only=True)


def test_prepared_presets_resolve_without_access_to_original_checkout(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    source = root / "source/visual_ai_agent/object_models.py"
    source.write_text(
        source.read_text().replace(
            "OBJECT_MODEL_PRESETS = ()",
            """OBJECT_MODEL_PRESETS = (
    ObjectModelPreset(id="r05", label="R05", path=Path("models/r05.onnx"),
                      sha256="fixture", experimental=True),
)""",
        )
    )
    row = next(row for row in catalog["files"] if row["source"].endswith("object_models.py"))
    row.update(size=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    import runpy

    presets = runpy.run_path(str(root / "work/project/visual_ai_agent/object_models.py"))
    assert presets["OBJECT_MODEL_PRESETS"][0].path == root / "models/r05.onnx"


def test_explicit_training_copies_mutable_data_before_starting_trainer(tmp_path, monkeypatch):
    root, _ = fixture_bundle(tmp_path)
    bundle = runtime()(root)
    bundle.prepare()
    captured = {}

    def trainer_boundary(command, *, cwd, env, check):
        data = Path(command[command.index("--data") + 1])
        captured["data"] = data
        captured["env"] = env
        # A real trainer may write a cache beside its images.
        (data.parent / "trainer.cache").write_text("cache")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("subprocess.run", trainer_boundary)
    result = bundle.train("scene-r01", check_only=False)
    assert result["status"] == "completed"
    assert captured["data"].is_relative_to(root / "work/training-data/scene-r01")
    assert not (root / "datasets/scene/trainer.cache").exists()
    assert str(root / "work/training-data/scene-r01") in captured["data"].read_text()
    assert captured["env"]["UV_PROJECT_ENVIRONMENT"] == str(root / "work/project/.export-venv")
    assert captured["env"]["VAA_API_KEY"] == ""


def test_metadata_relocation_preserves_task_and_class_labels(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    path = root / "datasets/scene/manifest.json"
    path.write_text(
        json.dumps({"task": "images", "label": "images", "path": "images/train/one.jpg"})
    )
    row = next(row for row in catalog["files"] if row["source"] == "data/scene/manifest.json")
    row.update(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    relocated = json.loads(bundle.resolve("data/scene/manifest.json", metadata=True).read_text())
    assert relocated["task"] == "images"
    assert relocated["label"] == "images"
    assert relocated["path"] == str(root / "datasets/scene/images/train/one.jpg")


def test_invalid_history_cannot_start_training(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    catalog["recipes"][0]["validity"] = "invalid"
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    with pytest.raises(ValueError, match="invalid"):
        bundle.train("scene-r01", check_only=False)


def test_verify_cli_forwards_bundle_and_output_to_standalone_verifier(tmp_path):
    from scripts.repro_bundle_runtime import main

    root, _ = fixture_bundle(tmp_path)
    verifier = root / "tools/verify.py"
    verifier.parent.mkdir()
    verifier.write_text("""import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--bundle", required=True)
p.add_argument("--output", required=True)
a=p.parse_args()
Path(a.output).write_text(json.dumps({"bundle": a.bundle}))
""")
    output = tmp_path / "verification.json"
    assert main(["--root", str(root), "verify", "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == {"bundle": str(root)}


def test_nested_bundle_under_original_root_is_valid_without_old_checkout_access(tmp_path):
    root, catalog = fixture_bundle(tmp_path)
    old_root = catalog["original_root"]
    catalog["original_root"] = str(tmp_path)
    for row in catalog["files"]:
        path = root / row["destination"]
        if path.suffix in {".json", ".yaml"}:
            path.write_text(path.read_text().replace(old_root, str(tmp_path)))
            row.update(
                size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            )
    catalog["recipes"][0]["argv"] = [
        value.replace(old_root, str(tmp_path)) for value in catalog["recipes"][0]["argv"]
    ]
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    assert bundle.train("scene-r01", check_only=True)["status"] == "not_trained"


def test_dataset_label_lookup_ignores_images_named_parent_directory(tmp_path):
    root, _ = fixture_bundle(tmp_path / "images")
    bundle = runtime()(root)
    bundle.prepare()
    assert bundle.train("scene-r01", check_only=True)["dataset"]["images"] == {"train": 1, "val": 1}


def test_working_trainer_adapts_legacy_label_lookup_without_changing_source_snapshot(tmp_path):
    import runpy

    root, catalog = fixture_bundle(tmp_path / "images")
    script = root / "source/scripts/train_scene_model.py"
    script.write_text("""from pathlib import Path

def legacy_label(path):
    parts = list(path.parts)
    parts[parts.index("images")] = "labels"
    return Path(*parts).with_suffix(".txt")
""")
    frozen = script.read_bytes()
    row = next(row for row in catalog["files"] if row["source"] == "scripts/train_scene_model.py")
    row.update(size=len(frozen), sha256=hashlib.sha256(frozen).hexdigest())
    (root / "provenance/catalog.json").write_text(json.dumps(catalog))
    bundle = runtime()(root)
    bundle.prepare()
    adapted = runpy.run_path(str(root / "work/project/scripts/train_scene_model.py"))
    assert adapted["legacy_label"](root / "datasets/scene/images/train/one.jpg") == (
        root / "datasets/scene/labels/train/one.txt"
    )
    assert script.read_bytes() == frozen


def test_relocated_bundle_supports_unicode_and_spaces_in_folder_name(tmp_path):
    root, _ = fixture_bundle(tmp_path / "训练资料 with space")
    bundle = runtime()(root)
    bundle.prepare()
    assert bundle.train("scene-r01", check_only=True)["status"] == "not_trained"
