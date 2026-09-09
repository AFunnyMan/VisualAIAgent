"""Freeze public and manually reviewed scene manifests into an isolated YOLO dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import cv2
import yaml

NAMES = {0: "bottle", 1: "cup", 2: "cell phone"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(manifests, output):
    if output.exists():
        raise ValueError("Refusing to overwrite a frozen dataset")
    samples, seen_ids, hashes, groups = [], set(), {}, {}
    sources = []
    for manifest_path in manifests:
        manifest_path = manifest_path.resolve()
        source = json.loads(manifest_path.read_text())
        sources.append({"path": str(manifest_path), "sha256": sha256(manifest_path)})
        for original in source["samples"]:
            sample = dict(original)
            if "label_method" in source and not sample.get("group"):
                raise ValueError("Manually annotated scene samples require a capture group")
            split = sample["split"]
            if split not in {"train", "val", "test"}:
                raise ValueError(f"Unexpected split: {split}")
            sample_id = sample["id"]
            if not isinstance(sample_id, str) or Path(sample_id).name != sample_id:
                raise ValueError("Invalid sample ID")
            if sample_id in seen_ids:
                raise ValueError(f"Duplicate sample ID: {sample_id}")
            seen_ids.add(sample_id)
            path = Path(sample["image_path"])
            path = path if path.is_absolute() else manifest_path.parent / path
            checksum = sha256(path)
            if checksum != sample["sha256"]:
                raise ValueError(f"Image hash mismatch: {sample_id}")
            if checksum in hashes:
                raise ValueError(f"Duplicate image bytes: {sample_id}")
            hashes[checksum] = split
            # Entire camera capture groups stay in one split; public images default to own ID.
            group = sample.get("group", sample_id)
            if group in groups and groups[group] != split:
                raise ValueError(f"Capture group crosses splits: {group}")
            groups[group] = split
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"Cannot decode {sample_id}")
            height, width = frame.shape[:2]
            if (width, height) != (sample["width"], sample["height"]):
                raise ValueError(f"Image dimensions disagree: {sample_id}")
            labels = []
            for annotation in sample["annotations"]:
                if annotation["category"] not in NAMES.values():
                    continue
                if annotation.get("iscrowd", False):
                    raise ValueError("Training manifest must exclude target crowd images")
                x1, y1, x2, y2 = annotation["bbox"]
                if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                    raise ValueError("Non-finite box")
                if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                    raise ValueError(f"Out-of-bounds box: {sample_id}")
                class_id = next(k for k, v in NAMES.items() if v == annotation["category"])
                values = (
                    (x1 + x2) / (2 * width),
                    (y1 + y2) / (2 * height),
                    (x2 - x1) / width,
                    (y2 - y1) / height,
                )
                labels.append(str(class_id) + " " + " ".join(f"{v:.10f}" for v in values))
            sample.update(source_image_path=str(path.resolve()), group=group)
            samples.append((sample, labels, path))
    if not all(any(s[0]["split"] == split for s in samples) for split in ("train", "val", "test")):
        raise ValueError("Each train/val/test split must contain images")
    output.mkdir(parents=True)
    frozen = []
    for sample, labels, path in samples:
        split = sample["split"]
        image_path = output / "images" / split / (sample["id"] + path.suffix.lower())
        label_path = output / "labels" / split / (sample["id"] + ".txt")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, image_path)
        label_path.write_text("\n".join(labels) + ("\n" if labels else ""))
        sample["image_path"] = str(image_path.resolve())
        frozen.append(sample)
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "names": NAMES,
        "samples": frozen,
        "counts": {
            split: sum(s["split"] == split for s in frozen) for split in ("train", "val", "test")
        },
        "limits": (
            "Same private cup instance across groups; test measures held-out view only. "
            "COCO train-derived val is not unseen to pretrained weights."
        ),
    }
    (output / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    (output / "test-manifest.json").write_text(
        json.dumps(
            {
                **record,
                "samples": [s for s in frozen if s["split"] == "test"],
                "counts": {"train": 0, "val": 0, "test": record["counts"]["test"]},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    (output / "dataset.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(output.resolve()),
                "train": "images/train",
                "val": "images/val",
                "test": "images/test",
                "names": NAMES,
            },
            sort_keys=False,
        )
    )
    return record["counts"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", action="append", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.manifest, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
