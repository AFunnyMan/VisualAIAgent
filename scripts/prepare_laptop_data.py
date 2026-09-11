#!/usr/bin/env python3
"""Build an isolated opened/closed laptop dataset from reviewed intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import cv2

from scripts.training_source_policy import verify_development_sources

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
LABELS = {"open", "closed", "unknown"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_intervals(intervals: list[dict], duration: float) -> None:
    previous_end = 0.0
    for item in intervals:
        start, end = item["start"], item["end"]
        if not all(math.isfinite(value) for value in (start, end)):
            raise ValueError("Nonfinite interval")
        if start < previous_end or not 0 <= start < end <= duration:
            raise ValueError("Overlapping, unordered or out-of-bounds interval")
        if item["label"] not in LABELS:
            raise ValueError("Unrecognized laptop label")
        previous_end = end


def label_at(intervals: list[dict], timestamp: float) -> str | None:
    for item in intervals:
        if item["start"] <= timestamp < item["end"]:
            return None if item["label"] == "unknown" else item["label"]
    return None


def build(annotations: Path, registry: Path, output: Path, sample_fps: float = 2.0) -> dict:
    if not math.isfinite(sample_fps) or not 0 < sample_fps <= 10:
        raise ValueError("sample_fps must be in (0, 10]")
    if output.exists():
        raise ValueError("Output must not exist")
    spec = json.loads(annotations.read_text(encoding="utf-8"))
    derivation = None
    if "base_annotations" in spec:
        base_path = (annotations.parent / spec["base_annotations"]).resolve()
        if sha256(base_path) != spec.get("base_annotations_sha256"):
            raise ValueError("Base annotation SHA mismatch")
        if spec.get("force_split") != "train":
            raise ValueError("Derived annotation may only freeze all development sources as train")
        base = json.loads(base_path.read_text(encoding="utf-8"))
        spec = {**base, "videos": [{**row, "split": "train"} for row in base["videos"]]}
        derivation = {
            "base_annotations": str(base_path),
            "base_annotations_sha256": sha256(base_path),
            "force_split": "train",
        }
    sources = spec["videos"]
    paths = [(REPOSITORY_ROOT / row["path"]).resolve() for row in sources]
    verified = verify_development_sources(paths, registry)
    seen_splits: dict[str, str] = {}
    for source, path in zip(sources, paths, strict=True):
        digest = verified[str(path)]
        if source["sha256"] != digest:
            raise ValueError("Annotation SHA does not match frozen source registry")
        split = source["split"]
        if split not in {"train", "val"}:
            raise ValueError("Only train and development val splits are allowed")
        if digest in seen_splits and seen_splits[digest] != split:
            raise ValueError("Source video group crosses splits")
        seen_splits[digest] = split
        duration = source["duration_seconds"]
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid duration")
        validate_intervals(source["intervals"], duration)

    output.mkdir(parents=True, exist_ok=False)
    records: list[dict] = []
    seen_images: dict[str, str] = {}
    for source, path in zip(sources, paths, strict=True):
        split = source["split"]
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
                raise ValueError("Video cannot be decoded")
            frame_index = sample_index = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                timestamp = frame_index / fps
                if timestamp + 1e-8 >= sample_index / sample_fps:
                    sample_index += 1
                    label = label_at(source["intervals"], timestamp)
                    if label is not None:
                        relative = Path("laptop") / split / label
                        relative /= f"{source['sha256'][:16]}-{frame_index:06d}.png"
                        target = output / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not cv2.imwrite(str(target), frame):
                            raise RuntimeError("Failed to write frame")
                        digest = sha256(target)
                        if digest in seen_images and seen_images[digest] != split:
                            raise ValueError("Identical frame crosses splits")
                        seen_images[digest] = split
                        records.append(
                            {
                                "path": str(relative),
                                "task": "laptop",
                                "label": label,
                                "split": split,
                                "source_sha256": source["sha256"],
                                "frame_index": frame_index,
                                "timestamp": timestamp,
                                "sha256": digest,
                            }
                        )
                frame_index += 1
            if abs(frame_index / fps - source["duration_seconds"]) > max(0.1, 2 / fps):
                raise ValueError("Decoded duration differs from reviewed source")
        finally:
            cap.release()
    result = {
        "schema_version": 1,
        "annotations_sha256": sha256(annotations),
        "annotation_derivation": derivation,
        "source_registry": str(registry.resolve()),
        "verified_development_sources": verified,
        "sample_fps": sample_fps,
        "counts": dict(Counter(f"{r['split']}/{r['label']}" for r in records)),
        "samples": records,
        "source_groups": seen_splits,
        "semantics": {
            "open": (
                "The observed laptop lid is visibly open, "
                "including a partly open lid or a dark screen."
            ),
            "closed": "The observed laptop lid is fully closed and visibly present.",
            "unknown": "Rejected: occluded, absent, or visually unclear.",
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    args = parser.parse_args()
    result = build(args.annotations, args.registry, args.output, args.sample_fps)
    print(json.dumps(result["counts"]))
