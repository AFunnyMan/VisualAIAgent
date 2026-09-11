"""Production-local behavior inference built from the validated r03 pipeline."""

from __future__ import annotations

import ast
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from visual_ai_agent.behavior_auxiliary import BoundedAuxiliaryRunner
from visual_ai_agent.behavior_models import BehaviorEvent, BehaviorObservation
from visual_ai_agent.behavior_preprocess import preprocess_bgr, validate_roi
from visual_ai_agent.behavior_seat_gate import SeatPersonGate
from visual_ai_agent.behavior_timeline import BehaviorTimelineV2
from visual_ai_agent.behavior_visibility import PersonTrackGate, _person_candidates
from visual_ai_agent.vision import Frame, SharedFrameSource, letterbox

BEHAVIOR_INTERVAL = 0.1
BEHAVIOR_STALE_AFTER = 0.25
POSTURE_CONFIRM_SECONDS = 0.3
DRINKING_CONFIRM_SECONDS = 0.5
DRINKING_END_SECONDS = 0.3
UNKNOWN_BRIDGE_SECONDS = 1.0
PERSON_WAIT_MS = 120.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_path(manifest: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else manifest.parent / path).resolve()


def load_behavior_manifest(path: Path, task: str) -> dict[str, Any]:
    """Validate the exact training contract and ONNX digest before loading."""
    path = path.resolve()
    record = json.loads(path.read_text(encoding="utf-8"))
    preprocessing = record.get("preprocessing", {})
    expected = {
        "layout": "NCHW",
        "dtype": "float32",
        "color": "RGB",
        "resize": "aspect-ratio-preserving letterbox",
        "range": [0.0, 1.0],
        "fill_rgb": [114, 114, 114],
    }
    if record.get("status") != "completed" or any(
        preprocessing.get(key) != value for key, value in expected.items()
    ):
        raise ValueError(f"{task} manifest is incomplete or incompatible")
    roi = validate_roi(preprocessing.get("roi"))
    source_size = preprocessing.get("expected_source_frame_size")
    if roi is not None and (
        not isinstance(source_size, list)
        or len(source_size) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in source_size)
    ):
        raise ValueError("ROI manifest requires a positive integer source frame size")
    names = record.get("dataset", {}).get("classes")
    names = (
        {int(key): str(value) for key, value in names.items()} if isinstance(names, dict) else {}
    )
    wanted = {
        "posture": {"seated", "standing", "empty"},
        "drinking": {"drinking", "not_drinking"},
        "laptop": ({"open", "closed"},),
    }
    expected_classes = wanted.get(task)
    class_mapping_valid = (
        set(names.values()) in expected_classes
        if task == "laptop" and expected_classes is not None
        else set(names.values()) == expected_classes
    )
    if (
        task not in wanted
        or set(names) != set(range(len(names)))
        or len(set(names.values())) != len(names)
        or not class_mapping_valid
    ):
        raise ValueError(f"{task} class mapping is unexpected")
    onnx = _resolve_path(path, record["onnx"])
    if not onnx.is_file() or sha256(onnx) != record.get("onnx_sha256"):
        raise ValueError(f"{task} model SHA mismatch")
    return {**record, "_path": path, "_onnx": onnx, "_names": names}


def accepted_label(values: np.ndarray, names: dict[int, str]) -> tuple[str, float, float]:
    probabilities = np.asarray(values, dtype=np.float32).reshape(-1)
    if (
        len(probabilities) != len(names)
        or not np.isfinite(probabilities).all()
        or np.any(probabilities < -1e-4)
        or np.any(probabilities > 1.0 + 1e-4)
        or abs(float(probabilities.sum()) - 1.0) > 1e-4
    ):
        raise ValueError("Invalid classifier probabilities")
    order = np.argsort(probabilities)[::-1]
    top1 = float(probabilities[order[0]])
    top2 = float(probabilities[order[1]]) if len(order) > 1 else 0.0
    return (
        names[int(order[0])] if top1 >= 0.75 and top1 - top2 >= 0.2 else "unknown",
        top1,
        top1 - top2,
    )


class OnnxClassifier:
    """Strict manifest-backed CPU classifier without training dependencies."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.names = record["_names"]
        preprocessing = record["preprocessing"]
        self.size = int(preprocessing["imgsz"])
        self.roi = validate_roi(preprocessing.get("roi"))
        self.expected_source_size = preprocessing.get("expected_source_frame_size")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(
            str(record["_onnx"]), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].type != "tensor(float)"
            or inputs[0].shape != [1, 3, self.size, self.size]
        ):
            raise ValueError("Behavior ONNX must have one static FP32 NCHW input")
        if (
            len(outputs) != 1
            or outputs[0].type != "tensor(float)"
            or outputs[0].shape != [1, len(self.names)]
        ):
            raise ValueError("Behavior ONNX must have one static FP32 class output")
        try:
            metadata = self.session.get_modelmeta().custom_metadata_map.get("names")
            metadata_names = {int(k): str(v) for k, v in ast.literal_eval(metadata).items()}
        except (AttributeError, SyntaxError, TypeError, ValueError) as exc:
            raise ValueError("Behavior ONNX class metadata is invalid") from exc
        if metadata_names != self.names:
            raise ValueError("Behavior ONNX class metadata differs from manifest")
        self.input_name, self.output_name = inputs[0].name, outputs[0].name

    def predict(self, frame: Frame) -> dict[str, Any]:
        if self.expected_source_size is not None and list(frame.shape[1::-1]) != list(
            self.expected_source_size
        ):
            raise ValueError("Source frame size changed; ROI recalibration required")
        preprocess_started = time.perf_counter()
        tensor = preprocess_bgr(frame, self.size, self.roi)
        preprocess_ms = 1000 * (time.perf_counter() - preprocess_started)
        inference_started = time.perf_counter()
        values = self.session.run([self.output_name], {self.input_name: tensor})[0]
        inference_ms = 1000 * (time.perf_counter() - inference_started)
        label, confidence, margin = accepted_label(values, self.names)
        flat = np.asarray(values, dtype=np.float32).reshape(-1)
        return {
            "label": label,
            "confidence": confidence,
            "margin": margin,
            "probabilities": {self.names[i]: float(value) for i, value in enumerate(flat)},
            "inference_ms": inference_ms,
            "preprocess_ms": preprocess_ms,
        }


class BehaviorDetector:
    """Paired r03 classifiers plus bounded person continuity evidence."""

    def __init__(
        self,
        posture: OnnxClassifier,
        drinking: OnnxClassifier,
        person_model: Path,
        *,
        seat_roi=None,
        person_threads: int = 4,
        person_spinning: bool = False,
        auxiliary_mode: str = "bounded",
        person_wait_ms: float = PERSON_WAIT_MS,
        expected_person_sha256: str | None = None,
        retain_diagnostic_frame: bool = True,
    ) -> None:
        self.posture, self.drinking, self.person_wait_ms = posture, drinking, person_wait_ms
        self.retain_diagnostic_frame = retain_diagnostic_frame
        if expected_person_sha256 and sha256(person_model) != expected_person_sha256:
            raise ValueError("Person model SHA mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = person_threads
        options.inter_op_num_threads = 1
        options.add_session_config_entry(
            "session.intra_op.allow_spinning", str(int(person_spinning))
        )
        options.add_session_config_entry(
            "session.inter_op.allow_spinning", str(int(person_spinning))
        )
        self.person_session = ort.InferenceSession(
            str(person_model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.person_input = self.person_session.get_inputs()[0].name
        self.person_output = self.person_session.get_outputs()[0].name
        self.gate = (
            SeatPersonGate(roi=seat_roi)
            if seat_roi is not None
            else PersonTrackGate(max_gap=BEHAVIOR_STALE_AFTER)
        )
        self.timeline = BehaviorTimelineV2(
            posture_confirm_seconds=POSTURE_CONFIRM_SECONDS,
            drinking_confirm_seconds=DRINKING_CONFIRM_SECONDS,
            drinking_end_seconds=DRINKING_END_SECONDS,
            unknown_bridge_seconds=UNKNOWN_BRIDGE_SECONDS,
            max_gap=BEHAVIOR_STALE_AFTER,
        )
        if auxiliary_mode not in {"serial", "bounded"} or not 0 < person_wait_ms <= 150:
            raise ValueError("Invalid auxiliary mode/wait budget")
        self.auxiliary = (
            BoundedAuxiliaryRunner(self._predict_person) if auxiliary_mode == "bounded" else None
        )
        self.drinking_auxiliary = (
            BoundedAuxiliaryRunner(self.drinking.predict) if auxiliary_mode == "bounded" else None
        )
        self.lock = threading.Lock()
        self.sequence = 0
        self.latest: dict[str, Any] | None = None

    def _predict_person(self, frame: Frame) -> dict[str, Any]:
        started = time.perf_counter()
        tensor, transform = letterbox(frame, 640)
        preprocessed = time.perf_counter()
        raw = self.person_session.run([self.person_output], {self.person_input: tensor})[0]
        inferred = time.perf_counter()
        candidates = _person_candidates(raw, transform)
        parsed = time.perf_counter()
        return {
            "candidates": candidates,
            "elapsed_ms": 1000 * (parsed - started),
            "timings": {
                "person_preprocess": 1000 * (preprocessed - started),
                "person_onnx": 1000 * (inferred - preprocessed),
                "person_parse": 1000 * (parsed - inferred),
            },
        }

    def detect(self, frame: Frame) -> list[Any]:
        started = time.perf_counter()
        person_job = self.auxiliary.submit(frame) if self.auxiliary else None
        drinking_job = self.drinking_auxiliary.submit(frame) if self.drinking_auxiliary else None
        posture = self.posture.predict(frame)
        if self.drinking_auxiliary is None:
            drinking_status, drinking_error = "ready", None
            drinking = self.drinking.predict(frame)
        else:
            outcome = (
                self.drinking_auxiliary.get(
                    drinking_job,
                    max(0.0, self.person_wait_ms / 1000 - (time.perf_counter() - started)),
                )
                if drinking_job
                else {"status": "busy", "result": None, "error": None}
            )
            drinking_status, drinking_error = outcome["status"], outcome["error"]
            drinking = (
                outcome["result"]
                if outcome["status"] == "ready"
                else {"label": "unknown", "reason": "drinking_" + outcome["status"]}
            )
        if self.auxiliary is None:
            person_outcome = {
                "status": "ready",
                "result": self._predict_person(frame),
                "error": None,
            }
        elif person_job is None:
            person_outcome = {"status": "busy", "result": None, "error": None}
        else:
            person_outcome = self.auxiliary.get(
                person_job,
                max(0.0, self.person_wait_ms / 1000 - (time.perf_counter() - started)),
            )
        person = person_outcome["result"] if person_outcome["status"] == "ready" else None
        copied_started = time.perf_counter()
        frame_copy = frame.copy() if self.retain_diagnostic_frame else None
        frame_hash = (
            hashlib.sha256(memoryview(frame)).hexdigest() if self.retain_diagnostic_frame else None
        )
        with self.lock:
            self.sequence += 1
            self.latest = {
                "sequence": self.sequence,
                "completed_monotonic": time.monotonic(),
                "frame": frame_copy,
                "frame_sha256": frame_hash,
                "posture": posture,
                "drinking": drinking,
                "person_candidates": person["candidates"] if person else [],
                "person_inference_ms": person["elapsed_ms"] if person else None,
                "auxiliary_status": person_outcome["status"],
                "auxiliary_error": person_outcome["error"],
                "drinking_auxiliary_status": drinking_status,
                "drinking_auxiliary_error": drinking_error,
                "total_inference_ms": 1000 * (time.perf_counter() - started),
                "stage_times_ms": {
                    "posture_preprocess": posture["preprocess_ms"],
                    "posture_onnx": posture["inference_ms"],
                    **(
                        {
                            "drinking_preprocess": drinking["preprocess_ms"],
                            "drinking_onnx": drinking["inference_ms"],
                        }
                        if drinking_status == "ready"
                        else {}
                    ),
                    **(person["timings"] if person else {}),
                    "frame_copy_hash": 1000 * (time.perf_counter() - copied_started),
                },
            }
        return []

    def observe(self, frame: Frame, timestamp: float, *, fresh: bool = True) -> dict[str, Any]:
        if not fresh:
            self.gate.reset()
            return self.timeline.observe(timestamp, "unknown", "unknown", fresh=False)
        self.detect(frame)
        with self.lock:
            assert self.latest is not None
            posture = self.latest["posture"]
            drinking = self.latest["drinking"]
            candidates = self.latest["person_candidates"]
        gate = self.gate.observe(timestamp, candidates, (frame.shape[1], frame.shape[0]))
        result = self.timeline.observe(
            timestamp,
            posture["label"],
            drinking["label"],
            fresh=True,
            continuous_visible=gate["person_track_supported"] is True,
            exit_evidence=gate.get("exit_evidence", False) is True,
        )
        return {**result, "raw_posture": posture, "raw_drinking": drinking, "person_gate": gate}

    def close(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        results = [
            runner.close(max(0.0, deadline - time.monotonic()))
            for runner in (self.auxiliary, self.drinking_auxiliary)
            if runner
        ]
        return all(results)

    def snapshot(self, *, include_frame: bool = True) -> dict[str, Any] | None:
        with self.lock:
            if self.latest is None:
                return None
            result = {key: value for key, value in self.latest.items() if key != "frame"}
            if include_frame and self.latest["frame"] is not None:
                result["frame"] = self.latest["frame"].copy()
            return result

    def reset_gate(self) -> None:
        self.gate.reset()


class BehaviorWorker:
    """Independent 10fps consumer of a shared camera, isolated from object inference."""

    def __init__(
        self,
        detector: BehaviorDetector,
        source: SharedFrameSource,
        callback,
        *,
        model_version: str,
        scene_id: str,
    ) -> None:
        self.detector = detector
        self.source = source
        self.callback = callback
        self.model_version = model_version
        self.scene_id = scene_id
        self.last_error: str | None = None
        self._cleanup_failed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._cleanup_failed or (self._thread is not None and self._thread.is_alive())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="behavior-inference", daemon=True)
        self._thread.start()

    def _observation(
        self,
        sample,
        *,
        status="running",
        fresh=True,
        posture="unknown",
        drinking=None,
        events=None,
        error=None,
    ):
        return BehaviorObservation(
            observed_at=sample.captured_at if sample is not None else datetime.now(UTC),
            monotonic_at=sample.monotonic_at if sample is not None else max(0.0, time.monotonic()),
            status=status,
            fresh=fresh,
            posture=posture,
            drinking=drinking,
            events=events or [],
            model_version=self.model_version,
            scene_id=self.scene_id,
            source=self.source.source_name,
            error=error,
        )

    def _run(self) -> None:
        next_run = time.monotonic()
        try:
            self.source.open()
            while not self._stop.is_set():
                delay = next_run - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
                next_run = max(next_run + BEHAVIOR_INTERVAL, time.monotonic())
                sample = self.source.read_sample()
                if sample is None:
                    fault_at = time.monotonic()
                    self.detector.observe(
                        np.empty((0, 0, 3), dtype=np.uint8), fault_at, fresh=False
                    )
                    status = self.source.status
                    if status not in {"stopped", "paused", "disconnected", "error"}:
                        status = "stale"
                    self.callback(
                        self._observation(
                            None,
                            status=status,
                            fresh=False,
                            error=self.source.last_error or "No new frame available",
                        ),
                        None,
                    )
                    continue
                age = time.monotonic() - sample.monotonic_at
                if age > BEHAVIOR_STALE_AFTER:
                    self.detector.observe(sample.frame, sample.monotonic_at, fresh=False)
                    self.callback(
                        self._observation(
                            sample, status="stale", fresh=False, error="Latest frame is stale"
                        ),
                        None,
                    )
                    continue
                try:
                    result = self.detector.observe(sample.frame, sample.monotonic_at)
                    if time.monotonic() - sample.monotonic_at > BEHAVIOR_STALE_AFTER:
                        self.detector.observe(sample.frame, sample.monotonic_at, fresh=False)
                        self.callback(
                            self._observation(
                                sample,
                                status="stale",
                                fresh=False,
                                error="Behavior inference completed after freshness limit",
                            ),
                            None,
                        )
                        continue
                    confirmed_at = sample.captured_at
                    events = [
                        BehaviorEvent(
                            kind=item["kind"],
                            observed_at=sample.captured_at,
                            confirmed_at=confirmed_at,
                            model_version=self.model_version,
                            scene_id=self.scene_id,
                            source=self.source.source_name,
                        )
                        for item in result["events"]
                    ]
                    jpeg = None
                    if events:
                        ok, encoded = cv2.imencode(
                            ".jpg", sample.frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
                        )
                        jpeg = encoded.tobytes() if ok else None
                    if time.monotonic() - sample.monotonic_at > BEHAVIOR_STALE_AFTER:
                        self.detector.observe(sample.frame, sample.monotonic_at, fresh=False)
                        self.callback(
                            self._observation(
                                sample,
                                status="stale",
                                fresh=False,
                                error="Behavior result expired before publication",
                            ),
                            None,
                        )
                        continue
                    posture = result["posture"]
                    drinking = {"drinking": True, "not_drinking": False}.get(result["drinking"])
                    self.callback(
                        self._observation(
                            sample, posture=posture, drinking=drinking, events=events
                        ),
                        jpeg,
                    )
                    self.last_error = None
                except Exception as exc:
                    self.detector.observe(sample.frame, sample.monotonic_at, fresh=False)
                    self.last_error = f"Behavior inference failed ({type(exc).__name__})"
                    self.callback(
                        self._observation(
                            sample, status="error", fresh=False, error=self.last_error
                        ),
                        None,
                    )
        except Exception as exc:
            self.last_error = f"Behavior worker failed ({type(exc).__name__})"
            self.callback(
                self._observation(None, status="error", fresh=False, error=self.last_error), None
            )
        finally:
            self.source.close()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        if thread is not None and not thread.is_alive():
            cleaned = self.detector.close(max(0.0, timeout))
            self._cleanup_failed = not cleaned
            if cleaned:
                self._thread = None
            else:
                self.last_error = "Behavior model workers did not stop within timeout"
