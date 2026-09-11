"""Local camera/replay capture and YOLO26 ONNX inference.

The runtime deliberately has no dependency on Ultralytics or PyTorch.  Model
export is handled by ``scripts/prepare_model.py`` in a separate environment.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import math
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
        observation_region: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.device_index = device_index
        self.requested_width = width
        self.requested_height = height
        self.backend = backend
        self.observation_region = _validate_observation_region(observation_region)
        self.status: CameraStatus = "stopped"
        self.last_error: str | None = None
        self.actual_width = 0
        self.actual_height = 0
        self.observation_width = 0
        self.observation_height = 0
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
        left, top, right, bottom = self.region_pixels(self.actual_width, self.actual_height)
        self.observation_width = right - left
        self.observation_height = bottom - top
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
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = self.region_pixels(frame_width, frame_height)
        self.observation_width = right - left
        self.observation_height = bottom - top
        if self.observation_region is None:
            return cast(Frame, frame)
        return cast(Frame, frame[top:bottom, left:right].copy())

    def region_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Return the configured observation bounds for a concrete frame size."""
        if width < 1 or height < 1:
            return (0, 0, max(0, width), max(0, height))
        if self.observation_region is None:
            return (0, 0, width, height)
        left, top, right, bottom = self.observation_region
        x1 = min(width - 1, max(0, math.floor(left * width)))
        y1 = min(height - 1, max(0, math.floor(top * height)))
        x2 = min(width, max(x1 + 1, math.ceil(right * width)))
        y2 = min(height, max(y1 + 1, math.ceil(bottom * height)))
        return (x1, y1, x2, y2)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
        self.status = "stopped"


@dataclass(slots=True)
class SharedFrame:
    """One immutable-by-convention camera sample shared by local consumers."""

    sequence: int
    frame: Frame
    captured_at: datetime
    monotonic_at: float


class SharedCamera:
    """Own one camera read loop and expose independent latest-frame subscriptions.

    Slow consumers only skip frames. They never queue camera frames or delay capture,
    and closing a subscription does not release the physical camera while another
    consumer is active.
    """

    def __init__(self, source: CameraSource) -> None:
        self.source = source
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: SharedFrame | None = None
        self._sequence = 0
        self._subscribers = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def subscribe(self) -> SharedFrameSource:
        return SharedFrameSource(self)

    def _acquire(self) -> None:
        with self._condition:
            self._subscribers += 1
            if self._thread is not None:
                return
            self._stop.clear()
            self._latest = None
            self._thread = threading.Thread(
                target=self._capture_loop, name="vision-camera", daemon=True
            )
            self._thread.start()

    def _release(self, timeout: float = 5.0) -> None:
        with self._condition:
            self._subscribers = max(0, self._subscribers - 1)
            if self._subscribers:
                return
            thread = self._thread
            self._stop.set()
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._condition:
            if thread is not None and not thread.is_alive() and self._thread is thread:
                self._thread = None

    def _capture_loop(self) -> None:
        terminal_error: str | None = None
        try:
            self.source.open()
            while not self._stop.is_set():
                frame = self.source.read()
                if frame is None:
                    with self._condition:
                        self._condition.notify_all()
                    if self.source.status == "stopped":
                        return
                    self._stop.wait(0.05)
                    continue
                with self._condition:
                    self._sequence += 1
                    self._latest = SharedFrame(
                        sequence=self._sequence,
                        frame=frame,
                        captured_at=datetime.now(UTC),
                        monotonic_at=time.monotonic(),
                    )
                    self._condition.notify_all()
        except Exception as exc:
            terminal_error = str(exc)
        finally:
            try:
                self.source.close()
            except Exception as exc:
                terminal_error = f"Source close failed: {exc}"
            if terminal_error is not None:
                self.source.status = "error"
                self.source.last_error = terminal_error
            with self._condition:
                self._condition.notify_all()


class SharedFrameSource:
    """FrameSource view of a SharedCamera for one inference worker."""

    source_name: SourceName = "camera"

    def __init__(self, camera: SharedCamera) -> None:
        self.camera = camera
        self.source_name = camera.source.source_name
        self._opened = False
        self._last_sequence = -1

    @property
    def status(self) -> CameraStatus:
        if self.camera.running and self.camera.source.status == "stopped":
            # The physical source may still be inside a slow open/permission call.
            # Consumers must not interpret that initial state as terminal EOF.
            return "stale"
        return self.camera.source.status

    @status.setter
    def status(self, value: CameraStatus) -> None:
        self.camera.source.status = value

    @property
    def last_error(self) -> str | None:
        return self.camera.source.last_error

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        self.camera.source.last_error = value

    def open(self) -> None:
        if not self._opened:
            self._opened = True
            self.camera._acquire()

    def read_sample(self) -> SharedFrame | None:
        with self.camera._condition:
            self.camera._condition.wait_for(
                lambda: (
                    (
                        self.camera._latest is not None
                        and self.camera._latest.sequence > self._last_sequence
                    )
                    or not self.camera.running
                ),
                timeout=0.25,
            )
            latest = self.camera._latest
            if latest is None or latest.sequence <= self._last_sequence:
                return None
            self._last_sequence = latest.sequence
            return SharedFrame(
                sequence=latest.sequence,
                frame=cast(Frame, latest.frame.copy()),
                captured_at=latest.captured_at,
                monotonic_at=latest.monotonic_at,
            )

    def read(self) -> Frame | None:
        sample = self.read_sample()
        return sample.frame if sample is not None else None

    def close(self) -> None:
        if self._opened:
            self._opened = False
            self.camera._release()


def _validate_observation_region(
    region: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if region is None:
        return None
    if not isinstance(region, tuple) or len(region) != 4:
        raise ValueError("observation_region must be a four-value tuple")
    try:
        left, top, right, bottom = (float(value) for value in region)
    except (TypeError, ValueError) as exc:
        raise ValueError("observation_region values must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise ValueError("observation_region values must be finite numbers")
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(
            "observation_region must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1"
        )
    values = (left, top, right, bottom)
    return None if values == (0.0, 0.0, 1.0, 1.0) else values


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


class CupScaleRecheckDetector:
    """Retry cup detection once with a bounded scale transform."""

    def __init__(self, detector: Detector, scale: float = 0.75) -> None:
        if not 0 < scale < 1:
            raise ValueError("scale must be between 0 and 1")
        self.detector = detector
        self.scale = scale

    def detect(self, frame_bgr: Frame) -> list[Detection]:
        primary = self.detector.detect(frame_bgr)
        if any(detection.category == "cup" for detection in primary):
            return primary

        height, width = frame_bgr.shape[:2]
        resized_width = max(1, round(width * self.scale))
        resized_height = max(1, round(height * self.scale))
        scale_x = resized_width / width
        scale_y = resized_height / height
        offset_x = (width - resized_width) // 2
        offset_y = (height - resized_height) // 2
        resized = cv2.resize(
            frame_bgr,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )
        canvas = np.full_like(frame_bgr, 114)
        canvas[offset_y : offset_y + resized_height, offset_x : offset_x + resized_width] = resized
        retry = self.detector.detect(canvas)

        cups: list[Detection] = []
        content_right = offset_x + resized_width
        content_bottom = offset_y + resized_height
        for detection in retry:
            if detection.category != "cup":
                continue
            x1, y1, x2, y2 = detection.bbox
            x1 = min(content_right, max(offset_x, x1))
            y1 = min(content_bottom, max(offset_y, y1))
            x2 = min(content_right, max(offset_x, x2))
            y2 = min(content_bottom, max(offset_y, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            mapped = (
                max(0.0, min(float(width), (x1 - offset_x) / scale_x)),
                max(0.0, min(float(height), (y1 - offset_y) / scale_y)),
                max(0.0, min(float(width), (x2 - offset_x) / scale_x)),
                max(0.0, min(float(height), (y2 - offset_y) / scale_y)),
            )
            if mapped[2] <= mapped[0] or mapped[3] <= mapped[1]:
                continue
            cups.append(
                Detection(
                    category="cup",
                    confidence=detection.confidence,
                    bbox=mapped,
                    region=region_for_box(mapped, width),
                )
            )
        return sorted([*primary, *cups], key=lambda item: item.confidence, reverse=True)


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
    capture_read_ms: float = 0.0


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
        self.last_callback_ms: float | None = None
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest: _LatestFrame | None = None
        self._snapshot: tuple[SceneObservation | None, bytes | None] = (None, None)
        self._sequence = 0
        self._capture_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None
        self._capture_close_error: str | None = None

    @property
    def running(self) -> bool:
        return any(
            thread is not None and thread.is_alive()
            for thread in (self._capture_thread, self._inference_thread)
        )

    def start(self) -> None:
        # Handles are cleared only by a completed stop(). After a stop timeout,
        # require that cleanup before a later native read can be replaced.
        if self._capture_thread is not None or self._inference_thread is not None:
            return
        self._stop.clear()
        self._latest = None
        self._capture_close_error = None
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
            self._publish(self._status_observation("error", detail), None)
            # Keep the handles: start() must not create a second worker over blocked threads.
            return
        self._capture_thread = None
        self._inference_thread = None
        previous, _ = self.snapshot()
        close_error = self._capture_close_error
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
                read_started = time.perf_counter()
                if isinstance(self.source, SharedFrameSource):
                    shared = self.source.read_sample()
                    frame = shared.frame if shared is not None else None
                else:
                    shared = None
                    frame = self.source.read()
                capture_read_ms = (time.perf_counter() - read_started) * 1000
                if frame is None:
                    with self._condition:
                        self._condition.notify_all()
                    if self.source.status == "stopped":
                        return
                    self._stop.wait(0.1)
                    continue
                now_monotonic = shared.monotonic_at if shared else time.monotonic()
                with self._condition:
                    self._sequence += 1
                    self._latest = _LatestFrame(
                        sequence=self._sequence,
                        frame=frame,
                        captured_at=shared.captured_at if shared else datetime.now(UTC),
                        monotonic_at=now_monotonic,
                        capture_read_ms=capture_read_ms,
                    )
                    self._condition.notify_all()
        finally:
            try:
                # VideoCapture must be released by the same thread that owns read().
                # Releasing it from stop() can race a native AVFoundation callback.
                self.source.close()
            except Exception as exc:
                self._capture_close_error = f"Source close failed: {exc}"
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
                jpeg, jpeg_ms = self._encode_jpeg_timed(latest.frame)
                self._publish(
                    self._frame_status_observation(
                        latest,
                        self.source.status,
                        self.source.last_error,
                        jpeg_ms=jpeg_ms,
                        processing_stage="precheck",
                    ),
                    jpeg,
                )
                continue
            age = now - latest.monotonic_at
            if age > self.stale_after:
                jpeg, jpeg_ms = self._encode_jpeg_timed(latest.frame)
                self._publish(
                    self._frame_status_observation(
                        latest,
                        "stale",
                        "Latest frame is stale",
                        jpeg_ms=jpeg_ms,
                        processing_stage="precheck",
                    ),
                    jpeg,
                )
                continue
            if latest.sequence == last_sequence:
                # A camera/replay that stopped producing frames is unknown, not an empty scene.
                self._publish(
                    self._frame_status_observation(
                        latest,
                        "stale",
                        "No new frame available",
                        processing_stage="precheck",
                    ),
                    None,
                )
                continue
            last_sequence = latest.sequence
            age_before_detect_ms = max(0.0, (time.monotonic() - latest.monotonic_at) * 1000)
            started = time.perf_counter()
            processing_stage = "detect"
            inference_ms: float | None = None
            try:
                detections = self.detector.detect(latest.frame)
                inference_ms = (time.perf_counter() - started) * 1000
                if self._stop.is_set():
                    return
                if self.source.status in ("disconnected", "error", "paused"):
                    jpeg, jpeg_ms = self._encode_jpeg_timed(latest.frame)
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            self.source.status,
                            self.source.last_error,
                            inference_ms,
                            age_before_detect_ms=age_before_detect_ms,
                            jpeg_ms=jpeg_ms,
                            processing_stage="detect",
                        ),
                        jpeg,
                    )
                    continue
                if time.monotonic() - latest.monotonic_at > self.stale_after:
                    jpeg, jpeg_ms = self._encode_jpeg_timed(latest.frame)
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            "stale",
                            "Inference completed after the frame expired",
                            inference_ms,
                            age_before_detect_ms=age_before_detect_ms,
                            jpeg_ms=jpeg_ms,
                            processing_stage="detect",
                        ),
                        jpeg,
                    )
                    continue
                processing_stage = "annotate"
                annotate_started = time.perf_counter()
                rendered = annotate_frame(latest.frame, detections)
                annotate_ms = (time.perf_counter() - annotate_started) * 1000
                processing_stage = "jpeg"
                jpeg, jpeg_ms = self._encode_jpeg_timed(rendered)
                if self._stop.is_set():
                    return
                if self.source.status in ("disconnected", "error", "paused"):
                    raw_jpeg, raw_jpeg_ms = self._encode_jpeg_timed(latest.frame)
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            self.source.status,
                            self.source.last_error,
                            inference_ms,
                            age_before_detect_ms=age_before_detect_ms,
                            annotate_ms=annotate_ms,
                            jpeg_ms=jpeg_ms + raw_jpeg_ms,
                            processing_stage="jpeg",
                        ),
                        raw_jpeg,
                    )
                    continue
                if time.monotonic() - latest.monotonic_at > self.stale_after:
                    raw_jpeg, raw_jpeg_ms = self._encode_jpeg_timed(latest.frame)
                    self._publish(
                        self._frame_status_observation(
                            latest,
                            "stale",
                            "Frame expired before inference results were published",
                            inference_ms,
                            age_before_detect_ms=age_before_detect_ms,
                            annotate_ms=annotate_ms,
                            jpeg_ms=jpeg_ms + raw_jpeg_ms,
                            processing_stage="jpeg",
                        ),
                        raw_jpeg,
                    )
                    continue
                observation = SceneObservation(
                    observed_at=latest.captured_at,
                    monotonic_at=latest.monotonic_at,
                    status="running",
                    fresh=True,
                    detections=detections,
                    inference_ms=inference_ms,
                    processing_timings_ms=self._frame_timings(
                        latest,
                        age_before_detect_ms=age_before_detect_ms,
                        inference_ms=inference_ms,
                        annotate_ms=annotate_ms,
                        jpeg_ms=jpeg_ms,
                    ),
                    processing_stage="publish",
                    width=latest.frame.shape[1],
                    height=latest.frame.shape[0],
                    source=self.source.source_name,
                )
                self._publish(observation, jpeg)
            except Exception as exc:
                if inference_ms is None:
                    inference_ms = (time.perf_counter() - started) * 1000
                jpeg, jpeg_ms = self._encode_jpeg_timed(latest.frame)
                observation = self._frame_status_observation(
                    latest,
                    "error",
                    f"Inference failed: {exc}",
                    inference_ms,
                    age_before_detect_ms=age_before_detect_ms,
                    jpeg_ms=jpeg_ms,
                    processing_stage=processing_stage,
                )
                self._publish(observation, jpeg)

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
        *,
        age_before_detect_ms: float | None = None,
        annotate_ms: float | None = None,
        jpeg_ms: float | None = None,
        processing_stage: str | None = None,
    ) -> SceneObservation:
        return SceneObservation(
            observed_at=latest.captured_at,
            monotonic_at=latest.monotonic_at,
            status=status,
            fresh=False,
            detections=[],
            error=error,
            inference_ms=inference_ms,
            processing_timings_ms=self._frame_timings(
                latest,
                age_before_detect_ms=age_before_detect_ms,
                inference_ms=inference_ms,
                annotate_ms=annotate_ms,
                jpeg_ms=jpeg_ms,
            ),
            processing_stage=processing_stage,
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

    def _encode_jpeg_timed(self, frame: Frame) -> tuple[bytes | None, float]:
        started = time.perf_counter()
        jpeg = self._encode_jpeg(frame)
        return jpeg, (time.perf_counter() - started) * 1000

    @staticmethod
    def _frame_timings(
        latest: _LatestFrame,
        *,
        age_before_detect_ms: float | None = None,
        inference_ms: float | None = None,
        annotate_ms: float | None = None,
        jpeg_ms: float | None = None,
    ) -> dict[str, float]:
        timings = {
            "capture_read": latest.capture_read_ms,
            "age_at_publish": max(0.0, (time.monotonic() - latest.monotonic_at) * 1000),
        }
        if inference_ms is not None:
            timings["detect"] = inference_ms
        if age_before_detect_ms is not None:
            timings["age_before_detect"] = age_before_detect_ms
        if annotate_ms is not None:
            timings["annotate"] = annotate_ms
        if jpeg_ms is not None:
            timings["jpeg"] = jpeg_ms
        return timings

    def _publish(self, observation: SceneObservation, jpeg: bytes | None) -> None:
        with self._condition:
            self._snapshot = (observation, jpeg)
        started = time.perf_counter()
        try:
            self.on_observation(observation, jpeg)
            self.last_callback_error = None
        except Exception as exc:
            # UI/storage callback failures must not stop capture or inference.
            self.last_callback_error = str(exc)
        finally:
            self.last_callback_ms = (time.perf_counter() - started) * 1000
