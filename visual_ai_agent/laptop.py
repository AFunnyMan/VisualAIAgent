"""Fail-closed laptop lid inference for an explicitly accepted local capability.

The lid classifier is never treated as a presence detector.  Production loading
requires a separate presence classifier and a capability manifest recording
independent absent and occlusion checks.  Without that contract the runtime keeps
this feature unavailable, even when a binary lid model is present on disk.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from .behavior import OnnxClassifier, load_behavior_manifest
from .behavior_models import LaptopEvent, LaptopObservation
from .behavior_preprocess import validate_roi
from .laptop_state import LaptopTimeline
from .vision import Frame, SharedFrameSource

LAPTOP_INTERVAL = 0.1
LAPTOP_STALE_AFTER = 0.25


class LaptopCapabilityError(ValueError):
    """The configured artifacts cannot safely establish laptop lid state."""


class Classifier(Protocol):
    def predict(self, frame: Frame) -> dict[str, Any]: ...


class PresenceGate(Protocol):
    model_version: str
    qualified: bool

    def predict(self, frame: Frame) -> dict[str, Any]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _resolved(manifest: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise LaptopCapabilityError(f"{field} is required")
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else manifest.parent / candidate).resolve()


def _load_presence_manifest(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaptopCapabilityError("presence manifest is unreadable") from exc
    preprocessing = record.get("preprocessing", {})
    expected = {
        "layout": "NCHW",
        "dtype": "float32",
        "color": "RGB",
        "resize": "aspect-ratio-preserving letterbox",
        "range": [0.0, 1.0],
        "fill_rgb": [114, 114, 114],
    }
    raw_names = record.get("dataset", {}).get("classes")
    try:
        names = {int(key): str(value) for key, value in raw_names.items()}
    except (AttributeError, TypeError, ValueError) as exc:
        raise LaptopCapabilityError("presence class mapping is invalid") from exc
    try:
        roi = validate_roi(preprocessing.get("roi"))
    except ValueError as exc:
        raise LaptopCapabilityError("presence ROI is invalid") from exc
    source_size = preprocessing.get("expected_source_frame_size")
    valid_source_size = (
        isinstance(source_size, list)
        and len(source_size) == 2
        and all(
            not isinstance(value, bool) and isinstance(value, int) and value > 0
            for value in source_size
        )
    )
    if (
        record.get("status") != "completed"
        or any(preprocessing.get(key) != value for key, value in expected.items())
        or set(raw_names) != {"0", "1", "2"}
        or len(raw_names) != 3
        or set(names) != set(range(len(names)))
        or set(names.values()) != {"present", "absent", "occluded"}
        or len(names) != 3
        or len(set(names.values())) != 3
        or not valid_source_size
        or isinstance(preprocessing.get("imgsz"), bool)
        or not isinstance(preprocessing.get("imgsz"), int)
        or preprocessing["imgsz"] <= 0
    ):
        raise LaptopCapabilityError("presence manifest is incomplete or incompatible")
    onnx = _resolved(path, record.get("onnx"), "presence onnx")
    if not onnx.is_file() or _sha256(onnx) != record.get("onnx_sha256"):
        raise LaptopCapabilityError("presence model SHA mismatch")
    return {
        **record,
        "_path": path,
        "_onnx": onnx,
        "_names": names,
        "_roi": roi,
        "_source_size": source_size,
    }


def load_laptop_capability_manifest(
    path: Path,
    *,
    expected_scene_id: str | None = None,
    expected_source_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Load only an independently accepted state-plus-presence capability."""

    path = Path(path).expanduser().resolve()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaptopCapabilityError("laptop capability manifest is unreadable") from exc
    state_path = _resolved(path, record.get("state_manifest"), "state_manifest")
    presence_path = _resolved(path, record.get("presence_manifest"), "presence_manifest")
    acceptance_path = _resolved(path, record.get("acceptance_report"), "acceptance_report")
    bound_files = (
        (state_path, record.get("state_manifest_sha256")),
        (presence_path, record.get("presence_manifest_sha256")),
        (acceptance_path, record.get("acceptance_report_sha256")),
    )
    if any(not item.is_file() or _sha256(item) != digest for item, digest in bound_files):
        raise LaptopCapabilityError("laptop capability artifact binding failed")
    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaptopCapabilityError("laptop acceptance report is unreadable") from exc
    validation = acceptance.get("results", {})
    integer_fields = (
        "absent_cases",
        "occluded_cases",
        "absent_false_present",
        "occluded_false_present",
    )
    counts = {name: validation.get(name) for name in integer_fields}
    if (
        record.get("schema_version") != 1
        or record.get("capability") != "laptop_lid_state_with_presence"
        or record.get("status") != "accepted"
        or record.get("experimental") is not True
        or acceptance.get("schema_version") != 1
        or acceptance.get("capability") != "laptop_lid_state_with_presence"
        or acceptance.get("status") != "accepted"
        or acceptance.get("independent") is not True
        or acceptance.get("state_transitions_accepted") is not True
        or acceptance.get("thresholds")
        != {
            "presence_confidence": 0.75,
            "state_confidence": 0.75,
            "state_margin": 0.2,
        }
        or acceptance.get("timing")
        != {
            "confirm_seconds": 0.5,
            "max_gap": 0.25,
            "visible_transition_bridge_seconds": 0.15,
        }
        or any(isinstance(value, bool) or not isinstance(value, int) for value in counts.values())
        or counts["absent_cases"] < 1
        or counts["occluded_cases"] < 1
        or counts["absent_false_present"] != 0
        or counts["occluded_false_present"] != 0
    ):
        raise LaptopCapabilityError("laptop capability has not passed presence acceptance")
    model_version = record.get("model_version")
    scene_id = record.get("scene_id")
    if (
        not isinstance(model_version, str)
        or not model_version.strip()
        or not isinstance(scene_id, str)
        or not scene_id.strip()
    ):
        raise LaptopCapabilityError("laptop capability model_version is required")
    try:
        state = load_behavior_manifest(state_path, "laptop")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise LaptopCapabilityError("laptop state manifest is not loadable") from exc
    presence = _load_presence_manifest(presence_path)
    state_preprocessing = state.get("preprocessing", {})
    try:
        state_roi = validate_roi(state_preprocessing.get("roi"))
    except ValueError as exc:
        raise LaptopCapabilityError("laptop state ROI is invalid") from exc
    state_source_size = state_preprocessing.get("expected_source_frame_size")
    source_size = record.get("expected_source_frame_size")
    if (
        not isinstance(source_size, list)
        or len(source_size) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in source_size
        )
        or state_source_size != source_size
        or presence["_source_size"] != source_size
        or acceptance.get("expected_source_frame_size") != source_size
        or acceptance.get("state_roi") != state_preprocessing.get("roi")
        or acceptance.get("presence_roi") != presence["preprocessing"].get("roi")
        or acceptance.get("scene_id") != scene_id
        or (expected_scene_id is not None and scene_id != expected_scene_id)
        or (
            expected_source_size is not None
            and tuple(source_size) != tuple(expected_source_size)
        )
    ):
        raise LaptopCapabilityError("laptop capability does not match scene geometry")
    if (
        acceptance.get("model_version") != model_version
        or acceptance.get("state_manifest_sha256") != record.get("state_manifest_sha256")
        or acceptance.get("presence_manifest_sha256") != record.get("presence_manifest_sha256")
        or acceptance.get("state_onnx_sha256") != state.get("onnx_sha256")
        or acceptance.get("presence_onnx_sha256") != presence.get("onnx_sha256")
        or not _is_sha256(record.get("state_manifest_sha256"))
        or not _is_sha256(record.get("presence_manifest_sha256"))
        or not _is_sha256(record.get("acceptance_report_sha256"))
        or not _is_sha256(acceptance.get("source_registry_sha256"))
        or not _is_sha256(acceptance.get("truth_sha256"))
    ):
        raise LaptopCapabilityError("laptop acceptance report does not bind evaluated inputs")
    return {
        **record,
        "_path": path,
        "_state": state,
        "_presence": presence,
        "_acceptance": acceptance,
        "_state_roi": state_roi,
        "_scene_id": scene_id,
        "_model_version": model_version.strip(),
    }


class ClassifierPresenceGate:
    """Adapter for the accepted three-way present/absent/occluded classifier."""

    qualified = True

    def __init__(self, classifier: Classifier, model_version: str) -> None:
        self.classifier = classifier
        self.model_version = model_version

    def predict(self, frame: Frame) -> dict[str, Any]:
        result = self.classifier.predict(frame)
        label = result.get("label", "unknown")
        return {
            "visible": label == "present",
            "occluded": label == "occluded",
            "confidence": result.get("confidence"),
            "label": label,
        }


class LaptopDetector:
    """Apply presence first; only a verified visible laptop reaches lid inference."""

    def __init__(
        self,
        state_classifier: Classifier,
        presence_gate: PresenceGate,
        *,
        timeline: LaptopTimeline | None = None,
    ) -> None:
        if getattr(presence_gate, "qualified", False) is not True:
            raise LaptopCapabilityError("laptop presence gate is not qualified")
        self.state_classifier = state_classifier
        self.presence_gate = presence_gate
        self.timeline = timeline or LaptopTimeline()

    def observe(
        self,
        frame: Frame,
        timestamp: float,
        *,
        fresh: bool = True,
        scene_id: str = "default",
    ) -> dict[str, Any]:
        if not fresh:
            state = self.timeline.observe(timestamp, "unknown", fresh=False, scene_id=scene_id)
            return {
                "state": state.state,
                "state_reason": state.reason,
                "events": [],
                "presence_verified": False,
                "presence_confidence": None,
                "occluded": False,
            }
        gate = self.presence_gate.predict(frame)
        confidence = gate.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            confidence = None
        visible = (
            gate.get("visible") is True
            and gate.get("label") == "present"
            and gate.get("occluded") is False
            and confidence is not None
            and float(confidence) >= 0.75
        )
        occluded = gate.get("occluded") is True
        label = "unknown"
        prediction: dict[str, Any] | None = None
        if visible and not occluded:
            prediction = self.state_classifier.predict(frame)
            candidate = prediction.get("label")
            state_confidence = prediction.get("confidence")
            state_margin = prediction.get("margin")
            if (
                candidate in {"open", "closed"}
                and isinstance(state_confidence, (int, float))
                and not isinstance(state_confidence, bool)
                and math.isfinite(float(state_confidence))
                and 0 <= float(state_confidence) <= 1
                and float(state_confidence) >= 0.75
                and isinstance(state_margin, (int, float))
                and not isinstance(state_margin, bool)
                and math.isfinite(float(state_margin))
                and 0 <= float(state_margin) <= 1
                and float(state_margin) >= 0.2
            ):
                label = candidate
        state = self.timeline.observe(
            timestamp,
            label,
            fresh=True,
            scene_id=scene_id,
            visibility_verified=visible and not occluded,
        )
        return {
            "state": state.state,
            "state_reason": state.reason,
            "events": [state.event] if state.event else [],
            "presence_verified": visible and not occluded,
            "presence_confidence": float(confidence) if confidence is not None else None,
            "occluded": occluded,
            "presence": gate,
            "prediction": prediction,
        }

    def close(self) -> None:
        for component in (self.state_classifier, self.presence_gate):
            close = getattr(component, "close", None)
            if callable(close):
                close()


def detector_from_capability(record: dict[str, Any]) -> tuple[LaptopDetector, str, str]:
    state = OnnxClassifier(record["_state"])
    presence_classifier = OnnxClassifier(record["_presence"])
    presence_version = f"presence:{record['_presence']['onnx_sha256'][:12]}"
    gate = ClassifierPresenceGate(presence_classifier, presence_version)
    version = f"{record['_model_version']}:state={record['_state']['onnx_sha256'][:12]}"
    return LaptopDetector(state, gate), version, presence_version


class LaptopWorker:
    """Independent latest-only consumer of the process-owned shared camera."""

    def __init__(
        self,
        detector: LaptopDetector,
        source: SharedFrameSource,
        callback,
        *,
        model_version: str,
        presence_model_version: str,
        scene_id: str,
        interval: float = LAPTOP_INTERVAL,
        stale_after: float = LAPTOP_STALE_AFTER,
    ) -> None:
        if interval <= 0 or stale_after <= 0:
            raise ValueError("laptop timing limits must be positive")
        self.detector = detector
        self.source = source
        self.callback = callback
        self.model_version = model_version
        self.presence_model_version = presence_model_version
        self.scene_id = scene_id
        self.interval = interval
        self.stale_after = stale_after
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="laptop-inference", daemon=True)
        self._thread.start()

    def _observation(
        self,
        sample,
        *,
        status: str = "running",
        fresh: bool = True,
        state: str = "unknown",
        state_reason: str | None = None,
        presence_verified: bool = False,
        presence_confidence: float | None = None,
        occluded: bool = False,
        events: list[LaptopEvent] | None = None,
        error: str | None = None,
    ) -> LaptopObservation:
        return LaptopObservation(
            observed_at=sample.captured_at if sample is not None else datetime.now(UTC),
            monotonic_at=sample.monotonic_at if sample is not None else max(0.0, time.monotonic()),
            status=status,
            fresh=fresh,
            presence_verified=presence_verified,
            presence_confidence=presence_confidence,
            occluded=occluded,
            state=state,
            state_reason=state_reason,
            events=events or [],
            model_version=self.model_version,
            presence_model_version=self.presence_model_version,
            scene_id=self.scene_id,
            source=self.source.source_name,
            error=error,
        )

    def _fault(self, sample, status: str, error: str) -> None:
        timestamp = sample.monotonic_at if sample is not None else max(0.0, time.monotonic())
        self.detector.observe(
            np.empty((0, 0, 3), dtype=np.uint8),
            timestamp,
            fresh=False,
            scene_id=self.scene_id,
        )
        self.callback(
            self._observation(sample, status=status, fresh=False, error=error),
            None,
        )

    def _run(self) -> None:
        next_run = time.monotonic()
        try:
            self.source.open()
            while not self._stop.is_set():
                delay = next_run - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
                next_run = max(next_run + self.interval, time.monotonic())
                sample = self.source.read_sample()
                if sample is None:
                    status = self.source.status
                    if status not in {"stopped", "paused", "disconnected", "error"}:
                        status = "stale"
                    self._fault(None, status, self.source.last_error or "No new frame available")
                    continue
                if time.monotonic() - sample.monotonic_at > self.stale_after:
                    self._fault(sample, "stale", "Latest frame is stale")
                    continue
                try:
                    result = self.detector.observe(
                        sample.frame, sample.monotonic_at, scene_id=self.scene_id
                    )
                    if time.monotonic() - sample.monotonic_at > self.stale_after:
                        self._fault(sample, "stale", "Laptop inference exceeded freshness limit")
                        continue
                    events = [
                        LaptopEvent(
                            kind=kind,
                            state=result["state"],
                            observed_at=sample.captured_at,
                            confirmed_at=sample.captured_at,
                            model_version=self.model_version,
                            presence_model_version=self.presence_model_version,
                            scene_id=self.scene_id,
                            source=self.source.source_name,
                        )
                        for kind in result["events"]
                    ]
                    jpeg = None
                    if events:
                        ok, encoded = cv2.imencode(
                            ".jpg", sample.frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
                        )
                        jpeg = encoded.tobytes() if ok else None
                    if time.monotonic() - sample.monotonic_at > self.stale_after:
                        self._fault(sample, "stale", "Laptop result expired before publication")
                        continue
                    self.callback(
                        self._observation(
                            sample,
                            state=result["state"],
                            state_reason=result["state_reason"],
                            presence_verified=result["presence_verified"],
                            presence_confidence=result["presence_confidence"],
                            occluded=result["occluded"],
                            events=events,
                        ),
                        jpeg,
                    )
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f"Laptop inference failed ({type(exc).__name__})"
                    self._fault(sample, "error", self.last_error)
        except Exception as exc:
            self.last_error = f"Laptop worker failed ({type(exc).__name__})"
            self._fault(None, "error", self.last_error)
        finally:
            self.source.close()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        if thread is not None and not thread.is_alive():
            self.detector.close()
            self._thread = None
