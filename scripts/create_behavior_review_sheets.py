#!/usr/bin/env python3
"""Create timestamped contact sheets for manual review of local behavior videos."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import numpy as np


def create_sheets(
    video: Path,
    output: Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    step: float = 1.0,
    columns: int = 5,
    rows: int = 4,
) -> list[Path]:
    if start < 0 or not math.isfinite(start) or step <= 0 or not math.isfinite(step):
        raise ValueError("start must be non-negative and step must be positive")
    if columns <= 0 or rows <= 0:
        raise ValueError("columns and rows must be positive")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
        cap.release()
        raise ValueError(f"Invalid video metadata: {video}")
    duration = frame_count / fps
    stop = duration if end is None else min(end, duration)
    if not start < stop:
        cap.release()
        raise ValueError("Review range is empty")

    output.mkdir(parents=True, exist_ok=True)
    frames: list[np.ndarray] = []
    timestamps: list[float] = []
    timestamp = start
    try:
        while timestamp < stop:
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok:
                # Container duration can round slightly beyond the final decodable frame.
                if timestamp + step >= duration:
                    break
                raise RuntimeError(f"Failed to decode {video} at {timestamp:.3f}s")
            frames.append(frame)
            timestamps.append(timestamp)
            timestamp += step
    finally:
        cap.release()

    page_size = columns * rows
    results: list[Path] = []
    for page_index in range(0, len(frames), page_size):
        page_frames = frames[page_index : page_index + page_size]
        page_timestamps = timestamps[page_index : page_index + page_size]
        thumbs = []
        for frame, sampled_at in zip(page_frames, page_timestamps, strict=True):
            thumb = cv2.resize(frame, (384, 216), interpolation=cv2.INTER_AREA)
            cv2.putText(
                thumb,
                f"{sampled_at:.2f}s",
                (8, 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.72,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            thumbs.append(thumb)
        blank = np.zeros_like(thumbs[0])
        thumbs.extend([blank] * (page_size - len(thumbs)))
        sheet = np.vstack(
            [np.hstack(thumbs[index : index + columns]) for index in range(0, page_size, columns)]
        )
        target = output / f"{video.stem}-{page_index // page_size + 1:02d}.jpg"
        if not cv2.imwrite(str(target), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise RuntimeError(f"Failed to write {target}")
        results.append(target)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float)
    parser.add_argument("--step", type=float, default=1.0)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--rows", type=int, default=4)
    args = parser.parse_args()
    for path in create_sheets(
        args.video,
        args.output,
        start=args.start,
        end=args.end,
        step=args.step,
        columns=args.columns,
        rows=args.rows,
    ):
        print(path)


if __name__ == "__main__":
    main()
