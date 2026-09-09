"""Build local behavior classification data from explicitly reviewed time intervals.

No label is inferred outside an annotated interval. A source video can belong to
only one split; train-only experiments must not be described as validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import cv2

LABELS = {
    "posture": {"seated", "standing", "empty", "unknown"},
    "drinking": {"drinking", "not_drinking", "unknown"},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_intervals(intervals: list[dict], task: str, duration: float) -> None:
    previous_end = 0.0
    for item in intervals:
        start, end = item["start"], item["end"]
        if not all(math.isfinite(x) for x in (start, end)):
            raise ValueError("Nonfinite interval")
        if start < previous_end or not 0 <= start < end <= duration:
            raise ValueError("Overlapping, unordered or out-of-bounds interval")
        if item["label"] not in LABELS[task]:
            raise ValueError("Unrecognized behavior label")
        previous_end = end


def label_at(intervals: list[dict], timestamp: float) -> str | None:
    for item in intervals:
        if item["start"] <= timestamp < item["end"]:
            return None if item["label"] == "unknown" else item["label"]
    return None


def build(annotations: Path, output: Path, sample_fps: float = 2.0) -> dict:
    if not math.isfinite(sample_fps) or not 0 < sample_fps <= 10:
        raise ValueError("sample_fps must be in (0, 10]")
    if output.exists():
        raise ValueError("Output must not exist")
    spec = json.loads(annotations.read_text())
    sources: list[dict] = spec["videos"]
    seen_sources: set[str] = set()
    # Verify all sources and annotation bounds before creating any output.
    for source in sources:
        path = Path(source["path"]).resolve()
        if not path.is_file():
            raise ValueError("Local source video missing")
        digest = sha256(path)
        if digest != source["sha256"] or digest in seen_sources:
            raise ValueError("Source SHA mismatch or repeated source group")
        seen_sources.add(digest)
        if source.get("split", "train") not in {"train", "val", "test"}:
            raise ValueError("Invalid split")
        duration = source["duration_seconds"]
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid duration")
        for task in LABELS:
            validate_intervals(source[task], task, duration)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    seen_images: dict[str, str] = {}
    for source in sources:
        path = Path(source["path"]).resolve()
        split = source.get("split", "train")
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
                raise ValueError("Video cannot be decoded")
            frame_index, sample_index = 0, 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                timestamp = frame_index / fps
                if timestamp + 1e-8 >= sample_index / sample_fps:
                    sample_index += 1
                    for task in LABELS:
                        label = label_at(source[task], timestamp)
                        if label is None:
                            continue
                        relative = Path(task) / split / label
                        relative /= f"{source['sha256'][:16]}-{frame_index:06d}.png"
                        target = output / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not cv2.imwrite(str(target), frame):
                            raise RuntimeError("Failed to write frame")
                        digest = sha256(target)
                        if digest in seen_images and seen_images[digest] != split:
                            raise ValueError("Identical frame crosses split")
                        seen_images[digest] = split
                        records.append(
                            dict(
                                path=str(relative),
                                task=task,
                                label=label,
                                split=split,
                                source_sha256=source["sha256"],
                                frame_index=frame_index,
                                timestamp=timestamp,
                                sha256=digest,
                            )
                        )
                frame_index += 1
            if abs(frame_index / fps - source["duration_seconds"]) > max(0.1, 2 / fps):
                raise ValueError("Decoded duration differs from reviewed source")
        finally:
            cap.release()
    result = dict(
        schema_version=1,
        annotations_sha256=sha256(annotations),
        sample_fps=sample_fps,
        counts=dict(Counter(f"{r['task']}/{r['split']}/{r['label']}" for r in records)),
        samples=records,
        source_groups={s["sha256"]: s.get("split", "train") for s in sources},
    )
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    args = parser.parse_args()
    print(json.dumps(build(args.annotations, args.output, args.sample_fps)["counts"]))
