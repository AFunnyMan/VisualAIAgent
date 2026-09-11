"""Experimental person-track support gate and offline cache collector.

The gate is coarse auxiliary evidence. ``person_track_supported`` never means
that a complete action, body, or seat contact is visible without occlusion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from visual_ai_agent.vision import inverse_letterbox, letterbox  # noqa: E402


@dataclass(frozen=True)
class PersonCandidate:
    confidence: float
    bbox: tuple[float, float, float, float]


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


class PersonTrackGate:
    """Require one strong, spatially continuous person detection on valid frames."""

    def __init__(self, confidence: float = 0.5, min_iou: float = 0.5, max_gap: float = 0.25):
        if not all(math.isfinite(v) for v in (confidence, min_iou, max_gap)):
            raise ValueError("Gate parameters must be finite")
        if not 0 <= confidence <= 1 or not 0 <= min_iou <= 1 or max_gap <= 0:
            raise ValueError("Invalid gate parameters")
        self.confidence, self.min_iou, self.max_gap = confidence, min_iou, max_gap
        self._previous: tuple[float, tuple[float, float, float, float], tuple[int, int]] | None = (
            None
        )

    def reset(self) -> None:
        self._previous = None

    def observe(
        self,
        timestamp: float,
        candidates: Iterable[PersonCandidate],
        frame_size: tuple[int, int] | None,
        *,
        valid_frame: bool = True,
    ) -> dict[str, object]:
        raw = list(candidates)
        result: dict[str, object] = {
            "person_track_supported": False,
            "person_candidate": None,
            "reason": "invalid_frame",
        }
        if not valid_frame or frame_size is None or not math.isfinite(timestamp):
            self.reset()
            return result
        width, height = frame_size
        if width <= 0 or height <= 0:
            self.reset()
            return result
        finite = all(
            math.isfinite(candidate.confidence)
            and len(candidate.bbox) == 4
            and all(math.isfinite(value) for value in candidate.bbox)
            and 0 <= candidate.confidence <= 1
            and 0 <= candidate.bbox[0] < candidate.bbox[2] <= width
            and 0 <= candidate.bbox[1] < candidate.bbox[3] <= height
            for candidate in raw
        )
        if not finite:
            self.reset()
            result["reason"] = "invalid_candidate"
            return result
        strong = [candidate for candidate in raw if candidate.confidence >= self.confidence]
        if len(strong) != 1:
            self.reset()
            result["reason"] = "not_one_strong_person"
            return result
        chosen = strong[0]
        result["person_candidate"] = {
            "confidence": chosen.confidence,
            "bbox": list(chosen.bbox),
        }
        current = (timestamp, chosen.bbox, frame_size)
        previous = self._previous
        self._previous = current
        if previous is None:
            result["reason"] = "needs_previous_frame"
            return result
        previous_time, previous_box, previous_size = previous
        gap = timestamp - previous_time
        if previous_size != frame_size:
            self.reset()
            result["reason"] = "frame_size_changed"
            return result
        if not 0 < gap <= self.max_gap:
            self.reset()
            result["reason"] = "timestamp_gap"
            return result
        overlap = _iou(previous_box, chosen.bbox)
        result["iou_with_previous"] = overlap
        if overlap < self.min_iou:
            self.reset()
            result["reason"] = "box_jump"
            return result
        result["person_track_supported"] = True
        result["reason"] = "continuous_unique_person"
        return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _person_candidates(raw: np.ndarray, transform) -> list[PersonCandidate]:
    rows = np.asarray(raw)
    if rows.shape != (1, 300, 6) or not np.isfinite(rows).all():
        raise ValueError("Expected finite static [1,300,6] detector output")
    rows = rows[0]
    selected = rows[(np.abs(rows[:, 5]) <= 1e-3) & (rows[:, 4] > 0)]
    boxes = inverse_letterbox(selected[:, :4], transform) if len(selected) else []
    height, width = transform.original_height, transform.original_width
    candidates = []
    for row, box in zip(selected, boxes, strict=True):
        x1, y1, x2, y2 = (float(v) for v in box)
        x1, x2 = min(max(x1, 0.0), width), min(max(x2, 0.0), width)
        y1, y2 = min(max(y1, 0.0), height), min(max(y2, 0.0), height)
        if x2 > x1 and y2 > y1:
            candidates.append(PersonCandidate(float(row[4]), (x1, y1, x2, y2)))
    return sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)


def collect_cache(manifest_path: Path, model_path: Path, output_dir: Path) -> None:
    manifest_path = manifest_path.resolve()
    model_path = model_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    videos = [
        {key: video[key] for key in ("path", "sha256", "duration_seconds", "decoded_frames")}
        for video in manifest["videos"]
    ]
    output_dir.mkdir(parents=True, exist_ok=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_name, output_name = session.get_inputs()[0].name, session.get_outputs()[0].name
    rows_path = output_dir / "person-predictions.jsonl"
    source_summaries = []
    with rows_path.open("w", encoding="utf-8") as output:
        for video in videos:
            source = ROOT / video["path"]
            if _sha256(source) != video["sha256"]:
                raise ValueError(f"Source checksum mismatch: {source}")
            capture = cv2.VideoCapture(str(source))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if not math.isfinite(fps) or fps <= 0:
                capture.release()
                raise ValueError(f"Invalid source FPS: {source}")
            stride = fps / 10.0
            next_sample = 0.0
            decoded = sampled = 0
            gate = PersonTrackGate()
            last_timestamp = None
            try:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    frame_index = decoded
                    decoded += 1
                    if frame_index + 1e-6 < next_sample:
                        continue
                    next_sample += stride
                    tensor, transform = letterbox(frame, 640)
                    raw = session.run([output_name], {input_name: tensor})[0]
                    candidates = _person_candidates(raw, transform)
                    timestamp = frame_index / fps
                    gate_result = gate.observe(
                        timestamp, candidates, (frame.shape[1], frame.shape[0])
                    )
                    row = {
                        "source": video["path"],
                        "source_sha256": video["sha256"],
                        "frame_index": frame_index,
                        "timestamp_s": timestamp,
                        "frame_size": [frame.shape[1], frame.shape[0]],
                        "person_candidates": [
                            {"confidence": item.confidence, "bbox": list(item.bbox)}
                            for item in candidates
                        ],
                        "person_track_supported": gate_result["person_track_supported"],
                        "person_candidate": gate_result["person_candidate"],
                        "gate_reason": gate_result["reason"],
                    }
                    if "iou_with_previous" in gate_result:
                        row["iou_with_previous"] = gate_result["iou_with_previous"]
                    output.write(json.dumps(row) + "\n")
                    sampled += 1
                    last_timestamp = timestamp
            finally:
                capture.release()
            decoded_duration = decoded / fps
            if (
                decoded != video["decoded_frames"]
                or abs(decoded_duration - video["duration_seconds"]) > 0.1
            ):
                raise ValueError(f"Decoded source metadata mismatch: {source}")
            source_summaries.append(
                {
                    "source": video["path"],
                    "source_sha256": video["sha256"],
                    "fps": fps,
                    "decoded_frames": decoded,
                    "sampled_frames": sampled,
                    "decoded_duration_seconds": decoded_duration,
                    "last_sample_timestamp_s": last_timestamp,
                }
            )
            print(json.dumps(source_summaries[-1]), flush=True)
    metadata = {
        "kind": "development_person_cache_not_independent_test",
        "sample_hz": 10.0,
        "model": str(model_path.relative_to(ROOT)),
        "model_sha256": _sha256(model_path),
        "person_class_id": 0,
        "gate_defaults": {"confidence": 0.5, "min_iou": 0.5, "max_gap": 0.25},
        "labels_used_for_gate": False,
        "meaning": (
            "person_track_supported is coarse auxiliary continuity evidence, "
            "not proof of an unoccluded complete action"
        ),
        "predictions_file": rows_path.name,
        "predictions_sha256": _sha256(rows_path),
        "sources": source_summaries,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect_cache(args.manifest, args.model, args.output)


if __name__ == "__main__":
    main()
