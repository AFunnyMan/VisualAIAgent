"""Score fixed-threshold detections against original instance boxes (not COCO mAP)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CATEGORIES = ("bottle", "cup", "cell phone")


def overlap(a, b, crowd=False):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    denominator = area_a if crowd else area_a + area_b - intersection
    return intersection / denominator if denominator else 0.0


def score_image(annotations, detections, threshold=0.35, iou=0.5, categories=CATEGORIES):
    """Match by class/descending confidence; duplicate boxes count as false positives.

    Non-crowd matches take precedence; unmatched predictions covered by a same-class
    crowd region are ignored by intersection over prediction area, not counted TP.
    """
    result = {}
    for category in categories:
        targets = [a for a in annotations if a["category"] == category and not a["iscrowd"]]
        crowds = [a for a in annotations if a["category"] == category and a["iscrowd"]]
        predictions = sorted(
            [d for d in detections if d["category"] == category and d["confidence"] >= threshold],
            key=lambda d: -d["confidence"],
        )
        matched = set()
        tp = fp = ignored = 0
        outcomes = []
        for prediction in predictions:
            candidates = [
                (overlap(prediction["bbox"], a["bbox"]), index)
                for index, a in enumerate(targets)
                if index not in matched
            ]
            best, index = max(candidates, default=(0.0, -1))
            if index >= 0 and best >= iou:
                matched.add(index)
                tp += 1
                outcome = "tp"
            elif any(overlap(prediction["bbox"], a["bbox"], True) >= iou for a in crowds):
                ignored += 1
                outcome = "ignored_crowd"
            else:
                fp += 1
                outcome = "fp"
            outcomes.append({"detection": prediction, "outcome": outcome})
        result[category] = {
            "tp": tp,
            "fp": fp,
            "fn": len(targets) - tp,
            "ignored": ignored,
            "outcomes": outcomes,
        }
    return result


def score_dataset(manifest, predictions, threshold=0.35, categories=CATEGORIES):
    samples = {str(s["id"]): s for s in manifest["samples"]}
    rows = {str(s["id"]): s for s in predictions}
    if (
        len(samples) != len(manifest["samples"])
        or len(rows) != len(predictions)
        or set(rows) != set(samples)
    ):
        raise ValueError("Predictions must cover exactly the unique manifest image IDs")
    images = []
    counts = {c: dict.fromkeys(("tp", "fp", "fn", "ignored"), 0) for c in categories}
    for sample_id, sample in samples.items():
        metrics = score_image(
            sample["annotations"], rows[sample_id]["detections"], threshold, categories=categories
        )
        images.append({"id": sample_id, "metrics": metrics})
        for category in categories:
            for key in counts[category]:
                counts[category][key] += metrics[category][key]
    for values in counts.values():
        values["precision"] = (
            values["tp"] / (values["tp"] + values["fp"]) if values["tp"] + values["fp"] else None
        )
        values["recall"] = (
            values["tp"] / (values["tp"] + values["fn"]) if values["tp"] + values["fn"] else None
        )
    return {"confidence": threshold, "iou": 0.5, "categories": counts, "images": images}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument(
        "--profile", help="Profile in an archived comparison; clip:<id> for cascade"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.35,
        help="Scoring floor; use 0.05 for already class-filtered experimental predictions",
    )
    args = parser.parse_args()
    if not 0 <= args.confidence <= 1:
        parser.error("--confidence must be between 0 and 1")
    predictions = json.loads(args.predictions.read_text())
    if args.profile:
        predictions = (
            predictions["clip"]["cascades"][args.profile.removeprefix("clip:")]
            if args.profile.startswith("clip:")
            else predictions["models"][args.profile]
        )
    result = score_dataset(
        json.loads(args.manifest.read_text()), predictions["images"], args.confidence
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["categories"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
