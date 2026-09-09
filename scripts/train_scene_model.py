#!/usr/bin/env python3
"""Fine-tune YOLO26n for scene classes and export an experimental ONNX.

This is an isolated experiment runner. It never replaces the production model or
manifest. Run it from ``.export-venv`` and use ``--check-only`` before a long run.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))
DEFAULT_OUTPUT = REPOSITORY_ROOT / "harness/artifacts/finetune-20260909/runs"
EXPECTED_NAMES = {0: "bottle", 1: "cup", 2: "cell phone"}
OFFICIAL_MANIFEST = REPOSITORY_ROOT / "model_manifests/yolo26n-e2e.onnx.json"
REQUIRED_CLASSES = {"bottle", "cup", "cell phone"}
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

# These must be set before importing Ultralytics. They keep its settings local,
# suppress network update checks, and prohibit automatic dependency installation.
os.environ.setdefault(
    "YOLO_CONFIG_DIR",
    str(REPOSITORY_ROOT / "harness/artifacts/finetune-20260909/settings"),
)
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("YOLO_OFFLINE", "true")
os.environ.setdefault("ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS", "true")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("COMET_MODE", "DISABLED")
os.environ.setdefault("MLFLOW_TRACKING_URI", "")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def normalize_names(value: Any) -> dict[int, str]:
    if isinstance(value, list):
        return dict(enumerate(map(str, value)))
    if isinstance(value, dict):
        try:
            return {int(key): str(name) for key, name in value.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("Dataset names keys must be integer class IDs") from exc
    raise ValueError("Dataset YAML must contain names as a list or mapping")


def split_roots(dataset_yaml: Path, config: dict[str, Any]) -> list[Path]:
    base_value = config.get("path", "")
    base = Path(base_value).expanduser() if base_value else dataset_yaml.parent
    if not base.is_absolute():
        base = dataset_yaml.parent / base
    roots: list[Path] = []
    for split in ("train", "val"):
        values = config.get(split)
        if not values:
            raise ValueError(f"Dataset YAML is missing required '{split}' split")
        for value in values if isinstance(values, list) else [values]:
            text = str(value)
            if "://" in text:
                raise ValueError(f"Remote dataset split is prohibited: {text}")
            path = Path(text).expanduser()
            roots.append(path if path.is_absolute() else base / path)
    return [path.resolve() for path in roots]


def split_images(dataset_yaml: Path, config: dict[str, Any], split: str) -> set[Path]:
    """Resolve only image files reachable from a local YOLO split."""
    base_value = config.get("path", "")
    base = Path(base_value).expanduser() if base_value else dataset_yaml.parent
    if not base.is_absolute():
        base = dataset_yaml.parent / base
    values = config.get(split)
    if not values:
        return set()
    images: set[Path] = set()
    for value in values if isinstance(values, list) else [values]:
        text = str(value)
        if "://" in text:
            raise ValueError(f"Remote dataset split is prohibited: {text}")
        path = Path(text).expanduser()
        path = (path if path.is_absolute() else base / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Dataset split path does not exist: {path}")
        if path.is_dir():
            images.update(
                file.resolve()
                for file in path.rglob("*")
                if file.is_file() and file.suffix.lower() in IMAGE_SUFFIXES
            )
        elif path.suffix.lower() == ".txt":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    image = Path(line.strip()).expanduser()
                    image = (image if image.is_absolute() else path.parent / image).resolve()
                    if not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
                        raise FileNotFoundError(f"Image list contains invalid image: {image}")
                    images.add(image)
        elif path.suffix.lower() in IMAGE_SUFFIXES:
            images.add(path)
    return images


def _validate_annotation(sample_id: str, annotation: Any, width: int, height: int) -> None:
    if not isinstance(annotation, dict) or annotation.get("category") not in REQUIRED_CLASSES:
        raise ValueError(f"Selection sample has invalid annotations: {sample_id}")
    bbox = annotation.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox)
        or not all(math.isfinite(float(value)) for value in bbox)
        or float(bbox[0]) < 0
        or float(bbox[1]) < 0
        or float(bbox[2]) <= float(bbox[0])
        or float(bbox[3]) <= float(bbox[1])
        or float(bbox[2]) > width
        or float(bbox[3]) > height
        or not isinstance(annotation.get("iscrowd"), bool)
    ):
        raise ValueError(f"Selection sample has invalid annotation box: {sample_id}")


def load_selection_manifest(dataset_yaml: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate selection images and capture groups against frozen dataset splits."""
    import cv2
    import yaml

    config = yaml.safe_load(dataset_yaml.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = manifest.get("samples") if isinstance(manifest, dict) else None
    if not isinstance(samples, list) or not samples:
        raise ValueError("Selection manifest must contain non-empty samples")
    splits = {name: split_images(dataset_yaml, config, name) for name in ("train", "val", "test")}
    non_val_hashes = {sha256(path) for path in splits["train"] | splits["test"]}
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()
    capture_to_selection: dict[str, str] = {}
    dataset_manifest_path = dataset_yaml.parent / "manifest.json"
    dataset_rows: dict[str, dict[str, Any]] = {}
    if dataset_manifest_path.is_file():
        dataset_manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
        raw_rows = dataset_manifest.get("samples") if isinstance(dataset_manifest, dict) else None
        if not isinstance(raw_rows, list):
            raise ValueError("Dataset manifest must contain samples")
        group_splits: dict[str, set[str]] = {}
        for row in raw_rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("Dataset manifest has invalid sample")
            if row["id"] in dataset_rows:
                raise ValueError("Dataset manifest sample IDs must be unique")
            dataset_rows[row["id"]] = row
            if isinstance(row.get("group"), str) and isinstance(row.get("split"), str):
                group_splits.setdefault(row["group"], set()).add(row["split"])
        leaked = sorted(group for group, values in group_splits.items() if len(values) > 1)
        if leaked:
            raise ValueError(f"Capture groups span dataset splits: {leaked[0]}")
    normalized = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("split") != "val":
            raise ValueError("Every selection sample must have split='val'")
        sample_id, capture_group = sample.get("id"), sample.get("group")
        selection_group = sample.get("selection_group")
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen_ids:
            raise ValueError("Selection sample IDs must be unique non-empty strings")
        if not isinstance(capture_group, str) or not capture_group.strip():
            raise ValueError(f"Selection sample {sample_id} requires a capture group")
        if not isinstance(selection_group, str) or not selection_group.strip():
            raise ValueError(f"Selection sample {sample_id} requires a selection_group")
        previous_selection = capture_to_selection.setdefault(capture_group, selection_group)
        if previous_selection != selection_group:
            raise ValueError(f"Capture group spans selection groups: {capture_group}")
        path_value = sample.get("image_path")
        if not isinstance(path_value, str):
            raise ValueError(f"Selection sample {sample_id} requires image_path")
        path = Path(path_value).expanduser()
        path = (path if path.is_absolute() else manifest_path.parent / path).resolve()
        checksum = sha256(path) if path.is_file() else None
        if path not in splits["val"]:
            raise ValueError(f"Selection sample is not in dataset val: {sample_id}")
        if sample.get("sha256") != checksum:
            raise ValueError(f"Selection image checksum mismatch: {sample_id}")
        if path in splits["train"] or path in splits["test"] or checksum in non_val_hashes:
            raise ValueError(f"Selection sample overlaps train/test: {sample_id}")
        annotations = sample.get("annotations")
        if not isinstance(annotations, list):
            raise ValueError(f"Selection sample has invalid annotations: {sample_id}")
        frame = cv2.imread(str(path))
        height, width = frame.shape[:2] if frame is not None else (0, 0)
        if (
            sample.get("width") != width
            or sample.get("height") != height
            or not width
            or not height
        ):
            raise ValueError(f"Selection image dimensions mismatch: {sample_id}")
        for annotation in annotations:
            _validate_annotation(sample_id, annotation, width, height)
        if dataset_rows:
            frozen = dataset_rows.get(sample_id)
            if (
                frozen is None
                or frozen.get("split") != "val"
                or frozen.get("group") != capture_group
            ):
                raise ValueError(f"Selection sample differs from dataset manifest: {sample_id}")
            frozen_path = Path(str(frozen.get("image_path", ""))).expanduser()
            frozen_path = (
                frozen_path
                if frozen_path.is_absolute()
                else dataset_manifest_path.parent / frozen_path
            ).resolve()
            if frozen_path != path or frozen.get("sha256") != checksum:
                raise ValueError(
                    f"Selection path/checksum differs from dataset manifest: {sample_id}"
                )
        if path in seen_paths or checksum in seen_hashes:
            raise ValueError("Selection images must be unique by path and content")
        seen_ids.add(sample_id)
        seen_paths.add(path)
        seen_hashes.add(checksum)
        normalized.append({**sample, "image_path": str(path)})
    return {**manifest, "samples": normalized, "_path": str(manifest_path.resolve())}


def grouped_macro_f1(manifest: dict[str, Any], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Average class F1 inside each selection group, then weight groups equally."""
    from scripts.score_detections import score_dataset

    rows = {str(row["id"]): row for row in predictions}
    sample_ids = [str(sample["id"]) for sample in manifest["samples"]]
    if (
        len(rows) != len(predictions)
        or set(rows) != set(sample_ids)
        or len(set(sample_ids)) != len(sample_ids)
    ):
        raise ValueError("Predictions must cover exactly the unique selection image IDs")
    groups: dict[str, list[dict[str, Any]]] = {}
    for sample in manifest["samples"]:
        groups.setdefault(sample["selection_group"], []).append(sample)
    group_scores: dict[str, Any] = {}
    for group, samples in groups.items():
        score = score_dataset(
            {"samples": samples}, [rows[str(sample["id"])] for sample in samples], 0.35
        )
        class_f1 = []
        for category in REQUIRED_CLASSES:
            counts = score["categories"][category]
            denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
            if denominator:
                class_f1.append(2 * counts["tp"] / denominator)
        macro_f1 = sum(class_f1) / len(class_f1) if class_f1 else 1.0
        group_scores[group] = {"macro_f1": macro_f1, "scores": score}
    return {
        "macro_f1": sum(row["macro_f1"] for row in group_scores.values()) / len(group_scores),
        "groups": group_scores,
    }


def files_for_split(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset split path does not exist: {path}")
    if path.is_dir():
        files = [file for file in path.rglob("*") if file.is_file()]
        # YOLO convention stores labels in a parallel labels/{split} tree.
        parts = list(path.parts)
        if "images" in parts:
            parts[parts.index("images")] = "labels"
            label_root = Path(*parts)
            if label_root.is_dir():
                files.extend(file for file in label_root.rglob("*") if file.is_file())
        return sorted(files)
    if path.suffix.lower() == ".txt":
        files = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                candidate = Path(line)
                resolved = candidate if candidate.is_absolute() else path.parent / candidate
                files.append(resolved.resolve())
        missing = [file for file in files if not file.is_file()]
        if missing:
            raise FileNotFoundError(f"Image list contains missing file: {missing[0]}")
        labels = []
        for image in files:
            parts = list(image.parts)
            if "images" in parts:
                parts[parts.index("images")] = "labels"
                label = Path(*parts).with_suffix(".txt")
                if label.is_file():
                    labels.append(label)
        return [path, *sorted(files), *sorted(labels)]
    return [path]


def official_names() -> dict[int, str]:
    manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    raw = manifest["model_metadata"]["names"]
    try:
        return normalize_names(ast.literal_eval(raw))
    except (SyntaxError, ValueError) as exc:
        raise ValueError("Official model manifest contains invalid class metadata") from exc


def validate_labels(files: list[Path], names: dict[int, str]) -> dict[str, Any]:
    label_files = [file for file in files if "labels" in file.parts and file.suffix == ".txt"]
    class_instances = {class_id: 0 for class_id in names}
    rows = 0
    for label_file in label_files:
        for line_number, line in enumerate(label_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 5:
                raise ValueError(
                    f"Invalid YOLO row at {label_file}:{line_number}: expected 5 fields"
                )
            try:
                class_value = float(fields[0])
                coordinates = [float(value) for value in fields[1:]]
            except ValueError as exc:
                raise ValueError(f"Non-numeric YOLO row at {label_file}:{line_number}") from exc
            if not math.isfinite(class_value):
                raise ValueError(f"Non-finite class at {label_file}:{line_number}")
            class_id = int(class_value)
            if class_value != class_id or class_id not in names:
                raise ValueError(f"Out-of-range class at {label_file}:{line_number}: {fields[0]}")
            x, y, width, height = coordinates
            if (
                not all(math.isfinite(value) for value in [class_value, *coordinates])
                or any(value < 0 or value > 1 for value in coordinates)
                or width <= 0
                or height <= 0
                # Decimal YOLO labels can round a border by a few floating-point bits.
                or x - width / 2 < -1e-8
                or x + width / 2 > 1 + 1e-8
                or y - height / 2 < -1e-8
                or y + height / 2 > 1 + 1e-8
            ):
                raise ValueError(f"Invalid normalized box at {label_file}:{line_number}")
            class_instances[class_id] += 1
            rows += 1
    required_ids = {class_id for class_id, name in names.items() if name in REQUIRED_CLASSES}
    if len(required_ids) != len(REQUIRED_CLASSES):
        raise ValueError("Dataset class mapping is missing a required business class")
    missing = [names[class_id] for class_id in required_ids if class_instances[class_id] == 0]
    if missing:
        raise ValueError(f"Dataset has no labeled instances for required classes: {missing}")
    return {"label_files": len(label_files), "label_rows": rows, "class_instances": class_instances}


def dataset_fingerprint(
    dataset_yaml: Path, expected_names: dict[int, str] = EXPECTED_NAMES
) -> tuple[str, int, dict[str, Any]]:
    """Hash YAML and all files reachable from its train/val split declarations."""
    import yaml

    config = yaml.safe_load(dataset_yaml.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Dataset YAML root must be a mapping")
    if "download" in config:
        raise ValueError("Dataset YAML download directives are prohibited for offline training")
    names = normalize_names(config.get("names"))
    if names != expected_names:
        raise ValueError(f"Expected exactly {expected_names}, got {names}")
    files = [dataset_yaml.resolve()]
    for root in split_roots(dataset_yaml, config):
        files.extend(files_for_split(root))
    unique = sorted(set(files), key=str)
    digest = hashlib.sha256()
    for file in unique:
        digest.update(str(file).encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256(file)))
    return digest.hexdigest(), len(unique), validate_labels(unique, names)


def choose_device(requested: str) -> str:
    import torch

    if requested != "auto":
        return requested
    return "mps" if torch.backends.mps.is_available() else "cpu"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path, help="Three-class YOLO dataset YAML")
    parser.add_argument("--weights", type=Path, default=REPOSITORY_ROOT / "models/yolo26n.pt")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--name", default="scene-yolo26n")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--freeze", type=int, default=10)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20)
    parser.add_argument("--optimizer", default="AdamW")
    parser.add_argument("--lr0", type=float, default=0.001)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--warmup-epochs", type=float, default=1.0)
    parser.add_argument("--warmup-bias-lr", type=float, default=0.1)
    parser.add_argument("--mosaic", type=float, default=0.5)
    parser.add_argument("--close-mosaic", type=int, default=5)
    parser.add_argument("--device", default="auto", help="auto, cpu, mps, or a CUDA device")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--export", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--selection-manifest", type=Path, help="Val-only deployment selection manifest"
    )
    parser.add_argument(
        "--preserve-coco-head",
        action="store_true",
        help="Require the official 80-class mapping and retain the pretrained detection head",
    )
    args = parser.parse_args()
    for field in ("epochs", "imgsz", "batch"):
        if getattr(args, field) < 1:
            parser.error(f"--{field} must be positive")
    if args.freeze < 0 or args.workers < 0:
        parser.error("--freeze and --workers cannot be negative")
    return args


def main() -> None:
    args = parse_args()
    expected_environment = REPOSITORY_ROOT / ".export-venv"
    if Path(sys.prefix).resolve() != expected_environment.resolve():
        raise SystemExit(
            f"Refusing to train outside {expected_environment}; current prefix is {sys.prefix}"
        )

    data = args.data.resolve()
    weights = args.weights.resolve()
    output = args.output.resolve()
    if not data.is_file() or not weights.is_file():
        raise SystemExit(f"Missing data YAML or source weights: {data}, {weights}")
    class_names = official_names() if args.preserve_coco_head else EXPECTED_NAMES
    data_hash, dataset_file_count, label_summary = dataset_fingerprint(data, class_names)
    selection = (
        load_selection_manifest(data, args.selection_manifest.resolve())
        if args.selection_manifest
        else None
    )
    device = choose_device(args.device)
    amp = args.amp
    run_dir = output / args.name
    from ultralytics import YOLO

    source_model = YOLO(str(weights))
    source_names = normalize_names(source_model.model.names)
    expected_source_names = official_names()
    if source_names != expected_source_names:
        raise ValueError("Source checkpoint does not have the official 80-class head")
    parameters = {
        "data": str(data),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "freeze": args.freeze,
        "workers": args.workers,
        "seed": args.seed,
        "deterministic": True,
        "device": device,
        "amp": amp,
        "optimizer": args.optimizer,
        "lr0": args.lr0,
        "patience": args.patience,
        "warmup_epochs": args.warmup_epochs,
        "warmup_bias_lr": args.warmup_bias_lr,
        "mosaic": args.mosaic,
        "close_mosaic": args.close_mosaic,
        "mixup": 0.0,
        "copy_paste": 0.0,
    }
    preflight = {
        "schema_version": 1,
        "status": "preflight_only" if args.check_only else "running",
        "started_at": datetime.now(UTC).isoformat(),
        "source_weights": str(weights),
        "source_weights_sha256": sha256(weights),
        "source_class_names": source_names,
        "dataset_yaml": str(data),
        "dataset_sha256": data_hash,
        "dataset_hashed_file_count": dataset_file_count,
        "dataset_label_summary": label_summary,
        "class_names": class_names,
        "head_mode": "preserved_coco_80" if args.preserve_coco_head else "rebuilt_three_class",
        "parameters": parameters,
        "selection": (
            {
                "manifest": selection["_path"],
                "manifest_sha256": sha256(Path(selection["_path"])),
                "confidence": 0.35,
                "head": "EMA one2one end2end=True",
                "metric": "equal-selection-group macro F1 over business classes",
            }
            if selection
            else None
        ),
        "versions": {
            "python": platform.python_version(),
            "torch": importlib.metadata.version("torch"),
            "ultralytics": importlib.metadata.version("ultralytics"),
        },
        "notes": [
            (
                "The official 80-class detection head is preserved; training data uses the exact "
                "official class mapping."
                if args.preserve_coco_head
                else (
                    "The pretrained 80-class detection head is replaced for this "
                    "three-class dataset."
                )
            ),
            "This experimental model does not establish real-camera or Windows acceptance.",
        ],
    }
    if args.check_only:
        print(json.dumps(preflight, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if run_dir.exists():
        raise SystemExit(f"Refusing to reuse existing run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "experiment.json"
    write_json(manifest_path, preflight)

    source_hash = preflight["source_weights_sha256"]
    started = time.monotonic()
    try:
        from ultralytics.utils import SETTINGS

        SETTINGS.update(
            {
                "sync": False,
                "clearml": False,
                "comet": False,
                "dvc": False,
                "mlflow": False,
                "raytune": False,
                "tensorboard": False,
                "wandb": False,
            }
        )
        model = source_model
        epoch_started = [0.0]
        active_epoch: list[int | None] = [None]
        best_selection: dict[str, Any] = {"score": float("-inf"), "epoch": None, "details": None}

        def select_deployment_checkpoint(trainer: Any) -> None:
            if selection is None or int(trainer.epoch) != active_epoch[0]:
                return
            from copy import deepcopy

            import cv2
            import numpy as np
            import torch

            from scripts.evaluate_scene_model import decode
            from visual_ai_agent.vision import letterbox

            # Match save_model's fp16 serialization before returning to fp32 CPU inference.
            ema = (
                deepcopy(trainer.ema.ema)
                .cpu()
                .half()
                .to(memory_format=torch.contiguous_format)
                .float()
                .eval()
            )
            for value in ema.state_dict().values():
                if isinstance(value, torch.Tensor) and value.is_floating_point():
                    torch.nan_to_num_(value)
            ema.end2end = True
            predictions = []
            previous_threads = torch.get_num_threads()
            try:
                torch.set_num_threads(2)
                for sample in selection["samples"]:
                    frame = cv2.imread(sample["image_path"])
                    if frame is None:
                        raise ValueError(f"Unreadable selection image: {sample['id']}")
                    tensor, transform = letterbox(frame, args.imgsz)
                    with torch.inference_mode():
                        raw = ema(torch.from_numpy(tensor))
                    if isinstance(raw, tuple):
                        raw = raw[0]
                    detections = decode(
                        np.asarray(raw.detach().numpy()), transform, 0.35, class_names
                    )
                    predictions.append(
                        {
                            "id": sample["id"],
                            "detections": [d.model_dump(mode="json") for d in detections],
                        }
                    )
            finally:
                torch.set_num_threads(previous_threads)
            details = grouped_macro_f1(selection, predictions)
            epoch = int(trainer.epoch) + 1
            row = {"epoch": epoch, **details}
            with (run_dir / "selection_metrics.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            if details["macro_f1"] > best_selection["score"]:
                checkpoint = run_dir / "weights/deployment-best.pt"
                if not trainer.last.is_file():
                    raise FileNotFoundError("Trainer did not save the current EMA checkpoint")
                shutil.copyfile(trainer.last, checkpoint)
                best_selection.update(score=details["macro_f1"], epoch=epoch, details=details)

        def on_train_epoch_start(trainer: Any) -> None:
            epoch_started[0] = time.monotonic()
            active_epoch[0] = int(trainer.epoch)

        def on_fit_epoch_end(trainer: Any) -> None:
            metrics = {
                str(key): float(value) if hasattr(value, "__float__") else value
                for key, value in getattr(trainer, "metrics", {}).items()
            }
            row = {
                "recorded_at": datetime.now(UTC).isoformat(),
                # Ultralytics calls this again for final best-weight validation,
                # temporarily advancing trainer.epoch without another training epoch.
                "phase": (
                    "training_epoch"
                    if int(trainer.epoch) == active_epoch[0]
                    else "final_validation"
                ),
                "epoch": (
                    int(trainer.epoch) + 1 if int(trainer.epoch) == active_epoch[0] else None
                ),
                "epoch_seconds": (
                    time.monotonic() - epoch_started[0]
                    if int(trainer.epoch) == active_epoch[0]
                    else None
                ),
                "metrics": metrics,
            }
            with (run_dir / "epoch_metrics.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            select_deployment_checkpoint(trainer)

        model.add_callback("on_train_epoch_start", on_train_epoch_start)
        model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
        model.train(
            **parameters,
            project=str(output),
            name=args.name,
            exist_ok=True,
            pretrained=True,
            save=True,
            plots=True,
            verbose=True,
        )
        actual_save_dir = Path(model.trainer.save_dir).resolve()
        if actual_save_dir != run_dir:
            raise RuntimeError(f"Trainer used unexpected output directory: {actual_save_dir}")
        best = run_dir / "weights/best.pt"
        if not best.is_file():
            raise FileNotFoundError(f"Training did not produce {best}")
        training_completed_at = datetime.now(UTC).isoformat()
        training_elapsed_seconds = time.monotonic() - started
        result: dict[str, Any] = {
            **preflight,
            "status": "completed",
            "training_completed_at": training_completed_at,
            "training_elapsed_seconds": training_elapsed_seconds,
            "ultralytics_best": str(best),
            "ultralytics_best_sha256": sha256(best),
        }
        deployment_best = run_dir / "weights/deployment-best.pt" if selection else best
        if selection:
            if not deployment_best.is_file():
                raise FileNotFoundError("Selection did not produce deployment-best.pt")
            result["deployment_selection"] = {
                "checkpoint": str(deployment_best),
                "checkpoint_sha256": sha256(deployment_best),
                "source_epoch": best_selection["epoch"],
                "macro_f1": best_selection["score"],
                "details": best_selection["details"],
            }
        result["trained_weights"] = str(deployment_best)
        result["trained_weights_sha256"] = sha256(deployment_best)
        if args.export:
            exported = Path(
                YOLO(str(deployment_best)).export(
                    format="onnx",
                    imgsz=args.imgsz,
                    nms=False,
                    dynamic=False,
                    simplify=True,
                    batch=1,
                    device="cpu",
                )
            ).resolve()
            result["exported_onnx"] = str(exported)
            result["exported_onnx_sha256"] = sha256(exported)
            result["export"] = {
                "source_checkpoint": str(deployment_best),
                "source_checkpoint_sha256": sha256(deployment_best),
                "format": "onnx",
                "imgsz": args.imgsz,
                "nms": False,
                "dynamic": False,
                "simplify": True,
                "batch": 1,
                "device": "cpu",
            }
        if sha256(weights) != source_hash:
            raise RuntimeError("Source checkpoint changed during training")
        result["completed_at"] = datetime.now(UTC).isoformat()
        result["elapsed_seconds"] = time.monotonic() - started
        write_json(manifest_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except BaseException as exc:
        failed = {
            **preflight,
            "status": "failed",
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_json(manifest_path, failed)
        raise


if __name__ == "__main__":
    main()
