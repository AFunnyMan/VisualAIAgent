"""Replay a bounded low-score association hypothesis; not ByteTrack or production logic."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.score_detections import overlap  # noqa: E402
from visual_ai_agent.events import EventStateMachine  # noqa: E402
from visual_ai_agent.models import Detection, SceneObservation  # noqa: E402


def associate(previous, candidates, t):
    used = set()
    current = []
    for d in sorted(candidates, key=lambda d: -d["confidence"]):
        if d["confidence"] < 0.1:
            continue
        matches = [
            (overlap(d["bbox"], p["detection"]["bbox"]), index)
            for index, p in enumerate(previous)
            if index not in used
            and p["detection"]["category"] == d["category"]
            and 0 < t - p["t"] <= 2
            and t - p["high_at"] <= 2
        ]
        best, index = max(matches, default=(0.0, -1))
        matched = index >= 0 and best >= 0.3
        if d["confidence"] >= 0.35 or matched:
            if matched:
                used.add(index)
            current.append(
                {
                    "detection": d,
                    "high_at": t if d["confidence"] >= 0.35 else previous[index]["high_at"],
                    "t": t,
                }
            )
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for path in sorted(args.input.glob("*/*.json")):
        data = json.loads(path.read_text())
        if "samples" not in data:
            continue
        machine = EventStateMachine(max_gap_seconds=2.5)
        origin = datetime(2026, 9, 7, tzinfo=UTC)
        previous, events, admitted = [], [], []
        for row in data["samples"]:
            t = row["t"]
            # The diagnostic proposal list is capped per class; retain all baseline
            # positives so a crowded scene cannot lose high-score detections here.
            candidates = row["detections"] + [
                d for d in row["candidates"] if d["confidence"] < 0.35
            ]
            previous = associate(previous, candidates, t)
            detections = [Detection.model_validate(p["detection"]) for p in previous]
            admitted.extend(
                {"t": t, "detection": p["detection"]}
                for p in previous
                if p["detection"]["confidence"] < 0.35
            )
            observation = SceneObservation(
                observed_at=origin + timedelta(seconds=t),
                monotonic_at=t,
                status="running",
                fresh=True,
                source="replay",
                detections=detections,
            )
            events.extend(
                {"t": t, "category": e.category, "kind": e.kind}
                for e in machine.process(observation)
            )
        results.append(
            {
                "profile": path.parent.name,
                "video": data["id"],
                "baseline_events": data["events"],
                "experimental_events": events,
                "admitted_low_candidates": admitted,
            }
        )
    args.output.write_text(
        json.dumps(
            {
                "parameters": {"high": 0.35, "low": 0.1, "iou": 0.3, "max_high_age": 2},
                "warning": "Diagnostic, no full frame GT; events are not an accuracy metric",
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
