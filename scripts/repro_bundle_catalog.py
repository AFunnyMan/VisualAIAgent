"""Discover recorded YOLO recipes without importing a trainer or executing a model.

Source paths are repository relative; detection fingerprint identities retain
their historical absolute paths. ``ready`` means recorded data and model hashes
match local files, not that a model passed product acceptance. Invalid
historical key labels remain visible and must never enter normal train profiles.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

RECORDED_ROOT = Path("/Users/fanxinhao/project/VisualAIAgent")
RECORD_PATTERNS = (
    "data/scene-r04-20260917/runs/*/experiment.json",
    "harness/artifacts/finetune-20260909*/runs/*/experiment.json",
    "harness/artifacts/behavior-*/**/training-manifest.json",
    "harness/artifacts/drinking-*/**/training-manifest.json",
    "harness/artifacts/laptop-*/training-manifest.json",
    "harness/artifacts/keys-*/runs*/*/experiment.json",
)
DATA_PATTERNS = (
    "harness/artifacts/finetune-20260909*/dataset*/manifest.json",
    "data/scene-r04-20260917/dataset/manifest.json",
    "data/behavior-*/manifest.json",
    "data/drinking-*/manifest.json",
    "harness/artifacts/laptop-*-data*/manifest.json",
    "harness/artifacts/keys-*/dataset*/manifest.json",
)
DETECT_OPTIONS = (
    "epochs",
    "imgsz",
    "batch",
    "freeze",
    "workers",
    "seed",
    "optimizer",
    "lr0",
    "patience",
    "warmup_epochs",
    "warmup_bias_lr",
    "mosaic",
    "close_mosaic",
    "device",
)
CLASSIFY_OPTIONS = ("epochs", "imgsz", "device", "batch", "seed", "freeze")
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def _id(task: str, relative: str) -> str:
    return f"{task}-{re.sub(r'[^a-zA-Z0-9]+', '-', relative).strip('-')}"


def _relative(root: Path, value: Any, notes: list[str], field: str) -> str | None:
    if not isinstance(value, str) or not value:
        notes.append(f"Missing {field} path")
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            try:
                path = path.relative_to(RECORDED_ROOT)
            except ValueError:
                notes.append(f"Unrecognized {field} path; no basename fallback used")
                return None
    if ".." in path.parts or not (root / path).resolve().is_relative_to(root):
        notes.append(f"Unrecognized {field} path outside repository")
        return None
    return path.as_posix()


def _task(path: Path, classify: bool) -> str:
    if any(part.startswith("keys-") for part in path.parts):
        return "keys"
    if any(part.startswith("laptop-") for part in path.parts):
        return "laptop"
    if classify:
        return "drinking" if "drinking" in path.as_posix() else "posture"
    return "objects"


def _invalid_keys(task: str, data_dir: str | None) -> bool:
    return task == "keys" and data_dir == "harness/artifacts/keys-20260911-r01/dataset"


def _normalize_parameters(root: Path, values: dict, notes: list[str]) -> dict:
    result = dict(values)
    for key in ("data", "model", "project"):
        if result.get(key):
            result[key] = _relative(root, result[key], notes, f"parameters.{key}")
    return result


def _argv(recipe: dict, record: dict, data: str | None, run_dir: str) -> list[str]:
    if data is None or recipe["source_weights"] is None:
        return []
    classify = recipe["task"] in ("posture", "drinking", "laptop")
    argv = ["--data", data, "--weights", recipe["source_weights"], "--output"]
    argv.append(run_dir if classify else str(Path(run_dir).parent))
    if not classify:
        argv += ["--name", Path(run_dir).name]
    parameters = recipe["parameters"]
    for name in CLASSIFY_OPTIONS if classify else DETECT_OPTIONS:
        if name in parameters:
            argv += [f"--{name.replace('_', '-')}", str(parameters[name])]
    if classify:
        if parameters.get("val") is False:
            argv.append("--train-only")
        runtime_threads = record.get("runtime", {}).get("torch_cpu_threads")
        if runtime_threads is not None:
            argv += ["--threads", str(runtime_threads)]
        preprocessing = recipe["preprocessing"]
        roi = preprocessing.get("roi")
        if roi is not None:
            argv += ["--roi", *(str(value) for value in roi)]
            size = preprocessing.get("expected_source_frame_size")
            if size:
                argv += ["--source-size", *(str(value) for value in size)]
    else:
        argv.append("--amp" if parameters.get("amp") else "--no-amp")
        argv.append("--export" if recipe["reference_onnx"] else "--no-export")
        names = record.get("class_names", {})
        if len(names) == 80:
            argv.append("--preserve-coco-head")
        else:
            for key in sorted(names, key=int):
                argv += ["--class-name", names[key]]
        if recipe.get("selection_manifest"):
            argv += ["--selection-manifest", recipe["selection_manifest"]]
    return argv


def _fingerprint_files(
    root: Path,
    raw_data: str,
    *,
    classify: bool,
    train_only: bool,
    file_sha256: Callable[[str], str],
) -> tuple[str, list[dict]]:
    """Replay the two historical path-sensitive algorithms, without trainer imports."""

    def local_path(identity: Path) -> Path:
        relative = _relative(root, str(identity), [], "fingerprint file")
        if relative is None:
            raise ValueError("Dataset fingerprint path is outside recognized repository roots")
        return root / relative

    recorded_data = Path(raw_data).expanduser()
    if not recorded_data.is_absolute():
        recorded_data = root / recorded_data
    recorded_data = recorded_data.resolve()
    data = local_path(recorded_data)
    identities: list[tuple[Path, str]] = []
    if classify:
        for split in ("train",) if train_only else ("train", "val"):
            directory = data / split
            if not directory.is_dir():
                raise ValueError(f"Dataset fingerprint missing required {split} directory")
            files = sorted(
                path.resolve()
                for category in directory.iterdir()
                if category.is_dir()
                for path in category.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            )
            identities.extend((path, path.relative_to(data).as_posix()) for path in files)
    else:
        config = yaml.safe_load(data.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or "download" in config:
            raise ValueError("Dataset fingerprint requires an offline YAML mapping")
        base_value = config.get("path", "")
        base = Path(base_value).expanduser() if base_value else recorded_data.parent
        if not base.is_absolute():
            base = recorded_data.parent / base
        files = {recorded_data}
        for split in ("train", "val"):
            values = config.get(split)
            if not values:
                raise ValueError(f"Dataset fingerprint missing required {split} declaration")
            for value in values if isinstance(values, list) else [values]:
                if "://" in str(value):
                    raise ValueError("Remote dataset fingerprint paths are prohibited")
                identity = Path(str(value)).expanduser()
                identity = (identity if identity.is_absolute() else base / identity).resolve()
                path = local_path(identity)
                if not path.exists():
                    raise ValueError(f"Dataset fingerprint missing {split} split")
                if path.is_dir():
                    files.update(
                        identity / p.relative_to(path) for p in path.rglob("*") if p.is_file()
                    )
                    parts = list(identity.parts)
                    if "images" in parts:
                        parts[parts.index("images")] = "labels"
                        label_identity = Path(*parts)
                        label_root = local_path(label_identity)
                        if label_root.is_dir():
                            files.update(
                                label_identity / p.relative_to(label_root)
                                for p in label_root.rglob("*")
                                if p.is_file()
                            )
                elif path.suffix.lower() == ".txt":
                    files.add(identity)
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        image = Path(line.strip())
                        image = (
                            image if image.is_absolute() else identity.parent / image
                        ).resolve()
                        if not local_path(image).is_file():
                            raise ValueError("Dataset fingerprint image list contains missing file")
                        files.add(image)
                        parts = list(image.parts)
                        if "images" in parts:
                            parts[parts.index("images")] = "labels"
                            label = Path(*parts).with_suffix(".txt")
                            if local_path(label).is_file():
                                files.add(label)
                else:
                    files.add(identity)
        identities = [(local_path(identity), str(identity)) for identity in sorted(files, key=str)]
    digest = hashlib.sha256()
    entries: list[dict] = []
    for path, identity in identities:
        relative = _relative(root, str(path), [], "fingerprint file")
        if relative is None:
            raise ValueError("Dataset fingerprint file escapes repository")
        checksum = file_sha256(relative)
        digest.update(identity.encode("utf-8"))
        if not classify:
            digest.update(b"\0")
        digest.update(bytes.fromhex(checksum))
        entries.append({"source": relative, "identity": identity, "sha256": checksum})
    return digest.hexdigest(), entries


def discover_catalog(root: Path) -> dict:
    """Return a deterministic, JSON-serializable catalog of local training records.

    This function only reads explicit training records, manifests, args.yaml,
    train/val data and recorded model files. It never reads credentials, starts training, downloads,
    imports Ultralytics, or modifies the original training artifacts.
    """
    root = root.resolve()
    datasets: dict[tuple[str, str], dict] = {}
    recipes: list[dict] = []
    hashes: dict[str, str] = {}
    fingerprints: dict[tuple, tuple[str | None, list[dict], str | None]] = {}

    def file_sha256(relative: str) -> str:
        if relative not in hashes:
            digest = hashlib.sha256()
            with (root / relative).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[relative] = digest.hexdigest()
        return hashes[relative]

    def dataset_entry(task: str, data_dir: str, *, counts: dict | None = None) -> dict:
        key = (task, data_dir)
        if key in datasets:
            return datasets[key]
        directory = root / data_dir
        candidates = (directory / "manifest.json", directory.parent / "manifest.json")
        manifest = next((path for path in candidates if path.is_file()), None)
        notes: list[str] = []
        frozen = _read(manifest) if manifest else {}
        if not manifest:
            notes.append("No frozen dataset manifest found")
        # An audited historical provenance discrepancy is retained verbatim,
        # never repaired by changing the old manifest or hiding a current error.
        historical_source = "harness/artifacts/finetune-20260909/public/manifest.json"
        if data_dir == "harness/artifacts/finetune-20260909/dataset-r01":
            for reference in frozen.get("sources", []):
                if not isinstance(reference, dict) or "path" not in reference:
                    continue
                old = Path(reference["path"])
                if old.is_absolute():
                    try:
                        old = old.relative_to(root)
                    except ValueError:
                        continue
                if old.as_posix() != historical_source or not (root / old).is_file():
                    continue
                actual = hashlib.sha256((root / old).read_bytes()).hexdigest()
                if actual != reference.get("sha256"):
                    notes.append(
                        f"historical {historical_source} SHA mismatch: "
                        f"recorded {reference.get('sha256')} current {actual}; "
                        "source snapshot retained unchanged, not a reproduced old snapshot"
                    )
        invalid = _invalid_keys(task, data_dir)
        if invalid:
            notes.append("Historical incorrect key boxes; preserved as invalid history")
        source_dir = manifest.parent if manifest else directory
        item = {
            "id": _id(task, data_dir),
            "task": task,
            "source_dir": source_dir.relative_to(root).as_posix(),
            "data_dir": data_dir,
            "manifest": manifest.relative_to(root).as_posix() if manifest else None,
            "counts": counts if counts is not None else frozen.get("counts", {}),
            "validity": "invalid" if invalid else "ready" if manifest else "incomplete",
            "notes": notes,
        }
        datasets[key] = item
        return item

    def verify(relative: str | None, expected: str | None, label: str, notes: list[str]) -> bool:
        if relative is None:
            return False
        if not expected or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            notes.append(f"Missing or invalid {label} SHA256")
            return False
        path = root / relative
        if not path.is_file():
            notes.append(f"Missing {label} file: {relative}")
            return False
        if file_sha256(relative) != expected.lower():
            notes.append(f"Mismatched {label} SHA256: {relative}")
            return False
        return True

    paths = sorted({path for pattern in RECORD_PATTERNS for path in root.glob(pattern)})
    for path in paths:
        record = _read(path)
        relative = path.relative_to(root).as_posix()
        classify = path.name == "training-manifest.json"
        task = _task(path.relative_to(root), classify)
        notes = list(record.get("notes", []))
        raw_parameters: dict = {}
        args_path = path.parent / "args.yaml"
        if args_path.is_file():
            loaded = yaml.safe_load(args_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw_parameters.update(loaded)
        raw_parameters.update(record.get("parameters", {}))
        parameters = _normalize_parameters(root, raw_parameters, notes)
        raw_data = record.get("dataset", {}).get("root") if classify else record.get("dataset_yaml")
        data_path = _relative(root, raw_data, notes, "dataset")
        data_dir = data_path if classify or data_path is None else str(Path(data_path).parent)
        dataset = (
            dataset_entry(task, data_dir, counts=record.get("dataset", {}).get("counts"))
            if data_dir
            else None
        )
        selected = "selected_checkpoint" if classify else "trained_weights"
        onnx = "onnx" if classify else "exported_onnx"
        recipe = {
            "id": _id(task, path.parent.relative_to(root).as_posix()),
            "task": task,
            "record": relative,
            "script": "scripts/train_laptop_model.py"
            if task == "laptop"
            else "scripts/train_behavior_model.py"
            if classify
            else "scripts/train_scene_model.py",
            "argv": [],
            "dataset_id": dataset["id"] if dataset else None,
            "source_weights": _relative(
                root, record.get("source_weights"), notes, "source_weights"
            ),
            "reference_weights": _relative(root, record.get(selected), notes, "reference_weights"),
            "reference_onnx": _relative(root, record.get(onnx), notes, "reference_onnx"),
            "source_weights_sha256": record.get("source_weights_sha256"),
            "reference_weights_sha256": record.get(f"{selected}_sha256"),
            "reference_onnx_sha256": record.get(f"{onnx}_sha256"),
            "dataset_sha256": record.get("dataset", {}).get("sha256")
            if classify
            else record.get("dataset_sha256"),
            "parameters": parameters,
            "dependencies": [],
            "notes": notes,
            "preprocessing": record.get("preprocessing", {}),
            "validation": record.get("validation", {}),
            "checkpoint_selection": record.get("checkpoint_selection"),
            "check_only": {
                "mode": "runtime" if classify else "native",
                "flag": None if classify else "--check-only",
            },
            "versions": record.get("versions", {}),
            "parameter_evidence": [relative]
            + ([args_path.relative_to(root).as_posix()] if args_path.is_file() else []),
        }
        selection = record.get("selection")
        if isinstance(selection, dict):
            recipe["selection_manifest"] = _relative(
                root, selection.get("manifest"), notes, "selection_manifest"
            )
        checks = [
            verify(recipe[key], recipe[f"{key}_sha256"], key, notes)
            for key in ("source_weights", "reference_weights", "reference_onnx")
        ]
        fingerprint_key = (raw_data, classify, parameters.get("val") is False)
        if fingerprint_key not in fingerprints:
            try:
                if not isinstance(raw_data, str) or not data_path:
                    raise ValueError("Missing or unrecognized dataset fingerprint source")
                computed, files = _fingerprint_files(
                    root,
                    raw_data,
                    classify=classify,
                    train_only=parameters.get("val") is False,
                    file_sha256=file_sha256,
                )
                fingerprints[fingerprint_key] = (computed, files, None)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                fingerprints[fingerprint_key] = (None, [], str(exc))
        computed, files, error = fingerprints[fingerprint_key]
        expected = recipe["dataset_sha256"]
        matched = bool(expected and computed and expected == computed)
        recipe["dataset_fingerprint"] = {
            "algorithm": "classification-relative-sha256-v1" if classify else "yolo-path-sha256-v1",
            "expected": expected,
            "computed": computed,
            "matched": matched,
            "files": files,
        }
        if error:
            recipe["dataset_fingerprint"]["error"] = error
        if not matched:
            notes.append(
                f"Dataset fingerprint SHA256 missing or mismatched{': ' + error if error else ''}"
            )
        checks.append(matched)
        invalid = _invalid_keys(task, data_dir) or record.get("status") == "failed"
        complete = record.get("status") == "completed" and all(checks) and dataset is not None
        complete = complete and dataset["validity"] == "ready" if dataset else False
        recipe["validity"] = "invalid" if invalid else "ready" if complete else "incomplete"
        recipe["argv"] = _argv(recipe, record, data_path, path.parent.relative_to(root).as_posix())
        if invalid:
            notes.append("Excluded from normal trainable profiles; original history retained")
        recipes.append(recipe)

    producers = {
        recipe["reference_weights"]: recipe["id"]
        for recipe in recipes
        if recipe["reference_weights"]
    }
    for recipe in recipes:
        dependency = producers.get(recipe["source_weights"])
        if dependency and dependency != recipe["id"]:
            recipe["dependencies"] = [dependency]

    # Preserve frozen incremental/validation versions even when no actual training
    # record consumed them. Their presence never fabricates a new training recipe.
    for path in sorted({p for pattern in DATA_PATTERNS for p in root.glob(pattern)}):
        if any(item["manifest"] == path.relative_to(root).as_posix() for item in datasets.values()):
            continue
        frozen = _read(path)
        task = _task(path.relative_to(root), "behavior" in str(path) or "drinking" in str(path))
        item = dataset_entry(task, path.parent.relative_to(root).as_posix())
        item["notes"].append("Frozen historical dataset; not a fabricated training run")
        item["counts"] = frozen.get("counts", {})
    return {
        "schema_version": 1,
        "datasets": sorted(datasets.values(), key=lambda item: item["id"]),
        "recipes": recipes,
    }
