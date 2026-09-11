"""Independent evaluation of one frozen laptop classifier and the bounded timeline."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from scripts.evaluate_behavior_models import model_source_groups
from scripts.evaluate_posture_holdout import score_actions
from visual_ai_agent.behavior import OnnxClassifier, load_behavior_manifest, sha256
from visual_ai_agent.laptop_state import LaptopTimeline


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise ValueError("Output must not exist")
    if sha256(args.truth) != args.truth_sha256 or sha256(args.model) != args.model_sha256:
        raise ValueError("Frozen truth or model manifest changed")
    truth = json.loads(args.truth.read_text())
    video = Path(truth["video"]).resolve()
    digest = sha256(video)
    registry = json.loads(args.registry.read_text())
    if digest != truth["video_sha256"] or not any(
        r["sha256"] == digest and r["purpose"] == "holdout" for r in registry["sources"]
    ):
        raise ValueError("Video is not the registered holdout")
    record = load_behavior_manifest(args.model, "laptop")
    if digest in model_source_groups(record):
        raise ValueError("Holdout appears in training or selection sources")
    model = OnnxClassifier(record)
    timeline = LaptopTimeline()
    args.output.mkdir(parents=True)
    frozen = {
        "frozen_at": datetime.now(UTC).isoformat(), "video_sha256": digest,
        "truth_sha256": args.truth_sha256, "manifest_sha256": args.model_sha256,
        "onnx_sha256": record["onnx_sha256"], "fps": 10,
        "parameters": {"confirm_seconds": timeline.confirm_seconds,
                       "max_gap": timeline.max_gap,
                       "max_transition_seconds": timeline.max_transition_seconds},
        "invocation": {k: str(v) for k, v in vars(args).items()},
    }
    (args.output / "frozen.json").write_text(json.dumps(frozen, indent=2) + "\n")
    counts, raw_counts, events = Counter(), Counter(), []
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
                prediction = model.predict(frame)
                state = timeline.observe(timestamp, prediction["label"])
                label = next((r["label"] for r in truth["intervals"]
                              if r["start"] <= timestamp < r["end"]), "unknown")
                if label != "unknown":
                    counts[f"{label}->{prediction['label']}"] += 1
                raw_counts[prediction["label"]] += 1
                if state.event:
                    events.append({"kind": state.event, "timestamp": timestamp})
                stream.write(json.dumps({"timestamp": timestamp, "truth": label,
                                         "prediction": prediction, "state": state.state,
                                         "reason": state.reason, "event": state.event}) + "\n")
    finally:
        cap.release()
    result = {
        **frozen, "completed_at": datetime.now(UTC).isoformat(), "samples": samples,
        "confusion": dict(counts), "raw_counts": dict(raw_counts), "events": events,
        "action_score": score_actions(truth["expected_events"], events),
        "observations_sha256": sha256(args.output / "observations.jsonl"),
        "scope": "One isolated fixed-scene video; not live or long-term acceptance",
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--truth-sha256", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(evaluate(parser.parse_args()), indent=2))
