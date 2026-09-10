#!/usr/bin/env python3
"""Train one isolated YOLO26n behavior classifier and export FP32 ONNX."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.behavior_preprocess import (  # noqa: E402,F401
    FullFrameTransform,
    preprocess_bgr,
    validate_roi,
)

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

os.environ.setdefault(
    "YOLO_CONFIG_DIR", str(REPOSITORY_ROOT / "harness/artifacts/behavior-settings")
)
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("YOLO_OFFLINE", "true")
os.environ.setdefault("ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS", "true")
os.environ.setdefault("WANDB_MODE", "disabled")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_dataset(data: Path, *, train_only: bool = False) -> dict[str, Any]:
    """Validate and fingerprint only train/val; deliberately never traverse test."""
    data = data.expanduser().resolve()
    if not data.is_dir():
        raise FileNotFoundError(f"Dataset directory does not exist: {data}")
    split_classes: dict[str, list[str]] = {}
    split_files: dict[str, list[Path]] = {}
    split_hashes: dict[str, dict[str, Path]] = {}
    splits = ("train",) if train_only else ("train", "val")
    for split in splits:
        root = data / split
        if not root.is_dir():
            raise FileNotFoundError(f"Required split does not exist: {root}")
        classes = sorted(path.name for path in root.iterdir() if path.is_dir())
        if len(classes) < 2:
            raise ValueError(f"{split} must contain at least two class directories")
        files = sorted(
            path.resolve()
            for class_name in classes
            for path in (root / class_name).rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        empty = [name for name in classes if not any(p.parent.name == name for p in files)]
        if empty:
            raise ValueError(f"{split} contains empty class directory: {empty[0]}")
        split_classes[split], split_files[split] = classes, files
        hashes: dict[str, Path] = {}
        for path in files:
            checksum = sha256(path)
            if checksum in hashes:
                raise ValueError(
                    f"Duplicate image content within {split}: {hashes[checksum]} and {path}"
                )
            hashes[checksum] = path
        split_hashes[split] = hashes
    if not train_only and split_classes["train"] != split_classes["val"]:
        raise ValueError("train and val class directories must match exactly")
    if not train_only:
        overlap = set(split_hashes["train"]) & set(split_hashes["val"])
        if overlap:
            checksum = sorted(overlap)[0]
            raise ValueError(
                "Image content appears in both train and val: "
                f"{split_hashes['train'][checksum]} and {split_hashes['val'][checksum]}"
            )
    digest = hashlib.sha256()
    counts: dict[str, dict[str, int]] = {}
    for split in splits:
        counts[split] = {name: 0 for name in split_classes[split]}
        for path in split_files[split]:
            relative = path.relative_to(data)
            counts[split][relative.parts[1]] += 1
            digest.update(relative.as_posix().encode())
            digest.update(bytes.fromhex(sha256(path)))
    groups_verified = False
    if not train_only:
        manifest_path = data.parent / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Independent validation requires a source-group manifest")
        source_manifest = json.loads(manifest_path.read_text())
        samples = {
            (data.parent / row["path"]).resolve(): row
            for row in source_manifest["samples"]
            if row["task"] == data.name and row["split"] in splits
        }
        group_splits: dict[str, str] = {}
        for split, paths in split_files.items():
            for path in paths:
                row = samples.get(path)
                if row is None or row["sha256"] != sha256(path) or row["split"] != split:
                    raise ValueError("Image does not match source-group manifest")
                group = row["source_sha256"]
                if group in group_splits and group_splits[group] != split:
                    raise ValueError("Source video group crosses train and val")
                group_splits[group] = split
        groups_verified = True
    return {
        "root": str(data),
        "classes": dict(enumerate(split_classes["train"])),
        "counts": counts,
        "sha256": digest.hexdigest(),
        "source_group_isolation_verified": groups_verified,
    }


def validate_paths(
    data: Path, weights: Path, output: Path, *, train_only: bool = False
) -> tuple[dict[str, Any], Path, Path]:
    dataset = inspect_dataset(data, train_only=train_only)
    weights = weights.expanduser().resolve()
    output = output.expanduser().resolve()
    if not weights.is_file() or weights.suffix.lower() != ".pt":
        raise FileNotFoundError(f"Local .pt weights do not exist: {weights}")
    if output.exists():
        raise FileExistsError(f"Output path already exists; refusing to overwrite: {output}")
    return dataset, weights, output


def validate_source_sizes(
    data: Path,
    expected_size: tuple[int, int],
    *,
    train_only: bool = False,
) -> None:
    """Reject cached/resized inputs when an ROI is calibrated to a source frame size."""
    expected_width, expected_height = expected_size
    if expected_width <= 0 or expected_height <= 0:
        raise ValueError("source-size width and height must be positive")
    splits = ("train",) if train_only else ("train", "val")
    for split in splits:
        for path in sorted((data.resolve() / split).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Cannot decode training image: {path}")
            height, width = image.shape[:2]
            if (width, height) != expected_size:
                raise ValueError(
                    f"Source frame size mismatch for {path}: expected "
                    f"{expected_width}x{expected_height}, got {width}x{height}"
                )


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--imgsz", type=int, default=320)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--freeze", type=int, default=5)
    parser.add_argument("--roi", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--source-size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"))
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Train fixed epochs without reading or reporting an independent validation split",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if min(args.epochs, args.imgsz, args.batch) <= 0 or args.freeze < 0:
        raise ValueError("epochs, imgsz and batch must be positive; freeze must be non-negative")
    dataset, weights, output = validate_paths(
        args.data, args.weights, args.output, train_only=args.train_only
    )
    roi = validate_roi(tuple(args.roi) if args.roi is not None else None)
    source_size = tuple(args.source_size) if args.source_size is not None else None
    if roi is not None and source_size is None:
        raise ValueError("--source-size WIDTH HEIGHT is required when --roi is used")
    if source_size is not None:
        if roi is None:
            raise ValueError("--source-size is only valid together with --roi")
        validate_source_sizes(args.data, source_size, train_only=args.train_only)

    import torch
    from ultralytics import YOLO
    from ultralytics.data import ClassificationDataset
    from ultralytics.models.yolo.classify import ClassificationTrainer

    class BehaviorClassificationDataset(ClassificationDataset):
        def __init__(self, root: str, trainer_args, augment: bool = False, prefix: str = ""):
            super().__init__(root, trainer_args, augment=augment, prefix=prefix)
            self.torch_transforms = FullFrameTransform(
                trainer_args.imgsz, training=augment, roi=roi
            )

    class BehaviorClassificationTrainer(ClassificationTrainer):
        def get_dataset(self):
            # Bypass check_cls_dataset, whose inventory pass traverses an optional test split.
            return {
                "train": Path(self.args.data).resolve() / "train",
                # The framework requires a loader during setup. In train-only mode it
                # is constructed from train, but validate/final_eval below never iterate it.
                "val": Path(self.args.data).resolve() / ("train" if args.train_only else "val"),
                "test": None,
                "nc": len(dataset["classes"]),
                "names": dataset["classes"],
                "channels": 3,
            }

        def build_dataset(self, img_path: str, mode: str = "train", batch=None):
            return BehaviorClassificationDataset(
                img_path, self.args, augment=mode == "train", prefix=mode
            )

        def validate(self):
            if args.train_only:
                return {}, 0.0
            return super().validate()

        def final_eval(self):
            if not args.train_only:
                return super().final_eval()
            return None

    torch.set_num_threads(2)
    parameters = {
        "data": str(Path(dataset["root"])),
        "model": str(weights),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "device": args.device,
        "batch": args.batch,
        "seed": args.seed,
        "freeze": args.freeze,
        "workers": 0,
        "project": str(output.parent),
        "name": output.name,
        # The directory is reserved atomically immediately before trainer construction.
        "exist_ok": True,
        "pretrained": True,
        "plots": True,
        "save": True,
        "verbose": True,
        "amp": False,
        "val": not args.train_only,
        "patience": 0 if args.train_only else 100,
        "optimizer": "AdamW",
        "lr0": 0.0002,
        "lrf": 0.1,
        "warmup_epochs": 2.0,
        "warmup_bias_lr": 0.0,
        "weight_decay": 0.0005,
        "dropout": 0.1,
        # Explicitly disable Ultralytics' stock classification augmentation.
        # The custom dataset installs FullFrameTransform instead.
        "auto_augment": None,
        "erasing": 0.0,
        "fliplr": 0.0,
        "flipud": 0.0,
    }
    started = time.monotonic()
    manifest_path = output / "training-manifest.json"
    manifest: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "dataset": dataset,
        "source_weights": str(weights),
        "source_weights_sha256": sha256(weights),
        "parameters": parameters,
        "versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": importlib.metadata.version("ultralytics"),
        },
        "preprocessing": {
            "function": "scripts.behavior_preprocess.preprocess_bgr",
            "layout": "NCHW",
            "dtype": "float32",
            "range": [0.0, 1.0],
            "color": "RGB",
            "resize": "aspect-ratio-preserving letterbox",
            "fill_rgb": [114, 114, 114],
            "imgsz": args.imgsz,
            "roi": list(roi) if roi is not None else None,
            "expected_source_frame_size": list(source_size) if source_size is not None else None,
        },
        "validation": {
            "mode": "none_train_only" if args.train_only else "independent_val_split",
            "independent_validation_performed": not args.train_only,
            "metrics_available": not args.train_only,
            "metrics_note": (
                "No validation was run; validation-looking placeholders or training CSV fields "
                "must not be used for model quality claims."
                if args.train_only
                else "Metrics use the independent val directory."
            ),
        },
    }
    # Reserve the exact path atomically. If another process wins the race, this
    # raises before the exception handler and never writes into its directory.
    output.mkdir(parents=True, exist_ok=False)
    try:
        trainer = BehaviorClassificationTrainer(overrides=parameters)
        if Path(trainer.save_dir).resolve() != output:
            raise RuntimeError(f"Trainer selected unexpected output path: {trainer.save_dir}")
        write_json(manifest_path, manifest)
        trainer.train()
        if Path(trainer.save_dir).resolve() != output:
            raise RuntimeError(f"Trainer used unexpected output path: {trainer.save_dir}")
        checkpoint = output / f"weights/{'last' if args.train_only else 'best'}.pt"
        if not checkpoint.is_file():
            raise FileNotFoundError(f"Training did not produce {checkpoint}")
        exported = Path(
            YOLO(str(checkpoint)).export(
                format="onnx", imgsz=args.imgsz, batch=1, dynamic=False, simplify=True, device="cpu"
            )
        ).resolve()
        manifest.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - started,
            completed_epochs=int(trainer.epoch) + 1,
            selected_checkpoint=str(checkpoint),
            selected_checkpoint_sha256=sha256(checkpoint),
            checkpoint_selection="last_epoch" if args.train_only else "best_independent_val",
            onnx=str(exported),
            onnx_sha256=sha256(exported),
        )
        write_json(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    except BaseException as exc:
        manifest.update(
            status="failed",
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
