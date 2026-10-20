#!/usr/bin/env python3
"""Verify a local reproduction bundle without training or opening a camera.

Integrity checks use only the standard library. Replay is opt-in, imports the
prepared bundle's production adapters, and never opens final/holdout media.
This program neither reads an .env file nor contacts a model service.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    """Reject traversal and symlinks in every component, even internal links."""
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts or not parts.parts:
        raise ValueError(f"unsafe bundle destination: {relative!r}")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink is forbidden: {relative}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"destination leaves bundle: {relative}")
    if not path.is_file():
        raise ValueError(f"mapped regular file is missing: {relative}")
    return path


class Catalog:
    def __init__(self, root: Path, data: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.data = data
        self.original_root = str(data.get("original_root", "")).rstrip("/")
        self.entries: dict[str, dict[str, Any]] = {}
        self._digests: dict[tuple[str, int, int], str] = {}
        for row in data.get("files", []):
            source = self.source_key(row["source"])
            if source in self.entries:
                raise ValueError(f"duplicate source mapping: {source}")
            self.entries[source] = row

    def source_key(self, value: str, parent: str | None = None) -> str:
        value = str(value).replace("\\", "/")
        if value.startswith(self.original_root + "/"):
            value = value[len(self.original_root) + 1 :]
        elif PurePosixPath(value).is_absolute():
            raise ValueError(f"source reference is outside original root: {value}")
        if parent:
            value = str(PurePosixPath(parent) / value)
        # Normalize only lexical . / ..; never follow the original location.
        parts: list[str] = []
        for part in PurePosixPath(value).parts:
            if part == "..":
                if not parts:
                    raise ValueError(f"source reference escapes root: {value}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        return "/".join(parts)

    def key(self, value: str, parent: str | None = None) -> str:
        direct = self.source_key(value)
        if direct in self.entries or not parent or str(value).startswith(self.original_root):
            return direct
        return self.source_key(value, parent)

    def path(self, value: str, parent: str | None = None) -> Path:
        key = self.key(value, parent)
        if key not in self.entries:
            raise ValueError(f"source reference has no mapping: {key}")
        return safe_file(self.root, self.entries[key]["destination"])

    def digest(self, path: Path) -> str:
        """Reuse the byte check for repeated source references, never a declared SHA."""
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._digests:
            self._digests[key] = sha256(path)
        return self._digests[key]


def historical_note(notes: Any, value: str) -> bool:
    text = json.dumps(notes, ensure_ascii=False).lower()
    return value.lower() in text and any(
        word in text for word in ("histor", "legacy", "旧", "历史", "missing", "缺失")
    )


def source_references(value: Any) -> list[dict[str, Any]]:
    """Historical manifests store sources both as named objects and as lists."""
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            return [value]
        return [row for child in value.values() for row in source_references(child)]
    if isinstance(value, list):
        return [row for child in value for row in source_references(child)]
    return []


def verify_source_reference(
    catalog: Catalog, reference: dict[str, Any], parent: str, notes: Any
) -> tuple[str | None, str | None]:
    value = reference["path"]
    try:
        key = catalog.key(value, parent)
        if key not in catalog.entries and len(PurePosixPath(value).parts) == 1:
            candidates = [
                candidate
                for candidate, row in catalog.entries.items()
                if PurePosixPath(candidate).name == value
                and reference.get("sha256") == row.get("sha256")
            ]
            if len(candidates) == 1:
                key = candidates[0]
            elif len(candidates) > 1:
                # Byte-identical copies are aliases of the same frozen video.
                key = sorted(candidates)[0]
        path = catalog.path(key)
        if reference.get("sha256") and catalog.digest(path) != reference["sha256"]:
            raise ValueError(f"source reference SHA mismatch: {value}")
    except (OSError, ValueError) as exc:
        if historical_note(notes, value) or historical_note(notes, catalog.key(value, parent)):
            return None, str(exc)
        return str(exc), None
    return None, None


def validate_labels(path: Path, classes: set[int] | None) -> None:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        values = line.split()
        try:
            category = int(values[0])
            coordinates = [float(value) for value in values[1:]]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"YOLO label invalid at {path.name}:{number}") from exc
        if (
            len(values) != 5
            or category < 0
            or (classes is not None and category not in classes)
            or any(not math.isfinite(value) or not 0 <= value <= 1 for value in coordinates)
            or coordinates[2] <= 0
            or coordinates[3] <= 0
        ):
            raise ValueError(f"YOLO label invalid/range error at {path.name}:{number}")


def _count_errors(declared: Any, split_counts: Counter, class_counts: Counter) -> list[str]:
    """Check the two historical count representations used by the datasets."""
    if not isinstance(declared, dict) or not declared:
        return []
    errors = []
    for key, wanted in declared.items():
        if isinstance(wanted, int):
            if "/" in key:
                actual = sum(
                    v for k, v in class_counts.items() if k == key or k.endswith("/" + key)
                )
            else:
                actual = split_counts[key]
            if actual != wanted:
                errors.append(f"count {key}: declared {wanted}, observed {actual}")
        elif isinstance(wanted, dict):
            for label, count in wanted.items():
                candidates = [v for k, v in class_counts.items() if k.endswith(f"/{key}/{label}")]
                actual = sum(candidates)
                if actual != count:
                    errors.append(f"count {key}/{label}: declared {count}, observed {actual}")
    return errors


def verify_dataset(catalog: Catalog, dataset: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": dataset["id"],
        "status": "passed",
        "samples": 0,
        "errors": [],
        "warnings": [],
    }
    errors, warnings = result["errors"], result["warnings"]
    try:
        manifest_key = catalog.key(dataset["manifest"])
        manifest = json.loads(catalog.path(manifest_key).read_text(encoding="utf-8"))
        parent = str(PurePosixPath(manifest_key).parent)
        source_dir = catalog.source_key(dataset.get("source_dir", parent))
        samples = manifest.get("samples")
        if not isinstance(samples, list):
            raise ValueError("dataset manifest must contain a samples list")
        names = manifest.get("names", manifest.get("classes"))
        classes = {int(k) for k in names} if isinstance(names, dict) else None
        if isinstance(names, list):
            classes = set(range(len(names)))
        split_counts: Counter = Counter()
        class_counts: Counter = Counter()
        seen: set[str] = set()
        groups: dict[str, set[str]] = {}
        for index, sample in enumerate(samples):
            try:
                image_ref = sample.get(
                    "image_path", sample.get("path", sample.get("image", sample.get("output")))
                )
                if not isinstance(image_ref, str):
                    raise ValueError("sample image path is missing")
                image_key = catalog.key(image_ref, parent)
                if image_key in seen:
                    raise ValueError(f"duplicate sample image: {image_key}")
                seen.add(image_key)
                image = catalog.path(image_key)
                expected = sample.get("sha256", sample.get("image_sha256"))
                if not isinstance(expected, str) or catalog.digest(image) != expected:
                    raise ValueError(f"sample image SHA mismatch: {image_key}")
                split = str(sample.get("split", ""))
                if not split:
                    raise ValueError(f"sample split missing: {image_key}")
                split_counts[split] += 1
                if "label" in sample:
                    task = str(sample.get("task", dataset.get("task", "")))
                    class_counts[f"{task}/{split}/{sample['label']}"] += 1
                    # The class directory is part of the training contract.
                    if PurePosixPath(image_key).parent.name != sample["label"]:
                        raise ValueError(f"sample class directory disagrees: {image_key}")
                # Detection labels must be paired at the active dataset path.
                # Some historical manifests retained stale upstream label_path.
                if "/images/" in image_key:
                    label_key = image_key.replace("/images/", "/labels/", 1)
                    label_key = str(PurePosixPath(label_key).with_suffix(".txt"))
                    label = catalog.path(label_key)
                    validate_labels(label, classes)
                    if sample.get("label_path"):
                        old_key = catalog.key(sample["label_path"], parent)
                        if old_key != label_key:
                            if old_key in catalog.entries:
                                if catalog.digest(catalog.path(old_key)) != catalog.digest(label):
                                    raise ValueError(f"upstream label content differs: {old_key}")
                            else:
                                warnings.append(
                                    f"historical label_path superseded by active pair: {old_key}"
                                )
                elif sample.get("label_path"):
                    validate_labels(catalog.path(sample["label_path"], parent), classes)
                for field in ("source_image_path", "source_video"):
                    if not sample.get(field):
                        continue
                    reference = {"path": sample[field]}
                    if field == "source_video" and sample.get("source_video_sha256"):
                        reference["sha256"] = sample["source_video_sha256"]
                    error, warning = verify_source_reference(
                        catalog, reference, parent, dataset.get("notes")
                    )
                    if error:
                        errors.append(error)
                    if warning:
                        warnings.append(warning)
                group = sample.get("source_sha256", sample.get("group"))
                if group:
                    groups.setdefault(str(group), set()).add(split)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"sample {index}: {exc}")
        result["samples"] = len(samples)
        result["split_counts"] = dict(split_counts)
        result["class_counts"] = dict(class_counts)
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        mapped_images = {
            key
            for key in catalog.entries
            if key.startswith(source_dir + "/")
            and PurePosixPath(key).suffix.lower() in image_extensions
        }
        if mapped_images != seen:
            errors.append(
                f"dataset image inventory differs from manifest: "
                f"{len(mapped_images - seen)} unlisted, {len(seen - mapped_images)} outside dataset"
            )
        expected_labels = {
            str(PurePosixPath(key.replace("/images/", "/labels/", 1)).with_suffix(".txt"))
            for key in seen
            if "/images/" in key
        }
        mapped_labels = {
            key
            for key in catalog.entries
            if key.startswith(source_dir + "/labels/") and key.endswith(".txt")
        }
        if expected_labels != mapped_labels:
            errors.append(
                "YOLO image/label inventory differs: "
                f"{len(mapped_labels - expected_labels)} unpaired labels, "
                f"{len(expected_labels - mapped_labels)} missing labels"
            )
        for where, counts in (
            ("manifest", manifest.get("counts")),
            ("catalog", dataset.get("counts")),
        ):
            errors.extend(
                f"{where} {error}" for error in _count_errors(counts, split_counts, class_counts)
            )
        # Split leakage is not silently promoted to independent validation.
        for group, splits in groups.items():
            if len(splits) > 1:
                message = f"source group spans splits {sorted(splits)}: {group}"
                if historical_note(dataset.get("notes"), group):
                    warnings.append(message)
                else:
                    errors.append(message)
        for field in ("sources", "inputs"):
            for reference in source_references(manifest.get(field, [])):
                error, warning = verify_source_reference(
                    catalog, reference, parent, dataset.get("notes")
                )
                if error:
                    errors.append(error)
                if warning:
                    warnings.append(warning)
        extra_references = []
        if isinstance(manifest.get("source_registry"), str):
            extra_references.append({"path": manifest["source_registry"]})
        for path, digest in manifest.get("verified_development_sources", {}).items():
            extra_references.append({"path": path, "sha256": digest})
        for reference in extra_references:
            error, warning = verify_source_reference(
                catalog, reference, parent, dataset.get("notes")
            )
            if error:
                errors.append(error)
            if warning:
                warnings.append(warning)
        result["source_dir"] = source_dir
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    result["status"] = "failed" if errors else "passed"
    return result


def verify_recipe_fingerprint(catalog: Catalog, recipe: dict[str, Any]) -> dict[str, Any]:
    """Recompute the training script's frozen identity-and-file-byte digest."""
    required = recipe.get("validity") != "invalid"
    result: dict[str, Any] = {
        "recipe": recipe["id"],
        "status": "passed" if required else "not_required_invalid",
        "required": required,
        "errors": [],
        "files_verified": 0,
    }
    fingerprint = recipe.get("dataset_fingerprint")
    if not fingerprint:
        if required:
            result["errors"].append("ready recipe has no frozen dataset_fingerprint")
            result["status"] = "failed"
        return result
    try:
        algorithm = fingerprint["algorithm"]
        if algorithm not in {"yolo-path-sha256-v1", "classification-relative-sha256-v1"}:
            raise ValueError(f"unsupported fingerprint algorithm: {algorithm}")
        expected = fingerprint["expected"]
        record = json.loads(catalog.path(recipe["record"]).read_text(encoding="utf-8"))
        recorded = (
            record.get("dataset_sha256")
            if algorithm == "yolo-path-sha256-v1"
            else record.get("dataset", {}).get("sha256")
        )
        if expected != recorded:
            raise ValueError("fingerprint expected differs from preserved training record")
        files = fingerprint["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("fingerprint frozen file order must be a nonempty list")
        seen = set()
        digest = hashlib.sha256()
        for row in files:
            source = catalog.key(row["source"])
            if source in seen:
                raise ValueError(f"duplicate fingerprint source: {source}")
            seen.add(source)
            actual_sha = catalog.digest(catalog.path(source))
            if actual_sha != row["sha256"]:
                result["errors"].append(f"frozen dataset file SHA mismatch: {source}")
            else:
                result["files_verified"] += 1
            digest.update(row["identity"].encode("utf-8"))
            if algorithm == "yolo-path-sha256-v1":
                digest.update(b"\0")
            digest.update(bytes.fromhex(actual_sha))
        actual = digest.hexdigest()
        result.update(
            algorithm=algorithm, expected=expected, recomputed=actual, matched=actual == expected
        )
        if actual != expected:
            result["errors"].append(
                "recomputed dataset fingerprint differs from historical training"
            )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["errors"].append(str(exc))
    if required and result["errors"]:
        result["status"] = "failed"
    return result


def compare_predictions(reference: Any, actual: Any, task: str) -> dict[str, Any]:
    """Compare the same ONNX on the same frame after relocation, not accuracy."""
    if task in {"object", "objects", "scene", "detect", "detection"}:

        def key(row):
            return (row["category"], *row["bbox"])

        left, right = sorted(reference, key=key), sorted(actual, key=key)
        if len(left) != len(right):
            return {"passed": False, "reason": "detection count differs"}
        max_box, max_confidence, min_iou = 0.0, 0.0, 1.0
        for a, b in zip(left, right, strict=True):
            if a["category"] != b["category"]:
                return {"passed": False, "reason": "detection class differs"}
            box_a, box_b = a["bbox"], b["bbox"]
            max_box = max(max_box, *(abs(x - y) for x, y in zip(box_a, box_b, strict=True)))
            max_confidence = max(max_confidence, abs(a["confidence"] - b["confidence"]))
            overlap = max(0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])) * max(
                0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1])
            )

            def area(box):
                return max(0, box[2] - box[0]) * max(0, box[3] - box[1])

            union = area(box_a) + area(box_b) - overlap
            min_iou = min(min_iou, overlap / union if union else 0)
        return {
            "passed": max_box <= 0.1 and max_confidence <= 0.001 and min_iou >= 0.999,
            "max_box_error_px": max_box,
            "max_confidence_error": max_confidence,
            "min_iou": min_iou,
        }
    a, b = reference["probabilities"], actual["probabilities"]
    if set(a) != set(b):
        return {"passed": False, "reason": "class mapping differs"}
    difference = max((abs(a[k] - b[k]) for k in a), default=0.0)
    return {
        "passed": reference["label"] == actual["label"] and difference <= 0.0001,
        "max_probability_error": difference,
        "labels_equal": reference["label"] == actual["label"],
    }


def _sealed(value: str) -> bool:
    return bool(re.search(r"final[-_]?test|holdout|sealed|未解封", value, re.IGNORECASE))


def load_production_model(
    catalog: Catalog, recipe: dict[str, Any], model_ref: str, *, reference_root: Path | None = None
) -> tuple[Any, str]:
    """Construct the real adapter; relocated records are changed only in memory."""
    from visual_ai_agent.behavior import OnnxClassifier
    from visual_ai_agent.vision import YoloOnnxDetector

    key = catalog.key(model_ref)
    bundled = catalog.path(key)
    expected = catalog.entries[key]["sha256"]
    path = reference_root / key if reference_root else bundled
    if path.is_symlink() or not path.is_file() or sha256(path) != expected:
        raise ValueError(f"model baseline SHA mismatch: {key}")
    task = str(recipe.get("task", "objects"))
    record_ref = recipe.get("record")
    record = json.loads(catalog.path(record_ref).read_text()) if record_ref else {}
    if record.get("preprocessing") and record.get("dataset", {}).get("classes"):
        expected_preprocessing = {
            "layout": "NCHW",
            "dtype": "float32",
            "color": "RGB",
            "resize": "aspect-ratio-preserving letterbox",
            "range": [0.0, 1.0],
            "fill_rgb": [114, 114, 114],
        }
        if record.get("status") != "completed" or any(
            record["preprocessing"].get(k) != v for k, v in expected_preprocessing.items()
        ):
            raise ValueError(
                "classifier record has an incomplete production preprocessing contract"
            )
        if record.get("onnx_sha256") != expected:
            raise ValueError("classifier record ONNX SHA differs from reference artifact")
        names = {int(k): str(v) for k, v in record["dataset"]["classes"].items()}
        # OnnxClassifier checks static tensor and metadata contracts itself.
        # The outer catalog verifies the exact model digest before construction.
        return OnnxClassifier({**record, "_onnx": path, "_names": names}), "classification"
    official = key == "models/yolo26n-e2e.onnx"
    manifest = catalog.path("model_manifests/yolo26n-e2e.onnx.json") if official else None
    detector = YoloOnnxDetector(
        path,
        confidence=0.35,
        manifest_path=manifest,
        verify_manifest=official,
        expected_sha256=expected,
    )
    return detector, "objects" if task else "objects"


def model_prediction(model: Any, kind: str, frame: Any) -> Any:
    if kind == "classification":
        result = model.predict(frame)
        return {"label": result["label"], "probabilities": result["probabilities"]}
    return [
        {"category": row.category, "confidence": row.confidence, "bbox": list(row.bbox)}
        for row in model.detect(frame)
    ]


def probe_onnx(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Load a historical artifact with CPU ORT; do not claim task compatibility."""
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    import onnxruntime as ort

    if sha256(path) != expected_sha256:
        raise ValueError("ONNX SHA mismatch before session construction")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )

    def describe(values):
        return [{"name": value.name, "shape": value.shape, "type": value.type} for value in values]

    return {
        "status": "passed",
        "scope": "session construction only; no inference or accuracy claim",
        "inputs": describe(session.get_inputs()),
        "outputs": describe(session.get_outputs()),
        "providers": session.get_providers(),
        "class_metadata": session.get_modelmeta().custom_metadata_map.get("names"),
    }


def production_compatibility(probe: dict[str, Any]) -> tuple[bool, str]:
    """Classify historical heads by their recorded tensor/class contract."""
    raw = probe.get("class_metadata")
    try:
        names = {int(k): str(v) for k, v in ast.literal_eval(raw).items()}
    except (AttributeError, TypeError, ValueError, SyntaxError):
        return False, "historical ONNX has no parseable class mapping"
    outputs = probe.get("outputs", [])
    shape = outputs[0].get("shape") if len(outputs) == 1 else None
    if shape == [1, 300, 6]:
        from visual_ai_agent.vision import COCO_CLASS_NAMES

        compatible = all(names.get(k) == v for k, v in COCO_CLASS_NAMES.items())
        return compatible, "historical detection head differs from production COCO class indices"
    supported = ({"seated", "standing", "empty"}, {"drinking", "not_drinking"}, {"open", "closed"})
    compatible = (
        set(names.values()) in supported
        and set(names) == set(range(len(names)))
        and shape == [1, len(names)]
    )
    return (
        compatible,
        "historical classification head differs from supported production task classes",
    )


def _record_requires_production(catalog: Catalog, recipe: dict[str, Any], key: str) -> bool:
    if key == "models/yolo26n-e2e.onnx":
        return True
    if not recipe.get("record"):
        return False
    record = json.loads(catalog.path(recipe["record"]).read_text())
    names = record.get("class_names", record.get("dataset", {}).get("classes", {}))
    if not isinstance(names, dict):
        return False
    names = {int(k): str(v) for k, v in names.items()}
    return set(names.values()) in (
        {"seated", "standing", "empty"},
        {"drinking", "not_drinking"},
        {"open", "closed"},
    ) or all(names.get(k) == v for k, v in {39: "bottle", 41: "cup", 67: "cell phone"}.items())


def normalized_event(event: Any, origin: datetime) -> dict[str, Any]:
    """Exclude random persistence IDs; retain every deterministic event fact."""
    return {
        "kind": event.kind,
        "category": event.category,
        "regions": sorted(event.regions),
        "observed_at_seconds": (event.observed_at - origin).total_seconds(),
        "confirmed_at_seconds": (event.confirmed_at - origin).total_seconds(),
        "last_seen_at_seconds": (
            (event.last_seen_at - origin).total_seconds() if event.last_seen_at else None
        ),
        "source": event.source,
    }


def compare_event_sequences(reference: list[dict], actual: list[dict]) -> dict[str, Any]:
    if reference == actual:
        return {"passed": True, "reference_count": len(reference), "actual_count": len(actual)}
    mismatch = next(
        (index for index, (a, b) in enumerate(zip(reference, actual, strict=False)) if a != b),
        min(len(reference), len(actual)),
    )
    return {
        "passed": False,
        "reference_count": len(reference),
        "actual_count": len(actual),
        "first_difference_index": mismatch,
    }


def verify_fault_sequence(machine: Any, last_time: float, origin: datetime) -> dict[str, Any]:
    """Faults beyond the missing timeout must discard pending absence evidence."""
    from visual_ai_agent.models import SceneObservation

    checks = []
    for offset, status in enumerate(("stale", "paused", "disconnected", "stopped"), 1):
        timestamp = last_time + offset * 10.0
        events = machine.process(
            SceneObservation(
                observed_at=origin + timedelta(seconds=timestamp),
                monotonic_at=timestamp,
                status=status,
                fresh=False,
                detections=[],
                source="replay",
            )
        )
        checks.append(
            {
                "status": status,
                "relative_time_seconds": timestamp,
                "passed": not any(event.kind == "missing" for event in events),
                "events": [normalized_event(event, origin) for event in events],
            }
        )
    return {"passed": all(row["passed"] for row in checks), "checks": checks}


def _detection_rows(detections: Any) -> list[dict[str, Any]]:
    return [
        {
            "category": row.category,
            "confidence": row.confidence,
            "bbox": list(row.bbox),
            "region": row.region,
        }
        for row in detections
    ]


def replay_video_events(catalog: Catalog, reference_root: Path | None) -> dict[str, Any]:
    """Sequential fixed development-video relocation replay through real events."""
    import cv2

    from visual_ai_agent.events import EventStateMachine
    from visual_ai_agent.models import SceneObservation
    from visual_ai_agent.vision import CupScaleRecheckDetector

    result: dict[str, Any] = {
        "status": "passed",
        "errors": [],
        "warnings": [],
        "videos": [],
        "scope": "offline event relocation equivalence; no accuracy, USB, API, or training claim",
        "sampling": "sequential decode to EOF, nearest frame at each integer second, 1Hz",
        "detector_contract": {"confidence": 0.35, "cup_scale_recheck": 0.75},
    }
    selected: dict[str, tuple[dict[str, Any], str]] = {}
    for recipe in catalog.data.get("recipes", []):
        if recipe.get("task") != "objects":
            continue
        references = recipe.get("reference_onnx") or []
        if isinstance(references, str):
            references = [references]
        for reference in references:
            match = re.search(r"scene-yolo26n-(r0[345])(?:/|$)", reference)
            if match:
                revision = match.group(1).upper()
                if revision in selected and selected[revision][1] != reference:
                    result["errors"].append(f"ambiguous event replay model: {revision}")
                selected[revision] = (recipe, reference)
    if set(selected) != {"R03", "R04", "R05"}:
        result["errors"].append("event replay requires the recorded R03/R04/R05 references")
    videos = catalog.data.get("replay_videos", [])
    if not videos:
        result["errors"].append("no explicit development replay_videos")
    models = {}
    for revision, (recipe, reference) in sorted(selected.items()):
        try:
            bundled, kind = load_production_model(catalog, recipe, reference)
            if kind != "objects":
                raise ValueError("object event replay received a classifier")
            baseline = None
            if reference_root is not None:
                baseline, _ = load_production_model(
                    catalog, recipe, reference, reference_root=reference_root
                )
            models[revision] = (
                CupScaleRecheckDetector(bundled),
                CupScaleRecheckDetector(baseline) if baseline is not None else None,
            )
        except Exception as exc:
            result["errors"].append(f"{revision}: {type(exc).__name__}: {exc}")
    if result["errors"]:
        result["status"] = "failed"
        return result
    origin = datetime(2000, 1, 1, tzinfo=UTC)
    for raw_entry in videos:
        entry = {"path": raw_entry} if isinstance(raw_entry, str) else raw_entry
        reference = entry["path"]
        video: dict[str, Any] = {
            "path": reference,
            "status": "passed",
            "samples": 0,
            "decoded_frames": 0,
            "models": {},
            "errors": [],
        }
        capture = None
        try:
            if _sealed(reference):
                raise ValueError(f"refusing sealed/final media: {reference}")
            capture = cv2.VideoCapture(str(catalog.path(reference)))
            if not capture.isOpened():
                raise ValueError(f"cannot open development video: {reference}")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if not math.isfinite(fps) or fps <= 0:
                raise ValueError("video has no valid frame rate; cannot establish 1Hz timeline")
            video["fps"] = fps
            video["declared_frames"] = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            machines = {}
            for revision in models:
                machines[revision] = (EventStateMachine(), EventStateMachine())
                video["models"][revision] = {
                    "model": selected[revision][1],
                    "status": "passed",
                    "samples": [],
                    "events": [],
                    "reference_events": [],
                    "comparison_requested": reference_root is not None,
                }
            frame_index, second, last_time = 0, 0, 0.0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                video["decoded_frames"] += 1
                if frame_index < round(second * fps):
                    frame_index += 1
                    continue
                timestamp = frame_index / fps
                video["samples"] += 1
                last_time = timestamp
                for revision, (bundled, baseline) in models.items():
                    item = video["models"][revision]
                    machine, baseline_machine = machines[revision]
                    detections = bundled.detect(frame)
                    rows = _detection_rows(detections)
                    observed = origin + timedelta(seconds=timestamp)
                    observation = SceneObservation(
                        observed_at=observed,
                        monotonic_at=timestamp,
                        status="running",
                        fresh=True,
                        detections=detections,
                        width=frame.shape[1],
                        height=frame.shape[0],
                        source="replay",
                    )
                    item["events"].extend(
                        normalized_event(event, origin) for event in machine.process(observation)
                    )
                    comparison = {"passed": True, "comparison": "not requested"}
                    baseline_rows = None
                    if baseline is not None:
                        baseline_detections = baseline.detect(frame)
                        baseline_rows = _detection_rows(baseline_detections)
                        comparison = compare_predictions(baseline_rows, rows, "objects")
                        if [
                            row["region"]
                            for row in sorted(
                                baseline_rows, key=lambda row: (row["category"], *row["bbox"])
                            )
                        ] != [
                            row["region"]
                            for row in sorted(rows, key=lambda row: (row["category"], *row["bbox"]))
                        ]:
                            comparison = {**comparison, "passed": False, "reason": "regions differ"}
                        baseline_observation = observation.model_copy(
                            update={"detections": baseline_detections}
                        )
                        item["reference_events"].extend(
                            normalized_event(event, origin)
                            for event in baseline_machine.process(baseline_observation)
                        )
                    item["samples"].append(
                        {
                            "frame_index": frame_index,
                            "relative_time_seconds": timestamp,
                            "requested_second": second,
                            "detections": rows,
                            "reference_detections": baseline_rows,
                            "comparison": comparison,
                        }
                    )
                    if not comparison["passed"]:
                        item["status"] = "failed"
                        video["errors"].append(
                            f"{revision}: detections differ at frame {frame_index}"
                        )
                second += 1
                frame_index += 1
            if video["samples"] == 0:
                raise ValueError("development video produced no decodable samples")
            if video["decoded_frames"] < video["declared_frames"]:
                result["warnings"].append(
                    f"{reference}: decoded {video['decoded_frames']} of declared "
                    f"{video['declared_frames']} frames; actual samples retained"
                )
            for revision, (machine, baseline_machine) in machines.items():
                item = video["models"][revision]
                item["faults"] = verify_fault_sequence(machine, last_time, origin)
                if not item["faults"]["passed"]:
                    item["status"] = "failed"
                    video["errors"].append(f"{revision}: fault produced missing event")
                if reference_root is not None:
                    item["reference_faults"] = verify_fault_sequence(
                        baseline_machine, last_time, origin
                    )
                    item["event_comparison"] = compare_event_sequences(
                        item["reference_events"], item["events"]
                    )
                    if (
                        not item["reference_faults"]["passed"]
                        or not item["event_comparison"]["passed"]
                    ):
                        item["status"] = "failed"
                        video["errors"].append(
                            f"{revision}: reference fault or event comparison failed"
                        )
        except Exception as exc:
            video["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            if capture is not None:
                capture.release()
        if video["errors"]:
            video["status"] = "failed"
            result["errors"].extend(f"{reference}: {error}" for error in video["errors"])
        result["videos"].append(video)
    result["total_samples_per_model"] = sum(video["samples"] for video in result["videos"])
    if result["errors"]:
        result["status"] = "failed"
    return result


def replay_bundle(catalog: Catalog, *, reference_root: Path | None = None) -> dict[str, Any]:
    """Probe each recorded reference model against explicitly allowed dev media.

    `replay_samples` is a list of original image/video paths, or dictionaries
    with `path` and optional `frame_indices`. No image is selected implicitly:
    the packager must freeze previously used development examples explicitly.
    """
    result: dict[str, Any] = {
        "status": "passed",
        "models": [],
        "onnx_load": [],
        "errors": [],
        "warnings": [],
        "scope": "offline relocation equivalence; no accuracy/USB/API claims",
    }
    project = catalog.root / "work/project"
    if not (project / "visual_ai_agent/vision.py").is_file():
        return {**result, "status": "failed", "errors": ["prepare work/project before --replay"]}
    # Do not accidentally use an installed or caller checkout's adapter.
    existing = sys.modules.get("visual_ai_agent")
    if existing is not None:
        module_path = Path(existing.__file__).resolve()
        if not module_path.is_relative_to(project.resolve()):
            return {
                **result,
                "status": "failed",
                "errors": ["production package already imported outside bundle"],
            }
    sys.path.insert(0, str(project))
    import cv2

    for key, entry in catalog.entries.items():
        if not key.lower().endswith(".onnx"):
            continue
        try:
            result["onnx_load"].append(
                {"model": key, **probe_onnx(catalog.path(key), entry["sha256"])}
            )
        except Exception as exc:
            result["onnx_load"].append({"model": key, "status": "failed", "error": str(exc)})
            result["errors"].append(f"ONNX load {key}: {exc}")
    probes = {row["model"]: row for row in result["onnx_load"]}

    samples = catalog.data.get("replay_samples", [])
    if not samples:
        return {**result, "status": "failed", "errors": ["no explicit development replay_samples"]}
    frames: list[tuple[str, Any]] = []
    for sample in samples:
        entry = {"path": sample} if isinstance(sample, str) else sample
        value = entry["path"]
        if _sealed(value):
            result["errors"].append(f"refusing sealed/final media: {value}")
            continue
        try:
            path = catalog.path(value)
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                frame = cv2.imread(str(path))
                if frame is None:
                    raise ValueError(f"cannot decode development image: {value}")
                frames.append((value, frame))
            else:
                capture = cv2.VideoCapture(str(path))
                try:
                    if not capture.isOpened():
                        raise ValueError(f"cannot open development video: {value}")
                    for index in entry.get("frame_indices", [0]):
                        if not isinstance(index, int) or index < 0:
                            raise ValueError("frame_indices must be nonnegative integers")
                        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = capture.read()
                        if not ok:
                            raise ValueError(f"cannot decode development frame {value}:{index}")
                        frames.append((f"{value}:{index}", frame))
                finally:
                    capture.release()
        except (OSError, ValueError, KeyError) as exc:
            result["errors"].append(str(exc))
    seen: set[str] = set()
    recipes = list(catalog.data.get("recipes", []))
    if "models/yolo26n-e2e.onnx" in catalog.entries:
        recipes.insert(
            0,
            {
                "id": "official-yolo26n",
                "task": "objects",
                "reference_onnx": "models/yolo26n-e2e.onnx",
            },
        )
    for recipe in recipes:
        references = recipe.get("reference_onnx") or []
        if isinstance(references, str):
            references = [references]
        for reference in references:
            if not reference or reference in seen:
                continue
            seen.add(reference)
            item: dict[str, Any] = {
                "recipe": recipe["id"],
                "model": reference,
                "status": "passed",
                "samples": [],
                "errors": [],
            }
            try:
                probe = probes.get(catalog.key(reference), {})
                if probe.get("status") != "passed":
                    raise ValueError("reference ONNX session probe failed")
                supported, reason = production_compatibility(probe)
                if not supported:
                    if _record_requires_production(catalog, recipe, catalog.key(reference)):
                        raise ValueError("production reference head differs from declared contract")
                    item["status"] = "load_only"
                    item["reason"] = reason
                    item["contract"] = probe
                    item["scope"] = "historical session construction; no frame replay"
                    if reference_root is not None:
                        baseline_path = reference_root / catalog.key(reference)
                        expected = catalog.entries[catalog.key(reference)]["sha256"]
                        item["reference_probe"] = probe_onnx(baseline_path, expected)
                        item["reference_bytes_equal"] = True
                    result["models"].append(item)
                    continue
                model, kind = load_production_model(catalog, recipe, reference)
                baseline = None
                if reference_root is not None:
                    baseline, _ = load_production_model(
                        catalog, recipe, reference, reference_root=reference_root
                    )
                for name, frame in frames:
                    if kind == "classification" and model.expected_source_size is not None:
                        if list(frame.shape[1::-1]) != list(model.expected_source_size):
                            # Resize would change the ROI contract. Keep a visible skip.
                            item["samples"].append(
                                {
                                    "sample": name,
                                    "status": "skipped",
                                    "reason": "source size differs from recorded ROI contract",
                                }
                            )
                            continue
                    prediction = model_prediction(model, kind, frame)
                    check = {"passed": True, "comparison": "not requested"}
                    if baseline is not None:
                        check = compare_predictions(
                            model_prediction(baseline, kind, frame), prediction, kind
                        )
                    item["samples"].append(
                        {
                            "sample": name,
                            "status": "passed" if check["passed"] else "failed",
                            "prediction": prediction,
                            "comparison": check,
                        }
                    )
                    if not check["passed"]:
                        item["errors"].append(f"relocation output differs: {name}")
                if not any(row["status"] != "skipped" for row in item["samples"]):
                    item["errors"].append(
                        "model loaded but no compatible development frame was replayed"
                    )
            except Exception as exc:  # A model failure is report data, never a false pass.
                item["errors"].append(f"{type(exc).__name__}: {exc}")
            if item["errors"]:
                item["status"] = "failed"
                result["errors"].extend(f"{reference}: {error}" for error in item["errors"])
            result["models"].append(item)
    if not seen:
        result["errors"].append("no reference ONNX models were recorded")
    if catalog.data.get("replay_videos"):
        result["video_events"] = replay_video_events(catalog, reference_root)
        result["errors"].extend(result["video_events"]["errors"])
        result["warnings"].extend(result["video_events"]["warnings"])
    if result["errors"]:
        result["status"] = "failed"
    return result


def verify_bundle(
    bundle: Path, *, replay: bool = False, reference_root: Path | None = None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "bundle": str(bundle.resolve()),
        "status": "passed",
        "errors": [],
        "warnings": [],
        "files": {"verified": 0, "total": 0},
        "datasets": [],
        "recipe_fingerprints": [],
        "replay": {"status": "not_run", "reason": "--replay not supplied"},
        "limitations": [
            "Byte integrity is not training reproducibility or model accuracy.",
            "No camera, cloud API, or platform acceptance is performed.",
            "Final/holdout media are byte-checked only, never decoded or inferred.",
        ],
    }
    try:
        catalog_path = safe_file(bundle.resolve(), "provenance/catalog.json")
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not data.get("original_root"):
            raise ValueError("unsupported catalog schema or missing original_root")
        if not isinstance(data.get("files"), list) or not data["files"]:
            raise ValueError("catalog files must be a nonempty list")
        catalog = Catalog(bundle, data)
        report["files"]["total"] = len(catalog.entries)
        for key, entry in catalog.entries.items():
            try:
                path = catalog.path(key)
                if path.stat().st_size != entry["size"]:
                    raise ValueError(f"size mismatch: {key}")
                if catalog.digest(path) != entry["sha256"]:
                    raise ValueError(f"file SHA mismatch: {key}")
                report["files"]["verified"] += 1
            except (OSError, ValueError, TypeError, KeyError) as exc:
                report["errors"].append(str(exc))
        for dataset in data.get("datasets", []):
            result = verify_dataset(catalog, dataset)
            report["datasets"].append(result)
            report["errors"].extend(f"dataset {result['id']}: {e}" for e in result["errors"])
            report["warnings"].extend(f"dataset {result['id']}: {e}" for e in result["warnings"])
        # The recipe's main chain cannot be waived by historical notes.
        dataset_ids = {row["id"] for row in data.get("datasets", [])}
        for recipe in data.get("recipes", []):
            fingerprint_result = verify_recipe_fingerprint(catalog, recipe)
            report["recipe_fingerprints"].append(fingerprint_result)
            if fingerprint_result["required"]:
                report["errors"].extend(
                    f"recipe {recipe['id']} fingerprint: {error}"
                    for error in fingerprint_result["errors"]
                )
            for field in (
                "record",
                "script",
                "source_weights",
                "reference_weights",
                "reference_onnx",
            ):
                values = recipe.get(field) or []
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    try:
                        catalog.path(value)
                    except (OSError, ValueError, TypeError) as exc:
                        report["errors"].append(f"recipe {recipe['id']} {field}: {exc}")
            if recipe.get("dataset_id") and recipe["dataset_id"] not in dataset_ids:
                report["errors"].append(f"recipe {recipe['id']}: unknown dataset_id")
        if replay:
            if report["errors"]:
                report["replay"] = {"status": "not_run", "reason": "integrity checks failed"}
            else:
                report["replay"] = replay_bundle(catalog, reference_root=reference_root)
                report["errors"].extend(report["replay"]["errors"])
                report["warnings"].extend(report["replay"].get("warnings", []))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report["errors"].append(str(exc))
    report["status"] = "failed" if report["errors"] else "passed"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = verify_bundle(args.bundle, replay=args.replay, reference_root=args.reference_root)
    output = args.output or args.bundle / "reports/verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "errors": len(report["errors"]),
                "warnings": len(report["warnings"]),
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
