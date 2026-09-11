"""Compare drinking candidates on verified development data, including real person evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2

from scripts.evaluate_behavior_models import (
    ModelPair,
    accepted_label,
    load_manifest,
    model_source_groups,
    score_expected_events,
    sha256,
)
from scripts.prepare_behavior_data import label_at
from scripts.training_source_policy import verify_development_sources
from visual_ai_agent.behavior import BehaviorDetector, OnnxClassifier
from visual_ai_agent.behavior_seat_gate import SeatPersonGate
from visual_ai_agent.behavior_timeline import BehaviorTimelineV2


def evaluate(args):
    if args.output.exists():
        raise ValueError("Output must not exist")
    spec = json.loads(args.annotations.read_text())
    sources = [Path(row["path"]).resolve() for row in spec["videos"]]
    freeze_path = getattr(args, "holdout_freeze", None)
    if freeze_path is None:
        verified = verify_development_sources(sources, args.registry)
    else:
        frozen = json.loads(freeze_path.read_text())
        if frozen["annotations_sha256"] != sha256(args.annotations):
            raise ValueError("Frozen truth SHA mismatch")
        if frozen["posture_manifest_sha256"] != sha256(args.posture_manifest):
            raise ValueError("Frozen posture manifest mismatch")
        registry = json.loads(args.registry.read_text())
        holdouts = {r["sha256"] for r in registry["sources"] if r["purpose"] == "holdout"}
        verified = {str(path): sha256(path) for path in sources}
        if not set(verified.values()) <= holdouts:
            raise ValueError("Source is not registered holdout")
        for name, path in args.model:
            if frozen["models"][name] != sha256(Path(path)):
                raise ValueError("Frozen drinking manifest mismatch")
            if set(verified.values()) & set(
                model_source_groups(load_manifest(Path(path), "drinking"))
            ):
                raise ValueError("Holdout overlaps drinking training or selection sources")
    for row, path in zip(spec["videos"], sources, strict=True):
        if row["sha256"] != verified[str(path)]:
            raise ValueError("Annotation video SHA mismatch")
    records = {name: load_manifest(Path(path), "drinking") for name, path in args.model}
    import torch

    torch.set_num_threads(2)
    pairs = {name: ModelPair(record) for name, record in records.items()}
    posture = OnnxClassifier(load_manifest(args.posture_manifest, "posture"))
    person = BehaviorDetector(
        posture,
        OnnxClassifier(next(iter(records.values()))),
        args.person_model,
        auxiliary_mode="serial",
        person_threads=2,
        retain_diagnostic_frame=False,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    totals, videos = {}, []
    max_difference = 0.0
    try:
        for source, path in zip(spec["videos"], sources, strict=True):
            cap = cv2.VideoCapture(str(path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or fps < 10:
                raise ValueError("Invalid source video")
            timelines = {
                name: BehaviorTimelineV2(
                    posture_confirm_seconds=0.3,
                    drinking_confirm_seconds=0.5,
                    drinking_end_seconds=0.3,
                    unknown_bridge_seconds=1.0,
                    max_gap=0.25,
                )
                for name in pairs
            }
            gate = SeatPersonGate(roi=(0.2, 0.2, 0.95, 1.0))
            events = {name: [] for name in pairs}
            confusion = {name: Counter() for name in pairs}
            rows, index, next_sample = [], 0, 0.0
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
                    raw_posture = posture.predict(frame)
                    evidence = gate.observe(
                        timestamp,
                        person._predict_person(frame)["candidates"],
                        (frame.shape[1], frame.shape[0]),
                    )
                    truth = label_at(source["drinking"], timestamp)
                    sample = {
                        "timestamp": timestamp,
                        "truth": truth,
                        "posture": raw_posture["label"],
                        "person_evidence": evidence,
                        "models": {},
                    }
                    for name, pair in pairs.items():
                        probabilities, difference = pair.predict(frame)
                        max_difference = max(max_difference, difference)
                        label, confidence, margin = accepted_label(probabilities, pair.names)
                        state = timelines[name].observe(
                            timestamp,
                            raw_posture["label"],
                            label,
                            continuous_visible=evidence["person_track_supported"] is True,
                        )
                        events[name].extend(
                            e for e in state["events"] if e["kind"] == "suspected_drink"
                        )
                        if truth is not None:
                            confusion[name][f"{truth}->{label}"] += 1
                        sample["models"][name] = {
                            "label": label,
                            "confidence": confidence,
                            "margin": margin,
                            "state": state,
                        }
                    rows.append(sample)
            finally:
                cap.release()
            detail = args.output / f"{source['sha256'][:12]}.json"
            detail.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            result = {
                "source_sha256": source["sha256"],
                "samples": len(rows),
                "unscored_samples": sum(row["truth"] is None for row in rows),
                "models": {},
            }
            for name in pairs:
                result["models"][name] = {
                    "confusion": dict(confusion[name]),
                    "events": events[name],
                    "event_score": score_expected_events(
                        source["expected_drinking_events"], events[name], tolerance=0
                    ),
                }
            videos.append(result)
        totals = {
            "scope": (
                "held-out drinking model check; posture pipeline has used this clip for diagnosis, "
                "so this is not independent whole-product acceptance"
                if freeze_path
                else "development regression, not independent acceptance"
            ),
            "freeze_sha256": sha256(freeze_path) if freeze_path else None,
            "models": {name: r["onnx_sha256"] for name, r in records.items()},
            "annotations_sha256": sha256(args.annotations),
            "pt_onnx_max_abs": max_difference,
            "videos": videos,
        }
        (args.output / "summary.json").write_text(json.dumps(totals, ensure_ascii=False, indent=2))
    finally:
        person.close()
    print(json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--holdout-freeze", type=Path)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("harness/evaluations/recognition-20260911-sources.json"),
    )
    parser.add_argument("--model", nargs=2, action="append", required=True)
    parser.add_argument("--posture-manifest", type=Path, required=True)
    parser.add_argument("--person-model", type=Path, default=Path("models/yolo26n-e2e.onnx"))
    parser.add_argument("--output", type=Path, required=True)
    evaluate(parser.parse_args())
