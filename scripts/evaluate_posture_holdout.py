"""Evaluate frozen posture candidates against a manually labelled, isolated video.

Offline frame coverage and action evidence only; not camera latency or long-term
false-alarm acceptance. This entry point never changes models or thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from scripts.evaluate_behavior_models import load_manifest, model_source_groups, sha256
from visual_ai_agent.behavior import (
    BEHAVIOR_STALE_AFTER,
    POSTURE_CONFIRM_SECONDS,
    UNKNOWN_BRIDGE_SECONDS,
    BehaviorDetector,
    OnnxClassifier,
)
from visual_ai_agent.behavior_seat_gate import SeatPersonGate
from visual_ai_agent.behavior_timeline import BehaviorTimelineV2


def score_actions(expected: list[dict], actual: list[dict]) -> dict:
    """Match each manually bracketed action at most once, without added tolerance."""
    unmatched = set(range(len(expected)))
    matches, extras = [], []
    for event in actual:
        candidates = [
            i for i in sorted(unmatched)
            if event["kind"] in expected[i]["kinds"]
            and expected[i]["start"] <= event["timestamp"] <= expected[i]["end"]
        ]
        if candidates:
            index = candidates[0]
            unmatched.remove(index)
            matches.append({"expected_index": index, "actual": event})
        else:
            extras.append(event)
    return {"matches": matches, "extra": extras,
            "missed": [expected[i] for i in sorted(unmatched)]}


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise ValueError("Output must not exist")
    if sha256(args.truth) != args.truth_sha256:
        raise ValueError("Manual truth changed after freeze")
    truth = json.loads(args.truth.read_text())
    video = Path(truth["video"]).resolve()
    digest = sha256(video)
    registry = json.loads(args.registry.read_text())
    if digest != truth["video_sha256"] or not any(
        row["sha256"] == digest and row["purpose"] == "holdout"
        for row in registry["sources"]
    ):
        raise ValueError("Video is not the registered holdout")
    records = {name: load_manifest(Path(path), "posture") for name, path in args.model}
    if len(records) != len(args.model):
        raise ValueError("Duplicate model names")
    for record in records.values():
        if digest in model_source_groups(record):
            raise ValueError("Holdout appears in model training or selection sources")
    models = {name: OnnxClassifier(record) for name, record in records.items()}
    drinking = load_manifest(args.drinking_manifest, "drinking")
    if digest in model_source_groups(drinking):
        raise ValueError("Holdout appears in auxiliary model sources")
    roi = (0.2, 0.2, 0.95, 1.0)
    detector = BehaviorDetector(
        next(iter(models.values())), OnnxClassifier(drinking), args.person_model,
        seat_roi=roi, person_threads=2, auxiliary_mode="serial",
        expected_person_sha256=args.person_sha256, retain_diagnostic_frame=False,
    )
    gates = {name: SeatPersonGate(roi=roi) for name in models}
    timelines = {name: BehaviorTimelineV2(
        posture_confirm_seconds=POSTURE_CONFIRM_SECONDS,
        unknown_bridge_seconds=UNKNOWN_BRIDGE_SECONDS, max_gap=BEHAVIOR_STALE_AFTER,
    ) for name in models}
    counts = {name: Counter() for name in models}
    critical = {name: Counter() for name in models}
    events = {name: [] for name in models}
    args.output.mkdir(parents=True)
    frozen = {
        "frozen_at": datetime.now(UTC).isoformat(), "truth_sha256": args.truth_sha256,
        "video_sha256": digest,
        "models": {name: {"manifest_sha256": sha256(record["_path"]),
                          "onnx_sha256": record["onnx_sha256"]}
                   for name, record in records.items()},
        "fps": 10, "max_gap": BEHAVIOR_STALE_AFTER,
        "posture_confirm_seconds": POSTURE_CONFIRM_SECONDS,
        "unknown_bridge_seconds": UNKNOWN_BRIDGE_SECONDS, "seat_roi": roi,
        "person_sha256": sha256(args.person_model),
    }
    (args.output / "frozen.json").write_text(json.dumps(frozen, indent=2) + "\n")
    cap = cv2.VideoCapture(str(video))
    index = samples = 0
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not cap.isOpened() or not math.isfinite(fps) or fps < 10:
            raise ValueError("Invalid video FPS")
        with (args.output / "observations.jsonl").open("w") as stream:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                timestamp = index / fps
                index += 1
                if timestamp + 1e-8 < samples / 10:
                    continue
                samples += 1
                person = detector._predict_person(frame)
                label = next((r["label"] for r in truth["intervals"]
                              if r["start"] <= timestamp < r["end"]), "unknown")
                row = {"timestamp": timestamp, "truth": label, "models": {}}
                for name, model in models.items():
                    prediction = model.predict(frame)
                    support = gates[name].observe(
                        timestamp, person["candidates"], tuple(frame.shape[1::-1]))
                    state = timelines[name].observe(
                        timestamp, prediction["label"], "not_drinking",
                        continuous_visible=support["person_track_supported"] is True,
                        exit_evidence=support["exit_evidence"] is True,
                    )
                    if label != "unknown":
                        counts[name][f"{label}->{prediction['label']}"] += 1
                    for interval in truth["critical_intervals"]:
                        if interval["start"] <= timestamp < interval["end"]:
                            critical[name][f"{interval['start']}:{prediction['label']}"] += 1
                    events[name].extend(state["events"])
                    row["models"][name] = {
                        "prediction": prediction, "support": support, "state": state}
                stream.write(json.dumps(row) + "\n")
    finally:
        cap.release()
        detector.close()
    summary = {
        **frozen, "completed_at": datetime.now(UTC).isoformat(), "samples": samples,
        "scope": "One independent fixed-scene video; not long-term or live acceptance",
        "models": {name: {**frozen["models"][name], "confusion": dict(counts[name]),
                          "critical": dict(critical[name]), "events": events[name],
                          "action_score": score_actions(truth["expected_events"], events[name])}
                   for name in models},
        "observations_sha256": sha256(args.output / "observations.jsonl"),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--truth-sha256", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--model", nargs=2, action="append", required=True)
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--person-model", type=Path, required=True)
    parser.add_argument("--person-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(evaluate(parser.parse_args()), indent=2))
