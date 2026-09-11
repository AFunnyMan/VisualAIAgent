#!/usr/bin/env python3
"""Build the isolated, manually reviewed fixed-camera key detector dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import cv2
import yaml

from scripts.training_source_policy import verify_development_sources

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "harness/evaluations/recognition-20260911-sources.json"
INBOX = ROOT / "data/training-inbox/development"

# Whole videos belong to one split. Boxes were reviewed against 1920x1080 source frames.
# A box touching an image edge describes the visible part of a truncated key; frames where
# a hand makes the key identity ambiguous are deliberately not sampled.
SOURCES = {
    "2026-09-11 23-01-33.mkv": {
        "split": "train",
        "samples": [
            (0.0, (1215, 862, 1385, 951)),
            (1.0, (1215, 862, 1385, 951)),
            (6.0, (1180, 938, 1350, 992)),
            (12.0, (1200, 805, 1390, 888)),
            (13.0, (1180, 805, 1375, 888)),
            (18.0, (750, 935, 810, 1080)),
            (19.0, (750, 935, 810, 1080)),
            (24.0, (1290, 909, 1360, 1080)),
            (27.0, (1293, 828, 1365, 990)),
        ],
    },
    "2026-09-11 23-05-02.mkv": {
        "split": "val",
        "samples": [
            (0.0, None),
            (8.0, None),
            (16.0, None),
            (32.0, None),
            (48.0, None),
            (56.0, (1490, 937, 1575, 1080)),
            (64.0, (1490, 937, 1575, 1080)),
            (80.0, (1490, 937, 1575, 1080)),
            (88.0, (1490, 937, 1575, 1080)),
            (96.0, (1490, 937, 1575, 1080)),
        ],
    },
    "2026-09-11 22-58-45.mkv": {
        "split": "train",
        "samples": [(4.0, None), (20.0, None), (36.0, None)],
    },
    "2026-09-11 22-59-29.mkv": {
        "split": "val",
        "samples": [(4.0, None), (24.0, None), (44.0, None)],
    },
    "2026-09-11 23-02-14.mkv": {
        "split": "train",
        "samples": [(4.0, (1290, 828, 1365, 985)), (20.0, None), (36.0, None)],
    },
    "2026-09-11 23-03-14.mkv": {
        "split": "val",
        "samples": [(4.0, None), (20.0, None), (36.0, None)],
    },
    "2026-09-11 23-04-15.mkv": {
        "split": "train",
        "samples": [(4.0, None), (16.0, None), (28.0, None)],
    },
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_frame(capture: cv2.VideoCapture, timestamp: float):
    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
    ok, frame = capture.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not decode frame at {timestamp:.3f}s")
    return frame


def build(output: Path) -> dict:
    if output.exists():
        raise ValueError(f"Refusing to overwrite frozen dataset: {output}")
    paths = [INBOX / name for name in SOURCES]
    verified = verify_development_sources(paths, REGISTRY)
    output.mkdir(parents=True)
    rows = []
    for path in paths:
        spec = SOURCES[path.name]
        split = spec["split"]
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        try:
            for timestamp, box in spec["samples"]:
                frame = extract_frame(capture, timestamp)
                height, width = frame.shape[:2]
                sample_id = f"{verified[str(path.resolve())][:12]}-{round(timestamp * fps):06d}"
                image = output / "images" / split / f"{sample_id}.jpg"
                label = output / "labels" / split / f"{sample_id}.txt"
                image.parent.mkdir(parents=True, exist_ok=True)
                label.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(image), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                    raise RuntimeError(f"Could not write {image}")
                annotations = []
                text = ""
                if box is not None:
                    x1, y1, x2, y2 = box
                    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                        raise ValueError(f"Invalid box for {sample_id}: {box}")
                    annotations.append(
                        {
                            "category": "key",
                            "bbox": list(box),
                            "iscrowd": False,
                            "truncated": x2 == width or y2 == height,
                        }
                    )
                    values = (
                        (x1 + x2) / (2 * width),
                        (y1 + y2) / (2 * height),
                        (x2 - x1) / width,
                        (y2 - y1) / height,
                    )
                    text = "0 " + " ".join(f"{value:.10f}" for value in values) + "\n"
                label.write_text(text, encoding="utf-8")
                rows.append(
                    {
                        "id": sample_id,
                        "split": split,
                        "group": path.name,
                        "timestamp_seconds": timestamp,
                        "frame_index": round(timestamp * fps),
                        "source_sha256": verified[str(path.resolve())],
                        "image_path": str(image.resolve()),
                        "image_sha256": sha256(image),
                        "width": width,
                        "height": height,
                        "annotations": annotations,
                    }
                )
        finally:
            capture.release()
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "label_method": "manual visual review of fixed-camera source frames",
        "names": {"0": "key"},
        "source_registry": str(REGISTRY.resolve()),
        "verified_development_sources": verified,
        "holdout_policy": (
            "This dataset preparation and training workflow did not decode, sample, train on, "
            "tune on, or predict the registered holdout."
        ),
        "samples": rows,
        "counts": {split: sum(row["split"] == split for row in rows) for split in ("train", "val")},
        "limits": [
            "Only two development videos contain the key, one per split.",
            "Validation key examples show a consistently edge-truncated placement.",
            "Labels cover the one supplied key instance and fixed camera scene only.",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for split in ("train", "val"):
        split_manifest = {
            **manifest,
            "samples": [row for row in rows if row["split"] == split],
            "counts": {split: manifest["counts"][split]},
        }
        (output / f"{split}-manifest.json").write_text(
            json.dumps(split_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (output / "dataset.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(output.resolve()),
                "train": "images/train",
                "val": "images/val",
                "names": {0: "key"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "harness/artifacts/keys-20260911-r01/dataset-r02",
    )
    args = parser.parse_args()
    result = build(args.output.resolve())
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
