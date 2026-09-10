"""Collect verifiable local predictions for development-only temporal comparisons."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from scripts.behavior_events import BehaviorTimeline
from scripts.evaluate_behavior_models import (
    ModelPair,
    accepted_label,
    load_manifest,
    model_source_groups,
    score_expected_events,
    sha256,
)
from scripts.prepare_behavior_data import label_at


def collect(args) -> None:
    import torch

    if not math.isfinite(args.fps) or not 0 < args.fps <= 30:
        raise ValueError("fps must be in (0, 30]")
    spec = json.loads(args.annotations.read_text())
    records = {
        "posture": load_manifest(args.posture_manifest, "posture"),
        "drinking": load_manifest(args.drinking_manifest, "drinking"),
    }
    provenance = {task: model_source_groups(record) for task, record in records.items()}
    torch.set_num_threads(2)
    models = {task: ModelPair(record) for task, record in records.items()}
    args.output.mkdir(parents=True, exist_ok=False)
    maximum_difference = 0.0
    counts = Counter()
    seen = set()
    with (args.output / "predictions.jsonl").open("w") as stream:
        for source in spec["videos"]:
            path = Path(source["path"])
            if sha256(path) != source["sha256"] or source["sha256"] in seen:
                raise ValueError("Source mismatch or duplicate video")
            seen.add(source["sha256"])
            cap = cv2.VideoCapture(str(path))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                if not cap.isOpened() or not math.isfinite(fps) or fps < args.fps:
                    raise ValueError("Unreadable video or insufficient source fps")
                index = sample = 0
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    timestamp = index / fps
                    if timestamp + 1e-8 >= sample / args.fps:
                        sample += 1
                        row = {
                            "source_sha256": source["sha256"],
                            "frame_index": index,
                            "timestamp": timestamp,
                        }
                        for task, model in models.items():
                            probabilities, difference = model.predict(frame)
                            maximum_difference = max(maximum_difference, difference)
                            label, confidence, margin = accepted_label(probabilities, model.names)
                            row[task] = {
                                "label": label,
                                "confidence": confidence,
                                "margin": margin,
                                "probabilities": {
                                    model.names[i]: float(p) for i, p in enumerate(probabilities)
                                },
                            }
                        stream.write(json.dumps(row) + "\n")
                        counts[source["sha256"]] += 1
                    index += 1
                if abs(index / fps - source["duration_seconds"]) > max(0.1, 2 / fps):
                    raise ValueError("Decoded source duration mismatch")
            finally:
                cap.release()
    result = {
        "status": "completed",
        "completed_at": datetime.now(UTC).isoformat(),
        "purpose": "development comparison; source reuse, not independent final test",
        "independent_accuracy": False,
        "annotations": str(args.annotations.resolve()),
        "annotations_sha256": sha256(args.annotations),
        "predictions_sha256": sha256(args.output / "predictions.jsonl"),
        "collector_sha256": sha256(Path(__file__)),
        "fps": args.fps,
        "counts": dict(counts),
        "pt_onnx_max_abs": maximum_difference,
        "models": {
            task: {
                "manifest": str(record["_path"]),
                "onnx_sha256": record["onnx_sha256"],
                "preprocessing": record["preprocessing"],
                "actual_source_groups": provenance[task],
            }
            for task, record in records.items()
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"counts": dict(counts), "pt_onnx_max_abs": maximum_difference}))


def sample_rows(rows: list[dict], fps: float, source_fps: float) -> list[dict]:
    if not math.isfinite(fps) or fps <= 0 or fps > source_fps:
        raise ValueError("Requested replay fps exceeds collected evidence")
    selected = []
    index = 0
    for row in rows:
        if row["timestamp"] + 1e-8 >= index / fps:
            selected.append(row)
            index += 1
    return selected


def load_person_evidence(path: Path, rows: list[dict]) -> dict:
    metadata = json.loads(path.with_name("metadata.json").read_text())
    if metadata["predictions_file"] != path.name or metadata["predictions_sha256"] != sha256(path):
        raise ValueError("Person evidence integrity mismatch")
    person_rows = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        key = (row["source_sha256"], row["frame_index"])
        if key in person_rows:
            raise ValueError("Duplicate person evidence key")
        if type(row["person_track_supported"]) is not bool:
            raise ValueError("Person continuity must be an explicit boolean")
        person_rows[key] = row
    keys = {(r["source_sha256"], r["frame_index"]) for r in rows}
    if len(keys) != len(rows) or keys != set(person_rows):
        raise ValueError("Person evidence must match every collected frame exactly")
    for row in rows:
        evidence = person_rows[(row["source_sha256"], row["frame_index"])]
        timestamps = (evidence["timestamp_s"], row["timestamp"])
        if (
            not all(math.isfinite(t) for t in timestamps)
            or abs(timestamps[0] - timestamps[1]) > 1e-8
        ):
            raise ValueError("Person evidence timestamp mismatch")
    return person_rows


def score(args) -> None:
    from scripts.behavior_timeline_v2 import BehaviorTimelineV2

    meta = json.loads((args.cache / "manifest.json").read_text())
    predictions = args.cache / "predictions.jsonl"
    if meta["status"] != "completed" or sha256(predictions) != meta["predictions_sha256"]:
        raise ValueError("Incomplete or modified cache")
    annotations = Path(meta["annotations"])
    if sha256(annotations) != meta["annotations_sha256"]:
        raise ValueError("Annotation mismatch")
    spec = json.loads(annotations.read_text())
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    if args.annotations is not None:
        if not args.annotation_revision_note:
            raise ValueError("Annotation revision requires an explicit rationale")
        revised = json.loads(args.annotations.read_text())
        source_identity = lambda value: sorted(  # noqa: E731
            (s["sha256"], s["path"], s["duration_seconds"]) for s in value["videos"]
        )
        if source_identity(spec) != source_identity(revised):
            raise ValueError("Revised labels must describe exactly the same source videos")
        spec = revised
    person_rows = None
    if args.person_cache is not None:
        person_rows = load_person_evidence(args.person_cache, rows)
    profiles = {"v1-2fps": (2, "v1"), "v1-10fps": (10, "v1")}
    profiles.update({f"v2-{fps}fps-conservative": (fps, "v2") for fps in (2, 5, 10)})
    if person_rows is not None:
        profiles.update({f"v2-{fps}fps-person-proxy": (fps, "proxy") for fps in (5, 10)})
        profiles["v2-10fps-person-proxy-posture03"] = (10, "proxy-fast")
        profiles["v2-10fps-person-proxy-posture03-drink05"] = (10, "proxy-drink05")
        profiles["v2-10fps-seat-association"] = (10, "seat")
    results = {}
    for name, (fps, version) in profiles.items():
        sources = []
        for source in spec["videos"]:
            selected = sample_rows(
                [r for r in rows if r["source_sha256"] == source["sha256"]], fps, meta["fps"]
            )
            timeline = (
                BehaviorTimeline()
                if version == "v1"
                else BehaviorTimelineV2(
                    posture_confirm_seconds=0.3, drinking_confirm_seconds=0.5, max_gap=0.25
                )
                if version in {"proxy-drink05", "seat"}
                else BehaviorTimelineV2(posture_confirm_seconds=0.3, max_gap=0.25)
                if version == "proxy-fast"
                else BehaviorTimelineV2()
            )
            events = []
            if version == "seat":
                from scripts.behavior_seat_gate import SeatPersonGate
                from scripts.behavior_visibility import PersonCandidate

                seat_gate = SeatPersonGate()
            classifications = {"posture": Counter(), "drinking": Counter()}
            unknown_count = Counter()
            for row in selected:
                extra = {}
                if version == "seat":
                    evidence = person_rows[(row["source_sha256"], row["frame_index"])]
                    candidates = [
                        PersonCandidate(c["confidence"], tuple(c["bbox"]))
                        for c in evidence["person_candidates"]
                    ]
                    support = seat_gate.observe(
                        row["timestamp"], candidates, tuple(evidence["frame_size"])
                    )
                    extra["continuous_visible"] = support["person_track_supported"]
                if version in {"proxy", "proxy-fast", "proxy-drink05"}:
                    evidence = person_rows[(row["source_sha256"], row["frame_index"])]
                    extra["continuous_visible"] = evidence["person_track_supported"] is True
                result = timeline.observe(
                    row["timestamp"], row["posture"]["label"], row["drinking"]["label"], **extra
                )
                events.extend(result["events"])
                for task in classifications:
                    truth = label_at(source[task], row["timestamp"])
                    if truth is not None:
                        classifications[task][f"{truth}/{row[task]['label']}"] += 1
                    unknown_count[task] += result[task] == "unknown"
            sources.append(
                {
                    "source_sha256": source["sha256"],
                    "trained_on": {
                        task: model["actual_source_groups"].get(source["sha256"]) == "train"
                        for task, model in meta["models"].items()
                    },
                    "events": events,
                    "scoring": score_expected_events(source["expected_events"], events)
                    if "expected_events" in source
                    else {"annotated": False},
                    "scoring_zero_tolerance": score_expected_events(
                        source["expected_events"], events, tolerance=0
                    )
                    if "expected_events" in source
                    else {"annotated": False},
                    "classification_counts": {t: dict(c) for t, c in classifications.items()},
                    "unknown_samples": dict(unknown_count),
                    "sample_count": len(selected),
                }
            )
        results[name] = sources
    output = {
        "purpose": "development ablation; not independent accuracy",
        "independent_accuracy": False,
        "cache_manifest_sha256": sha256(args.cache / "manifest.json"),
        "v2_code_sha256": sha256(Path(__file__).with_name("behavior_timeline_v2.py")),
        "seat_association_code_sha256": sha256(Path(__file__).with_name("behavior_seat_gate.py"))
        if person_rows is not None
        else None,
        "original_annotations_sha256": meta["annotations_sha256"],
        "scored_annotations_sha256": sha256(args.annotations or annotations),
        "annotation_revision_note": args.annotation_revision_note,
        "person_cache_sha256": sha256(args.person_cache) if args.person_cache else None,
        "person_proxy_limitation": (
            "Tracked person is auxiliary evidence, not proof of unobstructed action; "
            "proxy profiles are experimental and not production acceptance"
        ),
        "profiles": results,
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(output, indent=2) + "\n")
    print(json.dumps({k: [s["scoring"] for s in v] for k, v in results.items()}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gather = commands.add_parser("collect")
    for option in ("posture-manifest", "drinking-manifest", "annotations", "output"):
        gather.add_argument(f"--{option}", type=Path, required=True)
    gather.add_argument("--fps", type=float, default=10)
    compare = commands.add_parser("score")
    compare.add_argument("--cache", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--annotations", type=Path)
    compare.add_argument("--annotation-revision-note")
    compare.add_argument("--person-cache", type=Path)
    args = parser.parse_args()
    (collect if args.command == "collect" else score)(args)


if __name__ == "__main__":
    main()
