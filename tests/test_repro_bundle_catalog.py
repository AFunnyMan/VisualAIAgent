import hashlib
import json
from pathlib import Path

from scripts.repro_bundle_catalog import RECORDED_ROOT, discover_catalog


def write_json(root: Path, path: str, value: dict) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value))
    return target


def artifact(root: Path, path: str) -> tuple[str, str]:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.encode())
    return str(target), hashlib.sha256(target.read_bytes()).hexdigest()


def fixture_fingerprint(data_path: Path, *, classify: bool) -> str:
    digest = hashlib.sha256()
    files = (
        sorted((data_path / "train").rglob("*.png"))
        if classify
        else sorted(
            [
                data_path / "dataset.yaml",
                *data_path.glob("images/*/*"),
                *data_path.glob("labels/*/*"),
            ]
        )
    )
    for path in files:
        identity = path.relative_to(data_path).as_posix() if classify else str(path)
        digest.update(identity.encode())
        if not classify:
            digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def record(root: Path, run: str, *, classify: bool = False, data: str = "data/dataset") -> dict:
    data_path = root / data
    data_path.mkdir(parents=True, exist_ok=True)
    write_json(root, f"{data}/manifest.json", {"counts": {"train": 2}})
    weights, source_hash = artifact(root, "models/start.pt")
    checkpoint, final_hash = artifact(root, f"{run}/weights/last.pt")
    onnx, onnx_hash = artifact(root, f"{run}/weights/last.onnx")
    params = {
        "epochs": 20,
        "batch": 4,
        "imgsz": 320,
        "seed": 7,
        "freeze": 5,
        "device": "cpu",
        "val": False,
    }
    value = {
        "status": "completed",
        "parameters": params,
        "source_weights": weights,
        "source_weights_sha256": source_hash,
        "selected_checkpoint" if classify else "trained_weights": checkpoint,
        "selected_checkpoint_sha256" if classify else "trained_weights_sha256": final_hash,
        "onnx" if classify else "exported_onnx": onnx,
        "onnx_sha256" if classify else "exported_onnx_sha256": onnx_hash,
    }
    if classify:
        artifact(root, f"{data}/train/a/a.png")
        artifact(root, f"{data}/train/b/b.png")
        value.update(
            dataset={
                "root": str(data_path),
                "counts": {"train": {"a": 1, "b": 1}},
                "sha256": fixture_fingerprint(data_path, classify=True),
            },
            checkpoint_selection="last_epoch",
        )
    else:
        for split in ("train", "val"):
            artifact(root, f"{data}/images/{split}/{split}.png")
            label = root / data / "labels" / split / f"{split}.txt"
            label.parent.mkdir(parents=True, exist_ok=True)
            label.write_text("0 0.5 0.5 0.2 0.2\n")
        (data_path / "dataset.yaml").write_text(
            "names: [key]\ntrain: images/train\nval: images/val\n"
        )
        value.update(
            dataset_yaml=str(data_path / "dataset.yaml"),
            class_names={"0": "key"},
            dataset_sha256=fixture_fingerprint(data_path, classify=False),
        )
    write_json(root, f"{run}/{'training-manifest' if classify else 'experiment'}.json", value)
    return value


def test_r05_uses_recorded_r03_checkpoint_dependency(tmp_path: Path):
    r03 = "harness/artifacts/finetune-20260909-r03/runs/scene-yolo26n-r03"
    before = record(tmp_path, r03)
    r05 = "data/scene-r04-20260917/runs/scene-yolo26n-r05"
    value = record(tmp_path, r05)
    value["source_weights"] = before["trained_weights"]
    value["source_weights_sha256"] = before["trained_weights_sha256"]
    write_json(tmp_path, f"{r05}/experiment.json", value)
    catalog = discover_catalog(tmp_path)
    recipe = next(item for item in catalog["recipes"] if item["record"].startswith(r05))
    dependency = next(item for item in catalog["recipes"] if item["record"].startswith(r03))
    assert recipe["source_weights"] == f"{r03}/weights/last.pt"
    assert recipe["dependencies"] == [dependency["id"]]
    assert recipe["validity"] == "ready"


def test_classification_preserves_train_only_last_and_roi_identity(tmp_path: Path):
    for suffix, roi in (("a", None), ("b", [0.25, 0, 0.75, 0.85])):
        run = f"harness/artifacts/behavior-{suffix}/drinking-run"
        value = record(tmp_path, run, classify=True)
        value["preprocessing"] = {"roi": roi, "expected_source_frame_size": [1920, 1080]}
        write_json(tmp_path, f"{run}/training-manifest.json", value)
    catalog = discover_catalog(tmp_path)
    assert len({item["id"] for item in catalog["recipes"]}) == 2
    assert len(catalog["datasets"]) == 1
    for recipe in catalog["recipes"]:
        assert recipe["reference_weights"].endswith("/last.pt")
        assert "--train-only" in recipe["argv"]
        assert recipe["check_only"]["mode"] == "runtime"
    roi_recipe = next(item for item in catalog["recipes"] if item["preprocessing"]["roi"])
    assert roi_recipe["argv"][roi_recipe["argv"].index("--roi") + 1 :][:4] == [
        "0.25",
        "0",
        "0.75",
        "0.85",
    ]


def test_invalid_keys_first_round_is_preserved_but_not_ready(tmp_path: Path):
    run = "harness/artifacts/keys-20260911-r01/runs-probe/key-cpu-1ep"
    record(tmp_path, run, data="harness/artifacts/keys-20260911-r01/dataset")
    catalog = discover_catalog(tmp_path)
    assert len(catalog["recipes"]) == 1
    assert catalog["recipes"][0]["validity"] == "invalid"
    assert catalog["datasets"][0]["validity"] == "invalid"


def test_unknown_absolute_source_path_is_never_guessed_by_basename(tmp_path: Path):
    run = "harness/artifacts/laptop-unknown"
    value = record(tmp_path, run, classify=True)
    value["source_weights"] = "/other/project/models/start.pt"
    write_json(tmp_path, f"{run}/training-manifest.json", value)
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["source_weights"] is None
    assert recipe["validity"] == "incomplete"
    assert any("Unrecognized" in note for note in recipe["notes"])


def test_missing_hash_and_changed_reference_cannot_be_ready(tmp_path: Path):
    run = "harness/artifacts/laptop-hash"
    value = record(tmp_path, run, classify=True)
    del value["source_weights_sha256"]
    Path(value["selected_checkpoint"]).write_bytes(b"changed")
    write_json(tmp_path, f"{run}/training-manifest.json", value)
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "incomplete"
    assert any("SHA256" in note for note in recipe["notes"])


def test_args_yaml_recovers_missing_historical_parameter_without_overriding_record(tmp_path: Path):
    run = "harness/artifacts/finetune-20260909/runs/scene-yolo26n-r01"
    record(tmp_path, run)
    (tmp_path / run / "args.yaml").write_text("warmup_bias_lr: 0.1\nepochs: 999\n")
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["argv"][recipe["argv"].index("--warmup-bias-lr") + 1] == "0.1"
    assert recipe["argv"][recipe["argv"].index("--epochs") + 1] == "20"


def test_classification_source_includes_parent_manifest_and_frozen_versions(tmp_path: Path):
    run = "harness/artifacts/behavior-r01/posture-run"
    value = record(tmp_path, run, classify=True, data="data/behavior-r01/posture")
    (tmp_path / "data/behavior-r01/posture/manifest.json").unlink()
    write_json(tmp_path, "data/behavior-r01/manifest.json", {"counts": {"posture/train/a": 1}})
    write_json(tmp_path, "data/behavior-r01-incremental/manifest.json", {"counts": {"train": 2}})
    write_json(tmp_path, f"{run}/training-manifest.json", value)
    catalog = discover_catalog(tmp_path)
    trained = next(d for d in catalog["datasets"] if d["data_dir"].endswith("/posture"))
    assert trained["source_dir"] == "data/behavior-r01"
    assert len(catalog["datasets"]) == 2
    assert len(catalog["recipes"]) == 1


def test_audited_r01_source_snapshot_difference_is_explicit_history(tmp_path: Path):
    source = "harness/artifacts/finetune-20260909/public/manifest.json"
    write_json(tmp_path, source, {"revision": "current"})
    write_json(
        tmp_path,
        "harness/artifacts/finetune-20260909/dataset-r01/manifest.json",
        {"samples": [], "sources": [{"path": str(tmp_path / source), "sha256": "0" * 64}]},
    )
    dataset = discover_catalog(tmp_path)["datasets"][0]
    assert any("historical" in note and source in note for note in dataset["notes"])


def test_valid_label_change_blocks_ready_even_when_all_model_hashes_match(tmp_path: Path):
    run = "harness/artifacts/finetune-20260909/runs/scene-yolo26n-r01"
    record(tmp_path, run)
    assert discover_catalog(tmp_path)["recipes"][0]["validity"] == "ready"
    (tmp_path / "data/dataset/labels/train/train.txt").write_text("0 0.5 0.5 0.3 0.3\n")
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "incomplete"
    fingerprint = recipe["dataset_fingerprint"]
    assert fingerprint["algorithm"] == "yolo-path-sha256-v1"
    assert fingerprint["expected"] != fingerprint["computed"]
    assert fingerprint["matched"] is False
    assert any(f["source"].endswith("labels/train/train.txt") for f in fingerprint["files"])


def test_classification_fingerprint_excludes_test_and_train_only_validation(tmp_path: Path):
    run = "harness/artifacts/behavior-r01/posture-run"
    value = record(tmp_path, run, classify=True)
    artifact(tmp_path, "data/dataset/test/a/unseen.png")
    artifact(tmp_path, "data/dataset/val/a/not-used.png")
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "ready"
    fingerprint = recipe["dataset_fingerprint"]
    assert fingerprint["algorithm"] == "classification-relative-sha256-v1"
    assert fingerprint["expected"] == fingerprint["computed"] == value["dataset"]["sha256"]
    assert fingerprint["matched"] is True
    assert {f["identity"] for f in fingerprint["files"]} == {"train/a/a.png", "train/b/b.png"}


def test_missing_dataset_fingerprint_is_incomplete(tmp_path: Path):
    run = "harness/artifacts/behavior-r01/posture-run"
    value = record(tmp_path, run, classify=True)
    value["dataset"].pop("sha256")
    write_json(tmp_path, f"{run}/training-manifest.json", value)
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "incomplete"
    assert recipe["dataset_fingerprint"]["matched"] is False


def test_detection_preserves_historical_absolute_fingerprint_identities(tmp_path: Path):
    run = "harness/artifacts/finetune-20260909/runs/scene-yolo26n-r01"
    value = record(tmp_path, run)
    data_path = tmp_path / "data/dataset"
    files = sorted(
        [data_path / "dataset.yaml", *data_path.glob("images/*/*"), *data_path.glob("labels/*/*")]
    )
    digest = hashlib.sha256()
    for path in files:
        identity = str(RECORDED_ROOT / path.relative_to(tmp_path))
        digest.update(identity.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    value["dataset_yaml"] = str(RECORDED_ROOT / "data/dataset/dataset.yaml")
    value["dataset_sha256"] = digest.hexdigest()
    write_json(tmp_path, f"{run}/experiment.json", value)
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "ready"
    entries = recipe["dataset_fingerprint"]["files"]
    assert all(entry["identity"].startswith(str(RECORDED_ROOT)) for entry in entries)
    assert all(not Path(entry["source"]).is_absolute() for entry in entries)


def test_classification_independent_validation_is_hashed_after_training_split(tmp_path: Path):
    run = "harness/artifacts/laptop-r01"
    value = record(tmp_path, run, classify=True)
    for name in ("a", "b"):
        artifact(tmp_path, f"data/dataset/val/{name}/{name}.png")
    value["parameters"]["val"] = True
    data = tmp_path / "data/dataset"
    digest = hashlib.sha256()
    expected_order = []
    for split in ("train", "val"):
        for path in sorted((data / split).rglob("*.png")):
            identity = path.relative_to(data).as_posix()
            expected_order.append(identity)
            digest.update(identity.encode() + hashlib.sha256(path.read_bytes()).digest())
    value["dataset"]["sha256"] = digest.hexdigest()
    write_json(tmp_path, f"{run}/training-manifest.json", value)
    recipe = discover_catalog(tmp_path)["recipes"][0]
    assert recipe["validity"] == "ready"
    assert [f["identity"] for f in recipe["dataset_fingerprint"]["files"]] == expected_order
    (data / "val/a/a.png").write_bytes(b"changed validation image")
    assert discover_catalog(tmp_path)["recipes"][0]["validity"] == "incomplete"
