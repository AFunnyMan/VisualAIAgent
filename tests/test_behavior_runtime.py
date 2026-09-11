import threading
import time
from datetime import UTC, datetime

import numpy as np

from visual_ai_agent.behavior import BehaviorDetector, BehaviorWorker
from visual_ai_agent.behavior_visibility import PersonTrackGate
from visual_ai_agent.vision import SharedFrame


class OneSampleSource:
    status = "running"
    last_error = None
    source_name = "replay"

    def __init__(self):
        self.sample = SharedFrame(
            sequence=1,
            frame=np.zeros((4, 4, 3), dtype=np.uint8),
            captured_at=datetime.now(UTC),
            monotonic_at=time.monotonic(),
        )

    def open(self):
        pass

    def read_sample(self):
        sample, self.sample = self.sample, None
        return sample

    def close(self):
        self.status = "stopped"


class SlowDetector:
    def __init__(self):
        self.fresh_values = []

    def observe(self, _frame, _timestamp, *, fresh=True):
        self.fresh_values.append(fresh)
        if fresh:
            time.sleep(0.27)
        return {"posture": "standing", "drinking": "not_drinking", "events": []}

    def close(self, _timeout=5):
        return True


def test_default_behavior_detector_gate_does_not_require_exit_evidence_key():
    class Timeline:
        def observe(self, *_args, **kwargs):
            assert kwargs["exit_evidence"] is False
            return {"posture": "unknown", "drinking": "unknown", "events": []}

    detector = object.__new__(BehaviorDetector)
    detector.gate = PersonTrackGate()
    detector.timeline = Timeline()
    detector.lock = threading.Lock()
    detector.latest = {
        "posture": {"label": "unknown"},
        "drinking": {"label": "unknown"},
        "person_candidates": [],
    }
    detector.detect = lambda _frame: []
    result = detector.observe(np.zeros((100, 100, 3), dtype=np.uint8), 0.0)
    assert result["events"] == []
    assert detector.gate.observe(0.1, [], (100, 100))["exit_evidence"] is False


def test_behavior_worker_rejects_result_that_expires_during_inference():
    source = OneSampleSource()
    detector = SlowDetector()
    observations = []
    published = threading.Event()

    def callback(observation, _jpeg):
        observations.append(observation)
        if observation.status == "stale":
            published.set()

    worker = BehaviorWorker(detector, source, callback, model_version="r03", scene_id="scene-1")
    worker.start()
    assert published.wait(1)
    worker.stop()

    assert observations[0].fresh is False
    assert observations[0].posture == "unknown"
    assert observations[0].source == "replay"
    assert detector.fresh_values[:2] == [True, False]


class FailedSource(OneSampleSource):
    def open(self):
        raise RuntimeError("camera failed")


def test_behavior_worker_reports_source_open_failure():
    observations = []
    published = threading.Event()

    def callback(observation, _jpeg):
        observations.append(observation)
        published.set()

    worker = BehaviorWorker(
        SlowDetector(),
        FailedSource(),
        callback,
        model_version="r03",
        scene_id="scene-1",
    )
    worker.start()
    assert published.wait(1)
    worker.stop()

    assert observations[0].status == "error"
    assert observations[0].fresh is False
