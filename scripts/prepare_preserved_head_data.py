#!/usr/bin/env python3
"""Build r02 data that preserves the pretrained COCO 80-class detector head."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import cv2
import yaml

BUSINESS_CLASSES = {"bottle", "cup", "cell phone"}
DEFAULT_ROOT = Path("harness/artifacts/finetune-20260909")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clipped_xyxy(annotation: dict, width: int, height: int) -> list[float]:
    x, y, box_width, box_height = map(float, annotation["bbox"])
    result = [
        max(0.0, x),
        max(0.0, y),
        min(float(width), x + box_width),
        min(float(height), y + box_height),
    ]
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"Non-finite COCO box: {annotation['id']}")
    if not (result[0] < result[2] and result[1] < result[3]):
        raise ValueError(f"Empty clipped COCO box: {annotation['id']}")
    return result


def yolo_line(class_id: int, bbox: list[float], width: int, height: int) -> str:
    x1, y1, x2, y2 = bbox
    values = (
        (x1 + x2) / (2 * width),
        (y1 + y2) / (2 * height),
        (x2 - x1) / width,
        (y2 - y1) / height,
    )
    if not all(0 <= value <= 1 for value in values):
        raise ValueError(f"Normalized box outside [0,1]: {values}")
    return str(class_id) + " " + " ".join(f"{value:.10f}" for value in values)


def build(source_manifest: Path, annotations_path: Path, output: Path, evidence: Path) -> dict:
    if output.exists():
        raise ValueError(f"Refusing to overwrite frozen dataset: {output}")
    source_manifest = source_manifest.resolve()
    annotations_path = annotations_path.resolve()
    evidence = evidence.resolve()
    source = load_json(source_manifest)
    coco = load_json(annotations_path)

    categories = sorted(coco["categories"], key=lambda row: row["id"])
    if len(categories) != 80:
        raise ValueError(f"Expected 80 COCO categories, found {len(categories)}")
    names = {index: row["name"] for index, row in enumerate(categories)}
    class_by_coco_id = {row["id"]: index for index, row in enumerate(categories)}
    class_by_name = {name: index for index, name in names.items()}
    if {name: class_by_name[name] for name in BUSINESS_CLASSES} != {
        "bottle": 39,
        "cup": 41,
        "cell phone": 67,
    }:
        raise ValueError("Unexpected official COCO-to-YOLO class mapping")

    annotations_by_image: dict[int, list[dict]] = defaultdict(list)
    for annotation in coco["annotations"]:
        annotations_by_image[annotation["image_id"]].append(annotation)

    hashes: dict[str, str] = {}
    groups: dict[str, str] = {}
    frozen = []
    annotation_counts: Counter[str] = Counter()
    skipped_crowd_counts: Counter[str] = Counter()
    public_count = private_train_count = private_test_count = 0

    for sample in source["samples"]:
        sample = dict(sample)
        split = sample["split"]
        if split not in {"train", "val", "test"}:
            raise ValueError(f"Unexpected split: {split}")
        source_image = Path(sample["image_path"])
        if not source_image.is_absolute():
            source_image = source_manifest.parent / source_image
        image_hash = sha256(source_image)
        if image_hash != sample["sha256"]:
            raise ValueError(f"Image hash mismatch: {sample['id']}")
        if image_hash in hashes:
            raise ValueError(f"Duplicate bytes: {sample['id']} and {hashes[image_hash]}")
        hashes[image_hash] = sample["id"]
        group = sample.get("group", sample["id"])
        if group in groups and groups[group] != split:
            raise ValueError(f"Capture group crosses splits: {group}")
        groups[group] = split

        frame = cv2.imread(str(source_image), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Cannot decode image: {sample['id']}")
        height, width = frame.shape[:2]
        if (width, height) != (sample["width"], sample["height"]):
            raise ValueError(f"Image dimensions disagree: {sample['id']}")

        is_public = sample["id"].startswith("coco-train2017-")
        standard_annotations = []
        labels = []
        if is_public:
            public_count += 1
            image_id = int(sample["id"].removeprefix("coco-train2017-"))
            for annotation in sorted(annotations_by_image[image_id], key=lambda row: row["id"]):
                name = names[class_by_coco_id[annotation["category_id"]]]
                if annotation.get("iscrowd", 0):
                    if name in BUSINESS_CLASSES:
                        raise ValueError(
                            f"Public target crowd escaped r01 filtering: {sample['id']}"
                        )
                    skipped_crowd_counts[name] += 1
                    continue
                bbox = clipped_xyxy(annotation, width, height)
                class_id = class_by_name[name]
                labels.append(yolo_line(class_id, bbox, width, height))
                standard_annotations.append(
                    {
                        "category": name,
                        "class_id": class_id,
                        "bbox": bbox,
                        "iscrowd": False,
                        "coco_annotation_id": annotation["id"],
                    }
                )
                annotation_counts[name] += 1
            provenance = "COCO 2017 train, all non-crowd annotations"
        else:
            if split == "train":
                private_train_count += 1
            elif split == "test":
                private_test_count += 1
            else:
                raise ValueError("Private samples may only be train or test")
            for annotation in sample["annotations"]:
                name = annotation["category"]
                if name not in BUSINESS_CLASSES:
                    raise ValueError(f"Unexpected private class: {name}")
                if annotation.get("iscrowd", False):
                    raise ValueError(f"Private crowd annotation: {sample['id']}")
                bbox = list(map(float, annotation["bbox"]))
                if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
                    raise ValueError(f"Invalid private box: {sample['id']}")
                class_id = class_by_name[name]
                labels.append(yolo_line(class_id, bbox, width, height))
                standard_annotations.append(
                    {"category": name, "class_id": class_id, "bbox": bbox, "iscrowd": False}
                )
                annotation_counts[name] += 1
            provenance = "private manual labels for three business classes only"

        suffix = source_image.suffix.lower()
        image_path = output / "images" / split / f"{sample['id']}{suffix}"
        label_path = output / "labels" / split / f"{sample['id']}.txt"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_image, image_path)
        label_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
        sample.update(
            image_path=str(image_path.resolve()),
            label_path=str(label_path.resolve()),
            source_image_path=str(source_image.resolve()),
            group=group,
            annotations=standard_annotations,
            annotation_scope=provenance,
        )
        frozen.append(sample)

    counts = {
        split: sum(row["split"] == split for row in frozen) for split in ("train", "val", "test")
    }
    if counts != {"train": 105, "val": 24, "test": 4}:
        raise ValueError(f"Unexpected frozen split counts: {counts}")
    if (public_count, private_train_count, private_test_count) != (120, 9, 4):
        raise ValueError("Unexpected public/private composition")

    record = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_revision": "r02-preserved-coco80-head",
        "decision": (
            "Created after measured r01 three-class-head evaluation regressed severely; "
            "r02 preserves the pretrained 80-class head. This is an evidence-led retry, not "
            "a blind test-driven relabeling."
        ),
        "sources": {
            "dataset_r01_manifest": {
                "path": str(source_manifest),
                "sha256": sha256(source_manifest),
            },
            "coco_instances_train2017": {
                "path": str(annotations_path),
                "sha256": sha256(annotations_path),
            },
            "r01_evaluation": {"path": str(evidence), "sha256": sha256(evidence)},
        },
        "names": names,
        "counts": counts,
        "composition": {
            "public_coco": public_count,
            "private_train": private_train_count,
            "private_test": private_test_count,
        },
        "annotation_counts": dict(sorted(annotation_counts.items())),
        "crowd_policy": {
            "public_non_target": "skip individual iscrowd annotations and keep the image",
            "public_business_targets": "none present; fail if encountered",
            "skipped_by_class": dict(sorted(skipped_crowd_counts.items())),
        },
        "limitations": [
            (
                "The 9 private training images and 4 private test images were exhaustively "
                "reviewed only for bottle, cup, and cell phone; the other 77 COCO classes may "
                "be present but are not comprehensively labeled."
            ),
            (
                "Private samples are a small fraction of training (9/105) and evaluation "
                "claims are restricted to the three business classes."
            ),
            (
                "The 24-image public validation split comes from COCO train2017 and is not "
                "unseen to COCO-pretrained weights."
            ),
            (
                "Private test images preserve their original groups and are not used for "
                "training selection or label expansion."
            ),
        ],
        "samples": frozen,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    test_record = {**record, "samples": [row for row in frozen if row["split"] == "test"]}
    test_record["counts"] = {"train": 0, "val": 0, "test": counts["test"]}
    (output / "test-manifest.json").write_text(
        json.dumps(test_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "dataset.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(output.resolve()),
                "train": "images/train",
                "val": "images/val",
                "test": "images/test",
                "names": names,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-manifest", type=Path, default=DEFAULT_ROOT / "dataset-r01/manifest.json"
    )
    parser.add_argument(
        "--annotations", type=Path, default=DEFAULT_ROOT / "public/source/instances_train2017.json"
    )
    parser.add_argument("--evidence", type=Path, default=DEFAULT_ROOT / "evaluation-r01.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_ROOT / "dataset-r02")
    args = parser.parse_args()
    record = build(args.source_manifest, args.annotations, args.output, args.evidence)
    print(json.dumps(record["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
