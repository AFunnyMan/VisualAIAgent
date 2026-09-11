import threading
import time

import numpy as np

from visual_ai_agent.vision import SharedCamera, VisionWorker


class CountingCamera:
    source_name = "camera"

    def __init__(self):
        self.status = "stopped"
        self.last_error = None
        self.opens = 0
        self.closes = 0
        self.value = 0

    def open(self):
        self.opens += 1
        self.status = "running"

    def read(self):
        self.value += 1
        time.sleep(0.002)
        return np.full((2, 2, 3), self.value % 255, dtype=np.uint8)

    def close(self):
        self.closes += 1
        self.status = "stopped"


def test_shared_camera_opens_physical_source_once_for_two_consumers():
    physical = CountingCamera()
    camera = SharedCamera(physical)  # type: ignore[arg-type]
    first, second = camera.subscribe(), camera.subscribe()

    first.open()
    second.open()
    one = first.read_sample()
    two = second.read_sample()

    assert one is not None and two is not None
    assert physical.opens == 1
    first.close()
    assert physical.closes == 0
    second.close()
    assert physical.closes == 1


class FailedCamera(CountingCamera):
    def __init__(self, *, fail_open):
        super().__init__()
        self.fail_open = fail_open

    def open(self):
        if self.fail_open:
            raise PermissionError("camera denied")
        super().open()

    def read(self):
        raise RuntimeError("camera read failed")


def test_shared_camera_preserves_open_failure_after_source_close():
    physical = FailedCamera(fail_open=True)
    camera = SharedCamera(physical)  # type: ignore[arg-type]
    source = camera.subscribe()
    source.open()
    deadline = time.monotonic() + 1
    while camera.running and time.monotonic() < deadline:
        time.sleep(0.001)

    assert source.status == "error"
    assert source.last_error == "camera denied"
    source.close()


def test_shared_camera_preserves_read_failure_after_source_close():
    physical = FailedCamera(fail_open=False)
    camera = SharedCamera(physical)  # type: ignore[arg-type]
    source = camera.subscribe()
    source.open()
    deadline = time.monotonic() + 1
    while camera.running and time.monotonic() < deadline:
        time.sleep(0.001)

    assert source.status == "error"
    assert source.last_error == "camera read failed"
    source.close()


class SlowOpeningCamera(CountingCamera):
    def open(self):
        time.sleep(0.35)
        super().open()


class EmptyDetector:
    def detect(self, _frame):
        return []


def test_slow_open_does_not_end_either_inference_consumer_before_first_frame():
    physical = SlowOpeningCamera()
    camera = SharedCamera(physical)  # type: ignore[arg-type]
    fresh = [threading.Event(), threading.Event()]
    workers = []
    for index in range(2):
        worker = VisionWorker(
            EmptyDetector(),
            camera.subscribe(),
            lambda observation, _jpeg, index=index: (
                fresh[index].set() if observation.fresh else None
            ),
            inference_interval=0.02,
            stale_after=0.25,
        )
        workers.append(worker)
        worker.start()

    assert fresh[0].wait(2)
    assert fresh[1].wait(2)
    assert physical.opens == 1
    for worker in workers:
        worker.stop()
