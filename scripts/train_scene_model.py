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
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPOSITORY_ROOT / "harness/artifacts/finetune-20260909/runs"
EXPECTED_NAMES = {0: "bottle", 1: "cup", 2: "cell phone"}
OFFICIAL_MANIFEST = REPOSITORY_ROOT / "model_manifests/yolo26n-e2e.onnx.json"
REQUIRED_CLASSES = {"bottle", "cup", "cell phone"}

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
    parser.add_argument("--mosaic", type=float, default=0.5)
    parser.add_argument("--close-mosaic", type=int, default=5)
    parser.add_argument("--device", default="auto", help="auto, cpu, mps, or a CUDA device")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--export", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-only", action="store_true")
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
    device = choose_device(args.device)
    amp = args.amp
    run_dir = output / args.name
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
        "dataset_yaml": str(data),
        "dataset_sha256": data_hash,
        "dataset_hashed_file_count": dataset_file_count,
        "dataset_label_summary": label_summary,
        "class_names": class_names,
        "head_mode": "preserved_coco_80" if args.preserve_coco_head else "rebuilt_three_class",
        "parameters": parameters,
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
        from ultralytics import YOLO
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
        model = YOLO(str(weights))
        epoch_started = [0.0]
        active_epoch: list[int | None] = [None]

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
            "trained_weights": str(best),
            "trained_weights_sha256": sha256(best),
        }
        if args.export:
            exported = Path(
                YOLO(str(best)).export(
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
