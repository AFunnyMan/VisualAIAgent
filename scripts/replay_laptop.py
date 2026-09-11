"""Replay an explicit local video through an experimental lid model and timeline.

This is offline model evaluation, not live camera evidence or a product reminder.
Never use independent acceptance video results to select model parameters.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import cv2

from visual_ai_agent.behavior import OnnxClassifier, load_behavior_manifest, sha256
from visual_ai_agent.laptop_state import LaptopTimeline


def replay(manifest: Path, video: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Refusing to overwrite evaluation output")
    model = OnnxClassifier(load_behavior_manifest(manifest, "laptop"))
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or not math.isfinite(fps) or fps < 10:
        cap.release()
        raise ValueError("Video cannot be read")
    timeline = LaptopTimeline()
    rows, events = [], []
    index, next_sample = 0, 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            timestamp = index / fps
            index += 1
            if timestamp + 1e-9 < next_sample:
                continue
            next_sample = timestamp + 0.1
            prediction = model.predict(frame)
            state = timeline.observe(timestamp, prediction["label"])
            row = {
                "timestamp": timestamp,
                "raw": prediction,
                "state": state.state,
                "reason": state.reason,
                "event": state.event,
            }
            rows.append(row)
            if state.event:
                events.append({"kind": state.event, "timestamp": timestamp})
    finally:
        cap.release()
    result = {
        "scope": "offline experimental replay; no quality acceptance implied",
        "video_sha256": sha256(video),
        "manifest_sha256": sha256(manifest),
        "frames_decoded": index,
        "samples": len(rows),
        "raw_counts": dict(Counter(row["raw"]["label"] for row in rows)),
        "state_counts": dict(Counter(row["state"] for row in rows)),
        "events": events,
        "observations": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return {key: value for key, value in result.items() if key != "observations"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.manifest, args.video, args.output), ensure_ascii=False))
