#!/usr/bin/env python3
"""Prepare a small, deterministic three-class dataset from COCO 2017 train.

Only individual selected images are downloaded. COCO validation/regression images are
never candidates. Images are split before export and every retained target instance
is written in normalized YOLO format. Target crowd annotations are excluded by
excluding their entire image; non-target annotations are intentionally ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2

SEED = 20260909
TARGETS = ("bottle", "cup", "cell phone")
COCO_IDS = {"bottle": 44, "cup": 47, "cell phone": 77}
YOLO_IDS = {name: index for index, name in enumerate(TARGETS)}
NEGATIVE_CLASSES = {
    "backpack",
    "book",
    "bowl",
    "chair",
    "clock",
    "keyboard",
    "laptop",
    "mouse",
    "remote",
    "scissors",
    "toilet",
    "toothbrush",
    "vase",
    "wine glass",
}
DEFAULT_ROOT = Path("harness/artifacts/finetune-20260909/public")
ANNOTATION_MEMBER = "annotations/instances_train2017.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def resolve_annotations(path: Path, root: Path) -> Path:
    if path.suffix.lower() != ".zip":
        return path
    destination = root / "source" / "instances_train2017.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        with archive.open(ANNOTATION_MEMBER) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
    return destination


def regression_hashes(paths: list[Path]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        manifest = load_json(path)
        for sample in manifest.get("samples", []):
            value = sample.get("sha256")
            if value:
                hashes.add(value)
    return hashes


def download_image(url: str, destination: Path) -> None:
    """Download atomically, replacing a truncated prior attempt."""
    if destination.exists():
        with destination.open("rb") as stream:
            stream.seek(-2, 2)
            if stream.read() == b"\xff\xd9":
                return
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url.replace("http://", "https://", 1))
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with (
                urllib.request.urlopen(request, timeout=20) as source,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(source, output)
            with temporary.open("rb") as stream:
                stream.seek(-2, 2)
                if stream.read() != b"\xff\xd9":
                    raise RuntimeError("downloaded JPEG is truncated")
            temporary.replace(destination)
            return
        except Exception as error:  # retried with a strict bound
            last_error = error
            temporary.unlink(missing_ok=True)
    raise RuntimeError(f"Unable to download {url}: {last_error}")


def select(data: dict[str, Any], per_stratum: int) -> list[dict[str, Any]]:
    categories = {row["id"]: row["name"] for row in data["categories"]}
    observed = {name: cid for cid, name in categories.items() if name in TARGETS}
    if observed != COCO_IDS:
        raise RuntimeError(f"Unexpected COCO category mapping: {observed}")
    images = {row["id"]: row for row in data["images"]}
    annotations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in data["annotations"]:
        annotations[row["image_id"]].append(row)

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    target_ids = set(COCO_IDS.values())
    for offset, name in enumerate(TARGETS):
        cid = COCO_IDS[name]
        candidates = []
        for image_id, rows in annotations.items():
            if image_id in selected_ids or not any(row["category_id"] == cid for row in rows):
                continue
            # Exclude the whole image if any target instance is a crowd so that the
            # retained images have complete, unambiguous target box supervision.
            if any(row["category_id"] in target_ids and row.get("iscrowd", 0) for row in rows):
                continue
            candidates.append(image_id)
        random.Random(SEED + offset).shuffle(candidates)
        chosen = candidates[:per_stratum]
        if len(chosen) != per_stratum:
            raise RuntimeError(f"Insufficient candidates for {name}: {len(chosen)}")
        for image_id in chosen:
            selected.append({"stratum": name, **images[image_id]})
            selected_ids.add(image_id)

    negative_ids = {cid for cid, name in categories.items() if name in NEGATIVE_CLASSES}
    candidates = []
    for image_id, rows in annotations.items():
        ids = {row["category_id"] for row in rows}
        if (
            image_id not in selected_ids
            and ids.isdisjoint(target_ids)
            and ids.intersection(negative_ids)
        ):
            candidates.append(image_id)
    random.Random(SEED + 100).shuffle(candidates)
    chosen = candidates[:per_stratum]
    if len(chosen) != per_stratum:
        raise RuntimeError(f"Insufficient negative candidates: {len(chosen)}")
    for image_id in chosen:
        selected.append({"stratum": "negative", **images[image_id]})

    # Exact 80/20 split within every selection stratum, with stable shuffles.
    for offset, stratum in enumerate((*TARGETS, "negative")):
        rows = [row for row in selected if row["stratum"] == stratum]
        random.Random(SEED + 1000 + offset).shuffle(rows)
        val_count = len(rows) // 5
        val_ids = {row["id"] for row in rows[:val_count]}
        for row in selected:
            if row["stratum"] == stratum:
                row["split"] = "val" if row["id"] in val_ids else "train"
    return sorted(selected, key=lambda row: (row["split"], row["stratum"], row["id"]))


def normalized_box(annotation: dict[str, Any], width: int, height: int) -> tuple[float, ...]:
    x, y, box_width, box_height = map(float, annotation["bbox"])
    x1, y1 = max(0.0, x), max(0.0, y)
    x2, y2 = min(float(width), x + box_width), min(float(height), y + box_height)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid clipped bbox for annotation {annotation['id']}")
    return ((x1 + x2) / 2 / width, (y1 + y2) / 2 / height, (x2 - x1) / width, (y2 - y1) / height)


def prepare(annotation_source: Path, root: Path, per_stratum: int, regressions: list[Path]) -> None:
    annotation_path = resolve_annotations(annotation_source, root)
    data = load_json(annotation_path)
    selected = select(data, per_stratum)
    annotations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in data["annotations"]:
        if row["category_id"] in COCO_IDS.values():
            annotations[row["image_id"]].append(row)
    licenses = {row["id"]: row for row in data["licenses"]}
    forbidden_hashes = regression_hashes(regressions)
    seen_hashes: dict[str, str] = {}
    samples = []
    counts: Counter[str] = Counter()

    downloads = []
    for row in selected:
        image_dir = root / "images" / row["split"]
        image_dir.mkdir(parents=True, exist_ok=True)
        downloads.append((row["coco_url"], image_dir / row["file_name"]))
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda item: download_image(*item), downloads))

    for row in selected:
        split = row["split"]
        image_dir, label_dir = root / "images" / split, root / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        destination = image_dir / row["file_name"]
        image_hash = sha256(destination)
        if image_hash in forbidden_hashes:
            raise RuntimeError(f"Regression-set image reused: {row['file_name']} ({image_hash})")
        if image_hash in seen_hashes:
            raise RuntimeError(
                f"Duplicate image across dataset: {row['file_name']} and {seen_hashes[image_hash]}"
            )
        seen_hashes[image_hash] = f"{split}/{row['file_name']}"

        decoded = cv2.imread(str(destination), cv2.IMREAD_COLOR)
        if decoded is None:
            raise RuntimeError(f"Unable to decode selected image: {destination}")
        actual_height, actual_width = decoded.shape[:2]
        if (actual_width, actual_height) != (row["width"], row["height"]):
            raise RuntimeError(
                f"COCO dimensions disagree for {destination}: "
                f"metadata={(row['width'], row['height'])}, "
                f"decoded={(actual_width, actual_height)}"
            )

        target_rows = sorted(annotations[row["id"]], key=lambda item: item["id"])
        lines, boxes, standard_annotations = [], [], []
        for annotation in target_rows:
            name = next(name for name, cid in COCO_IDS.items() if cid == annotation["category_id"])
            box = normalized_box(annotation, row["width"], row["height"])
            lines.append(f"{YOLO_IDS[name]} " + " ".join(f"{value:.8f}" for value in box))
            boxes.append(
                {
                    "category": name,
                    "coco_annotation_id": annotation["id"],
                    "bbox_xywh": annotation["bbox"],
                }
            )
            center_x, center_y, box_width, box_height = box
            standard_annotations.append(
                {
                    "category": name,
                    "bbox": [
                        (center_x - box_width / 2) * actual_width,
                        (center_y - box_height / 2) * actual_height,
                        (center_x + box_width / 2) * actual_width,
                        (center_y + box_height / 2) * actual_height,
                    ],
                    "iscrowd": False,
                }
            )
            counts[f"instances/{name}"] += 1
        label_path = label_dir / f"{Path(row['file_name']).stem}.txt"
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        counts[f"images/{split}"] += 1
        counts[f"strata/{row['stratum']}"] += 1
        license_row = licenses[row["license"]]
        samples.append(
            {
                "id": f"coco-train2017-{row['id']:012d}",
                "split": split,
                "stratum": row["stratum"],
                "image_path": f"images/{split}/{row['file_name']}",
                "label_path": f"labels/{split}/{label_path.name}",
                "source_url": row["coco_url"],
                "license": {"name": license_row["name"], "url": license_row["url"]},
                "sha256": image_hash,
                "width": actual_width,
                "height": actual_height,
                "annotations": standard_annotations,
                "target_annotations": boxes,
            }
        )

    manifest = {
        "schema_version": 1,
        "dataset": "COCO 2017 train deterministic three-class subset",
        "created_by": "scripts/prepare_finetune_data.py",
        "seed": SEED,
        "classes": [
            {"yolo_id": YOLO_IDS[name], "name": name, "coco_category_id": COCO_IDS[name]}
            for name in TARGETS
        ],
        "selection": {
            "per_stratum": per_stratum,
            "strata": [*TARGETS, "negative"],
            "split": "image-independent stratified 80/20",
        },
        "annotation_policy": {
            "target_instances": "all bottle/cup/cell phone boxes retained in every selected image",
            "crowd": "any image containing an iscrowd target annotation is excluded",
            "other_categories": (
                "ignored because this is a three-class detector; negative images contain "
                "no target annotations"
            ),
            "coordinates": (
                "COCO xywh clipped to actual image width/height, then normalized YOLO cx cy w h"
            ),
        },
        "provenance": {
            "dataset_home": "https://cocodataset.org/#download",
            "dataset_terms": "https://cocodataset.org/#termsofuse",
            "annotation_source": str(annotation_source),
            "annotation_source_sha256": sha256(annotation_source),
            "annotation_member": ANNOTATION_MEMBER,
            "annotation_sha256": sha256(annotation_path),
            "per_image_license_preserved": True,
        },
        "regression_exclusion": {
            "manifests": [str(path) for path in regressions],
            "sha256_count": len(forbidden_hashes),
            "overlap": 0,
        },
        "counts": dict(sorted(counts.items())),
        "samples": samples,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "dataset.yaml").write_text(
        f"path: {root.resolve()}\n"
        "train: images/train\nval: images/val\nnames:\n"
        "  0: bottle\n  1: cup\n  2: cell phone\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--annotations",
        type=Path,
        required=True,
        help="instances_train2017.json or official annotations ZIP",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--per-stratum", type=int, default=30)
    parser.add_argument("--regression-manifest", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.per_stratum <= 0 or args.per_stratum * 4 > 200:
        parser.error("dataset must contain 1..200 images")
    prepare(args.annotations, args.root, args.per_stratum, args.regression_manifest)


if __name__ == "__main__":
    main()
