"""Local camera/replay capture and YOLO26 ONNX inference.

The runtime deliberately has no dependency on Ultralytics or PyTorch.  Model
export is handled by ``scripts/prepare_model.py`` in a separate environment.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import cv2
import numpy as np

# ORT 1.29 on macOS otherwise writes a telemetry device-id cache during import.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
import onnxruntime as ort
import supervision as sv
from numpy.typing import NDArray

from visual_ai_agent.models import CATEGORIES, CameraStatus, Detection, SceneObservation

if hasattr(ort, "disable_telemetry_events"):
    ort.disable_telemetry_events()

Frame = NDArray[np.uint8]
SourceName = Literal["camera", "replay", "test"]

COCO_CLASS_NAMES: dict[int, str] = {
    39: "bottle",
    41: "cup",
    67: "cell phone",
}
TARGET_CLASS_IDS = {name: class_id for class_id, name in COCO_CLASS_NAMES.items()}


class FrameSource(Protocol):
    """A pull-based source used by the capture thread."""

    source_name: SourceName
    status: CameraStatus
    last_error: str | None

    def open(self) -> None: ...

    def read(self) -> Frame | None: ...

    def close(self) -> None: ...


class Detector(Protocol):
    def detect(self, frame_bgr: Frame) -> list[Detection]: ...


class CameraSource:
    """OpenCV camera input with explicit status and obtained dimensions."""

    source_name: SourceName = "camera"

    def __init__(
        self,
        device_index: int = 0,
        width: int = 640,
        height: int = 480,
        backend: int | None = None,
    ) -> None:
        self.device_index = device_index
        self.requested_width = width
        self.requested_height = height
        self.backend = backend
        self.status: CameraStatus = "stopped"
        self.last_error: str | None = None
        self.actual_width = 0
        self.actual_height = 0
        self.backend_name: str | None = None
        self._capture: cv2.VideoCapture | None = None

    def open(self) -> None:
        self.close()
        capture = (
            cv2.VideoCapture(self.device_index)
            if self.backend is None
            else cv2.VideoCapture(self.device_index, self.backend)
        )
        if not capture.isOpened():
            capture.release()
            self.status = "error"
            self.last_error = f"Unable to open camera device {self.device_index}"
            raise RuntimeError(self.last_error)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
        self.actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        try:
            self.backend_name = capture.getBackendName()
        except cv2.error:
            self.backend_name = None
        self._capture = capture
        self.status = "running"
        self.last_error = None

    def read(self) -> Frame | None:
        if self._capture is None or not self._capture.isOpened():
            self.status = "disconnected"
            self.last_error = "Camera is not open"
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None or frame.size == 0:
            self.status = "disconnected"
            self.last_error = "Camera frame read failed"
            return None
        self.status = "running"
        self.last_error = None
        return cast(Frame, frame)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
        self.status = "stopped"


class ReplaySource:
    """OpenCV video replay for deterministic operation without a camera."""

    source_name: SourceName = "replay"

    def __init__(self, path: str | Path, loop: bool = False, realtime: bool = False) -> None:
        self.path = Path(path)
        self.loop = loop
        self.realtime = realtime
        self.status: CameraStatus = "stopped"
        self.last_error: str | None = None
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self._capture: cv2.VideoCapture | None = None
        self._next_frame_at: float | None = None

    def open(self) -> None:
        self.close()
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            self.status = "error"
            self.last_error = f"Unable to open replay: {self.path}"
            raise RuntimeError(self.last_error)
        self._capture = capture
        self.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = float(capture.get(cv2.CAP_PROP_FPS))
        self._next_frame_at = time.monotonic()
        self.status = "running"
        self.last_error = None

    def read(self) -> Frame | None:
        capture = self._capture
        if capture is None:
            self.status = "stopped"
            self.last_error = "Replay is not open"
            return None
        if self.realtime and self.fps > 0 and self._next_frame_at is not None:
            delay = self._next_frame_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next_frame_at = max(self._next_frame_at + 1.0 / self.fps, time.monotonic())
        ok, frame = capture.read()
        if not ok and self.loop:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            self.status = "stopped"
            self.last_error = None
            return None
        self.status = "running"
        self.last_error = None
        return cast(Frame, frame)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
        self.status = "stopped"


@dataclass(frozen=True, slots=True)
class LetterboxTransform:
    scale: float
    pad_left: int
    pad_top: int
    input_width: int
    input_height: int
    original_width: int
    original_height: int


def letterbox(frame: Frame, size: int = 640) -> tuple[NDArray[np.float32], LetterboxTransform]:
    """Resize with unchanged aspect ratio and gray padding, returning NCHW RGB."""
    if frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
        raise ValueError("Expected a non-empty HxWx3 BGR frame")
    height, width = frame.shape[:2]
    scale = min(size / width, size / height)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_width = size - resized_width
    pad_height = size - resized_height
    pad_left = pad_width // 2
    pad_top = pad_height // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[pad_top : pad_top + resized_height, pad_left : pad_left + resized_width] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    transform = LetterboxTransform(
        scale=scale,
        pad_left=pad_left,
        pad_top=pad_top,
        input_width=size,
        input_height=size,
        original_width=width,
        original_height=height,
    )
    return blob, transform


def inverse_letterbox(
    boxes: NDArray[np.floating], transform: LetterboxTransform
) -> NDArray[np.float32]:
    """Map model xyxy boxes back to the source frame and clip them."""
    mapped = np.asarray(boxes, dtype=np.float32).reshape(-1, 4).copy()
    mapped[:, [0, 2]] = (mapped[:, [0, 2]] - transform.pad_left) / transform.scale
    mapped[:, [1, 3]] = (mapped[:, [1, 3]] - transform.pad_top) / transform.scale
    mapped[:, [0, 2]] = mapped[:, [0, 2]].clip(0, transform.original_width)
    mapped[:, [1, 3]] = mapped[:, [1, 3]].clip(0, transform.original_height)
    return mapped


def region_for_box(box: tuple[float, float, float, float], frame_width: int) -> str:
    """Classify a box by its horizontal center without mirroring coordinates."""
    center_x = (box[0] + box[2]) / 2
    if center_x < frame_width / 3:
        return "left"
    if center_x < 2 * frame_width / 3:
        return "center"
    return "right"


def _parse_names(raw: str | None) -> Mapping[int, str] | None:
    if not raw:
        return None
    value: object
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return None
    if isinstance(value, list):
        return {index: str(name) for index, name in enumerate(value)}
    if isinstance(value, dict):
        try:
            return {int(key): str(name) for key, name in value.items()}
        except (TypeError, ValueError):
            return None
    return None


def _verify_model_manifest(
    model_path: Path, manifest_path: Path, configured_sha256: str | None = None
) -> dict[str, object]:
    if not model_path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {model_path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Model manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Unable to read model manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Model manifest root must be an object")
    if manifest.get("file") != model_path.name:
        raise ValueError(
            f"Manifest file {manifest.get('file')!r} does not match {model_path.name!r}"
        )
    if manifest.get("end_to_end") is not True:
        raise ValueError("Manifest must identify an end-to-end YOLO26 export")
    if manifest.get("target_classes") != {
        "39": "bottle",
        "41": "cup",
        "67": "cell phone",
    }:
        raise ValueError("Manifest target classes do not match the required COCO mapping")
    expected_hash = manifest.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("Manifest sha256 must be a 64-character hexadecimal digest")
    if configured_sha256 is not None and not hmac.compare_digest(
        configured_sha256.lower(), expected_hash.lower()
    ):
        raise ValueError("Configured model SHA-256 does not match the committed manifest")
    actual_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash.lower()):
        raise ValueError(
            f"ONNX SHA-256 mismatch: expected {expected_hash.lower()}, got {actual_hash}"
        )
    return cast(dict[str, object], manifest)


class YoloOnnxDetector:
    """Strict adapter for a static YOLO26 end-to-end ONNX detection model."""

    def __init__(
        self,
        model_path: str | Path,
        confidence: float = 0.35,
        input_size: int = 640,
        intra_op_threads: int = 2,
        manifest_path: str | Path | None = None,
        verify_manifest: bool = True,
        expected_sha256: str | None = None,
    ) -> None:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if intra_op_threads < 1:
            raise ValueError("intra_op_threads must be positive")
        model_path = Path(model_path)
        if verify_manifest:
            if manifest_path is None:
                repository_root = Path(__file__).resolve().parent.parent
                manifest_path = repository_root / "model_manifests" / f"{model_path.name}.json"
            manifest = _verify_model_manifest(model_path, Path(manifest_path), expected_sha256)
            manifest_input_size = manifest.get("input_size")
            if manifest_input_size != input_size:
                raise ValueError(
                    f"Manifest input_size {manifest_input_size!r} does not match {input_size}"
                )
        elif expected_sha256 is not None:
            actual_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
            if not hmac.compare_digest(actual_hash, expected_sha256.lower()):
                raise ValueError(
                    f"ONNX SHA-256 mismatch: expected {expected_sha256.lower()}, got {actual_hash}"
                )
        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_op_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("Expected exactly one ONNX input and one output")
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name
        if outputs[0].shape != [1, 300, 6]:
            raise ValueError(
                f"Expected static YOLO26 end-to-end output [1, 300, 6], got {outputs[0].shape}"
            )
        shape = inputs[0].shape
        if len(shape) != 4 or shape[0] not in (1, "batch", None) or shape[1] != 3:
            raise ValueError(f"Unexpected ONNX input shape: {shape}")
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            if shape[2] != shape[3]:
                raise ValueError(f"Expected square ONNX input, got {shape}")
            input_size = shape[2]
        self.input_size = input_size
        self.confidence = confidence
        metadata = self.session.get_modelmeta().custom_metadata_map
        names = _parse_names(metadata.get("names"))
        if names is None:
            raise ValueError("ONNX model metadata does not contain parseable class names")
        mismatches = {
            class_id: (expected, names.get(class_id))
            for class_id, expected in COCO_CLASS_NAMES.items()
            if names.get(class_id) != expected
        }
        if mismatches:
            raise ValueError(f"Model metadata does not match expected COCO classes: {mismatches}")
        self.class_names = names

    def detect(self, frame_bgr: Frame) -> list[Detection]:
        tensor, transform = letterbox(frame_bgr, self.input_size)
        raw = self.session.run([self.output_name], {self.input_name: tensor})[0]
        predictions = np.asarray(raw)
        if predictions.ndim == 3 and predictions.shape[0] == 1:
            predictions = predictions[0]
        if predictions.ndim != 2 or predictions.shape[1] != 6:
            raise ValueError(
                "Expected YOLO26 end-to-end output shaped (1, N, 6) as "
                f"xyxy/conf/class, got {tuple(np.asarray(raw).shape)}"
            )
        finite = np.isfinite(predictions).all(axis=1)
        selected = predictions[finite & (predictions[:, 4] >= self.confidence)]
        if selected.size == 0:
            return []
        boxes = inverse_letterbox(selected[:, :4], transform)
        detections: list[Detection] = []
        for row, box_array in zip(selected, boxes, strict=True):
            class_value = float(row[5])
            class_id = int(round(class_value))
            if abs(class_value - class_id) > 1e-3:
                raise ValueError(f"Non-integral class id in ONNX output: {class_value}")
            category = self.class_names.get(class_id)
            if category not in CATEGORIES:
                continue
            x1, y1, x2, y2 = (float(value) for value in box_array)
            if x2 <= x1 or y2 <= y1:
                continue
            box = (x1, y1, x2, y2)
            detections.append(
                Detection(
                    category=category,
                    confidence=float(row[4]),
                    bbox=box,
                    region=region_for_box(box, transform.original_width),
                )
            )
        return sorted(detections, key=lambda detection: detection.confidence, reverse=True)


def annotate_frame(frame: Frame, detections: list[Detection]) -> Frame:
    """Draw local detections with Supervision, preserving all same-class candidates."""
    if not detections:
        return frame.copy()
    xyxy = np.asarray([detection.bbox for detection in detections], dtype=np.float32)
    confidence = np.asarray([detection.confidence for detection in detections], dtype=np.float32)
    class_id = np.asarray([CATEGORIES.index(detection.category) for detection in detections])
    sv_detections = sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
    labels = [
        f"{detection.category} {detection.confidence:.2f} ({detection.region})"
        for detection in detections
    ]
    annotated = sv.BoxAnnotator().annotate(scene=frame.copy(), detections=sv_detections)
    return cast(
        Frame,
        sv.LabelAnnotator().annotate(scene=annotated, detections=sv_detections, labels=labels),
    )


@dataclass(slots=True)
class _LatestFrame:
    sequence: int
    frame: Frame
    captured_at: datetime
    monotonic_at: float


ObservationCallback = Callable[[SceneObservation, bytes | None], None]


class VisionWorker:
    """Two-thread latest-frame capture and low-frequency inference worker."""

    def __init__(
        self,
        detector: Detector,
        source: FrameSource,
        on_observation: ObservationCallback,
        inference_interval: float = 1.0,
        stale_after: float = 2.5,
        jpeg_quality: int = 85,
    ) -> None:
        if inference_interval <= 0:
            raise ValueError("inference_interval must be positive")
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        if not 0 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 0 and 100")
        self.detector = detector
        self.source = source
        self.on_observation = on_observation
        self.inference_interval = inference_interval
        self.stale_after = stale_after
        self.jpeg_quality = jpeg_quality
        self.last_callback_error: str | None = None
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest: _LatestFrame | None = None
        self._snapshot: tuple[SceneObservation | None, bytes | None] = (None, None)
        self._sequence = 0
        self._capture_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return any(
            thread is not None and thread.is_alive()
            for thread in (self._capture_thread, self._inference_thread)
        )

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._latest = None
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="vision-capture", daemon=True
        )
        self._inference_thread = threading.Thread(
            target=self._inference_loop, name="vision-inference", daemon=True
        )
        self._capture_thread.start()
        self._inference_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._capture_thread is None and self._inference_thread is None:
            return
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        close_error: str | None = None
        try:
            # Releasing a native capture is the only available way to interrupt a blocked read.
            self.source.close()
        except Exception as exc:
            close_error = f"Source close failed: {exc}"
        deadline = time.monotonic() + timeout
        for thread in (self._capture_thread, self._inference_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        alive = [
            thread.name
            for thread in (self._capture_thread, self._inference_thread)
            if thread is not None and thread.is_alive()
        ]
        if alive:
            detail = f"Vision worker did not stop within {timeout:.3f}s: {', '.join(alive)}"
            if close_error:
                detail = f"{detail}; {close_error}"
            self._publish(self._status_observation("error", detail), None)
            # Keep the handles: start() must not create a second worker over blocked threads.
            return
        self._capture_thread = None
        self._inference_thread = None
        previous, _ = self.snapshot()
        status: CameraStatus = "error" if close_error else "stopped"
        observation = self._status_observation(status, close_error, previous)
        self._publish(observation, None)

    def snapshot(self) -> tuple[SceneObservation | None, bytes | None]:
        with self._condition:
            observation, jpeg = self._snapshot
            return (
                observation.model_copy(deep=True) if observation is not None else None,
                jpeg,
            )

    def _capture_loop(self) -> None:
        try:
            self.source.open()
        except Exception as exc:
            self.source.status = "error"
            self.source.last_error = str(exc)
            with self._condition:
                self._condition.notify_all()
            return
        try:
            while not self._stop.is_set():
                frame = self.source.read()
                if frame is None:
                    with self._condition:
                        self._condition.notify_all()
                    if self.source.status == "stopped":
                        return
                    self._stop.wait(0.1)
                    continue
                now_monotonic = time.monotonic()
                with self._condition:
                    self._sequence += 1
                    self._latest = _LatestFrame(
                        sequence=self._sequence,
                        frame=frame,
                        captured_at=datetime.now(UTC),
                        monotonic_at=now_monotonic,
                    )
                    self._condition.notify_all()
        finally:
            self.source.close()
            with self._condition:
                self._condition.notify_all()

    def _inference_loop(self) -> None:
        next_run = time.monotonic()
        last_sequence = -1
        while not self._stop.is_set():
            wait_for = max(0.0, next_run - time.monotonic())
            with self._condition:
                if self._latest is None and not self._stop.is_set():
                    self._condition.wait(timeout=min(wait_for or 0.1, 0.1))
                latest = self._latest
            if self._stop.is_set():
                return
            now = time.monotonic()
            if now < next_run:
                self._stop.wait(min(next_run - now, 0.1))
                continue
            next_run = now + self.inference_interval
            if latest is None:
                self._publish(self._status_observation_from_source(), None)
                continue
            if self.source.status in ("disconnected", "error", "paused"):
                self._publish(
                    self._frame_status_observation(
                        latest, self.source.status, self.source.last_error
                    ),
                    self._encode_jpeg(latest.frame),
                )
                continue
            age = now - latest.monotonic_at
            if age > self.stale_after:
                jpeg = self._encode_jpeg(latest.frame)
                self._publish(
                    self._frame_status_observation(latest, "stale", "Latest frame is stale"),
                    jpeg,
                )
                continue
            if latest.sequence == last_sequence:
                # A camera/replay that stopped producing frames is unknown, not an empty scene.
                self._publish(
                    self._frame_status_observation(latest, "stale", "No new frame available"),
                    None,
                )
                continue
            last_sequence = latest.sequence
            started = time.perf_counter()
            try:
                detections = self.detector.detect(latest.frame)
                inference_ms = (time.perf_counter() - started) * 1000
                if self._stop.is_set():
                    return
                if self.source.status in ("disconnected", "error", "paused"):
                    self._publish(
                        self._frame_status_observation(
                            latest, self.source.status, self.source.last_error, inference_ms
                        ),
                        self._encode_jpeg(latest.frame),
                    )
                    continue
                if time.monotonic() - latest.monotonic_at > self.stale_after:
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            "stale",
                            "Inference completed after the frame expired",
                            inference_ms,
                        ),
                        self._encode_jpeg(latest.frame),
                    )
                    continue
                rendered = annotate_frame(latest.frame, detections)
                jpeg = self._encode_jpeg(rendered)
                if self._stop.is_set():
                    return
                if self.source.status in ("disconnected", "error", "paused"):
                    self._publish(
                        self._frame_status_observation(
                            latest, self.source.status, self.source.last_error, inference_ms
                        ),
                        self._encode_jpeg(latest.frame),
                    )
                    continue
                if time.monotonic() - latest.monotonic_at > self.stale_after:
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            "stale",
                            "Frame expired before inference results were published",
                            inference_ms,
                        ),
                        self._encode_jpeg(latest.frame),
                    )
                    continue
                observation = SceneObservation(
                    observed_at=latest.captured_at,
                    monotonic_at=latest.monotonic_at,
                    status="running",
                    fresh=True,
                    detections=detections,
                    inference_ms=inference_ms,
                    width=latest.frame.shape[1],
                    height=latest.frame.shape[0],
                    source=self.source.source_name,
                )
                self._publish(observation, jpeg)
            except Exception as exc:
                observation = self._status_observation("error", f"Inference failed: {exc}")
                self._publish(observation, self._encode_jpeg(latest.frame))

    def _status_observation_from_source(self) -> SceneObservation:
        status = self.source.status
        if status == "running":
            status = "stale"
        return self._status_observation(status, self.source.last_error)

    def _frame_status_observation(
        self,
        latest: _LatestFrame,
        status: CameraStatus,
        error: str | None,
        inference_ms: float | None = None,
    ) -> SceneObservation:
        return SceneObservation(
            observed_at=latest.captured_at,
            monotonic_at=latest.monotonic_at,
            status=status,
            fresh=False,
            detections=[],
            error=error,
            inference_ms=inference_ms,
            width=latest.frame.shape[1],
            height=latest.frame.shape[0],
            source=self.source.source_name,
        )

    def _status_observation(
        self,
        status: CameraStatus,
        error: str | None,
        previous: SceneObservation | None = None,
    ) -> SceneObservation:
        return SceneObservation(
            observed_at=datetime.now(UTC),
            monotonic_at=time.monotonic(),
            status=status,
            fresh=False,
            detections=[],
            error=error,
            width=previous.width if previous else 0,
            height=previous.height if previous else 0,
            source=self.source.source_name,
        )

    def _encode_jpeg(self, frame: Frame) -> bytes | None:
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return encoded.tobytes() if ok else None

    def _publish(self, observation: SceneObservation, jpeg: bytes | None) -> None:
        with self._condition:
            self._snapshot = (observation, jpeg)
        try:
            self.on_observation(observation, jpeg)
            self.last_callback_error = None
        except Exception as exc:
            # UI/storage callback failures must not stop capture or inference.
            self.last_callback_error = str(exc)
