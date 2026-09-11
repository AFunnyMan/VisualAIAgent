#!/usr/bin/env python3
"""Replay posture models with the production 10 fps timeline and seat-person gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from scripts.evaluate_behavior_models import load_manifest, score_expected_events
from scripts.training_source_policy import verify_development_sources
from visual_ai_agent.behavior import (
    BEHAVIOR_STALE_AFTER,
    POSTURE_CONFIRM_SECONDS,
    UNKNOWN_BRIDGE_SECONDS,
    BehaviorDetector,
    OnnxClassifier,
)
from visual_ai_agent.behavior_seat_gate import SeatPersonGate
from visual_ai_agent.behavior_timeline import BehaviorTimelineV2

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REPLAY_FPS = 10.0
SEAT_ROI = (0.2, 0.2, 0.95, 1.0)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise ValueError("Output must not exist")
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    resolved_sources = []
    annotations["videos"] = [
        source
        for source in annotations["videos"]
        if not args.source_prefix
        or any(source["sha256"].startswith(prefix) for prefix in args.source_prefix)
    ]
    if not annotations["videos"]:
        raise ValueError("No annotation source matches --source-prefix")
    for source in annotations["videos"]:
        path = Path(source["path"])
        path = path if path.is_absolute() else REPOSITORY_ROOT / path
        resolved_sources.append(path.resolve())
    verified_sources = verify_development_sources(resolved_sources, args.source_registry)

    records = {name: load_manifest(path, "posture") for name, path in args.model}
    posture_models = {name: OnnxClassifier(record) for name, record in records.items()}
    drinking_record = load_manifest(args.drinking_manifest, "drinking")
    person_probe = BehaviorDetector(
        next(iter(posture_models.values())),
        OnnxClassifier(drinking_record),
        args.person_model,
        seat_roi=SEAT_ROI,
        person_threads=2,
        auxiliary_mode="serial",
        expected_person_sha256=args.person_sha256,
        retain_diagnostic_frame=False,
    )
    summaries = []
    all_events = {name: [] for name in records}
    args.output.mkdir(parents=True, exist_ok=False)
    prediction_path = args.output / "predictions.jsonl"
    prediction_stream = prediction_path.open("x", encoding="utf-8")
    try:
        for source, path in zip(annotations["videos"], resolved_sources, strict=True):
            if sha256(path) != source["sha256"]:
                raise ValueError(f"Source SHA mismatch: {path}")
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                raise ValueError(f"Cannot decode {path}")
            source_fps = cap.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(source_fps) or source_fps < REPLAY_FPS:
                raise ValueError(f"Invalid source FPS: {path}")
            gates = {name: SeatPersonGate(roi=SEAT_ROI) for name in records}
            timelines = {
                name: BehaviorTimelineV2(
                    posture_confirm_seconds=POSTURE_CONFIRM_SECONDS,
                    unknown_bridge_seconds=UNKNOWN_BRIDGE_SECONDS,
                    max_gap=BEHAVIOR_STALE_AFTER,
                )
                for name in records
            }
            source_events = {name: [] for name in records}
            critical = {name: Counter() for name in records}
            frame_index = sample_index = 0
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    timestamp = frame_index / source_fps
                    if timestamp + 1e-8 >= sample_index / REPLAY_FPS:
                        sample_index += 1
                        person = person_probe._predict_person(frame)  # noqa: SLF001
                        prediction_row = {
                            "source_sha256": source["sha256"],
                            "frame_index": frame_index,
                            "timestamp": timestamp,
                            "person_candidates": [
                                {"confidence": item.confidence, "bbox": list(item.bbox)}
                                for item in person["candidates"]
                            ],
                            "models": {},
                        }
                        for name, classifier in posture_models.items():
                            raw = classifier.predict(frame)
                            posture = raw["label"]
                            support = gates[name].observe(
                                timestamp, person["candidates"], tuple(frame.shape[1::-1])
                            )
                            result = timelines[name].observe(
                                timestamp,
                                posture,
                                "not_drinking",
                                continuous_visible=support["person_track_supported"] is True,
                                exit_evidence=support["exit_evidence"] is True,
                            )
                            if source["sha256"].startswith("f9e748") and 84 <= timestamp < 96:
                                critical[name][posture] += 1
                            for event in result["events"]:
                                event_row = {**event, "source_sha256": source["sha256"]}
                                source_events[name].append(event_row)
                                all_events[name].append(event_row)
                            prediction_row["models"][name] = {
                                "raw_label": posture,
                                "confidence": raw["confidence"],
                                "margin": raw["margin"],
                                "person_track_supported": support["person_track_supported"],
                                "exit_evidence": support["exit_evidence"],
                                "person_gate_reason": support["reason"],
                                "confirmed_posture": result["posture"],
                                "events": result["events"],
                            }
                        prediction_stream.write(
                            json.dumps(prediction_row, ensure_ascii=False, sort_keys=True) + "\n"
                        )
                    frame_index += 1
            finally:
                cap.release()
            summaries.append(
                {
                    "source_sha256": source["sha256"],
                    "samples": sample_index,
                    "models": {
                        name: {
                            "events": values,
                            "scoring": score_expected_events(source["expected_events"], values),
                            "critical_23_05_02_84_96": dict(critical[name]),
                        }
                        for name, values in source_events.items()
                    },
                }
            )
    finally:
        prediction_stream.close()
        person_probe.close()

    result = {
        "status": "completed",
        "completed_at": datetime.now(UTC).isoformat(),
        "interpretation": (
            "10 fps development/training-source event replay; not independent acceptance."
        ),
        "parameters": {
            "fps": REPLAY_FPS,
            "posture_confirm_seconds": POSTURE_CONFIRM_SECONDS,
            "unknown_bridge_seconds": UNKNOWN_BRIDGE_SECONDS,
            "max_gap": BEHAVIOR_STALE_AFTER,
            "seat_roi": list(SEAT_ROI),
            "person_gate": "SeatPersonGate",
            "source_prefixes": args.source_prefix,
        },
        "annotations_sha256": sha256(args.annotations),
        "source_registry_sha256": sha256(args.source_registry),
        "predictions": {"path": str(prediction_path), "sha256": sha256(prediction_path)},
        "verified_development_sources": verified_sources,
        "person_model": {
            "path": str(args.person_model.resolve()),
            "sha256": sha256(args.person_model),
        },
        "models": {
            name: {
                "manifest": str(record["_path"]),
                "onnx_sha256": record["onnx_sha256"],
                "events": dict(Counter(event["kind"] for event in all_events[name])),
            }
            for name, record in records.items()
        },
        "sources": summaries,
    }
    (args.output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument(
        "--model", action="append", nargs=2, metavar=("NAME", "MANIFEST"), required=True
    )
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--person-model", type=Path, required=True)
    parser.add_argument("--person-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-prefix", action="append", default=[])
    args = parser.parse_args()
    args.model = [(name, Path(path)) for name, path in args.model]
    print(json.dumps(evaluate(args), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
