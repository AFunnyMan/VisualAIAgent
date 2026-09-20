from datetime import UTC, datetime

import cv2
import numpy as np

from visual_ai_agent.models import Detection, SceneObservation
from visual_ai_agent.preview import PreviewRenderer


def _frame(offset: int = 0, *, shape: tuple[int, int] = (120, 160)) -> np.ndarray:
    image = np.zeros((*shape, 3), dtype=np.uint8)
    # A textured object supplies stable sparse features without resembling a model output.
    for y in range(35, 76, 8):
        for x in range(45, 86, 8):
            cv2.circle(image, (x + offset, y), 2, (255, 255, 255), -1)
    return image


def _observation(at: float = 10.0, *, width: int = 160, height: int = 120) -> SceneObservation:
    return SceneObservation(
        observed_at=datetime.now(UTC),
        monotonic_at=at,
        status="running",
        fresh=True,
        detections=[
            Detection(category="cup", confidence=0.91, bbox=(40, 30, 90, 82), region="left")
        ],
        width=width,
        height=height,
        source="test",
    )


def test_tracks_display_box_across_a_translated_frame() -> None:
    renderer = PreviewRenderer()
    first = renderer.render(
        frame=_frame(), sequence=1, monotonic_at=10.1, observation=_observation(), now=10.1
    )
    second = renderer.render(
        frame=_frame(7), sequence=2, monotonic_at=10.2, observation=_observation(), now=10.2
    )

    assert first.jpeg is not None and first.tracked is False
    assert second.jpeg is not None and second.tracked is True
    assert second.detection_age_seconds is not None
    assert abs(second.detection_age_seconds - 0.2) < 1e-6
    decoded = cv2.imdecode(np.frombuffer(second.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == 160
    assert decoded[82, 97, 1] > 120


def test_drops_box_when_visual_features_disappear() -> None:
    renderer = PreviewRenderer()
    observation = _observation()
    renderer.render(
        frame=_frame(), sequence=1, monotonic_at=10.0, observation=observation, now=10.0
    )
    blank = np.zeros((120, 160, 3), dtype=np.uint8)
    result = renderer.render(
        frame=blank, sequence=2, monotonic_at=10.1, observation=observation, now=10.1
    )

    assert result.jpeg is not None
    assert result.tracked is False
    assert result.detection_age_seconds is None


def test_refuses_stale_frame_and_expires_detection_boxes() -> None:
    renderer = PreviewRenderer()
    observation = _observation()
    renderer.render(
        frame=_frame(), sequence=1, monotonic_at=10.0, observation=observation, now=10.0
    )
    expired = renderer.render(
        frame=_frame(2), sequence=2, monotonic_at=12.6, observation=observation, now=12.6
    )
    stale = renderer.render(
        frame=_frame(), sequence=3, monotonic_at=20.0, observation=None, now=20.6
    )

    assert expired.jpeg is not None
    assert expired.detection_age_seconds is None
    assert expired.tracked is False
    assert stale.jpeg is None
    assert stale.status == "stale"


def test_resolution_change_and_non_running_observation_clear_boxes() -> None:
    renderer = PreviewRenderer()
    observation = _observation()
    renderer.render(
        frame=_frame(), sequence=1, monotonic_at=10.0, observation=observation, now=10.0
    )
    resized = renderer.render(
        frame=_frame(shape=(90, 120)),
        sequence=2,
        monotonic_at=10.1,
        observation=observation,
        now=10.1,
    )
    invalid = _observation(10.2, width=120, height=90).model_copy(
        update={"status": "paused", "fresh": False}
    )
    paused = renderer.render(
        frame=_frame(shape=(90, 120)),
        sequence=3,
        monotonic_at=10.2,
        observation=invalid,
        now=10.2,
    )

    assert resized.jpeg is not None and resized.tracked is False
    assert paused.jpeg is not None and paused.tracked is False
    assert paused.status == "paused"


def test_downscales_large_frame_and_detection_coordinates_to_working_size() -> None:
    renderer = PreviewRenderer()
    large = cv2.resize(_frame(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    observation = _observation(width=1920, height=1080).model_copy(
        update={
            "detections": [
                Detection(
                    category="cup",
                    confidence=0.91,
                    bbox=(480, 270, 1080, 810),
                    region="center",
                )
            ]
        }
    )
    result = renderer.render(
        frame=large, sequence=1, monotonic_at=10.0, observation=observation, now=10.0
    )

    assert result.jpeg is not None
    decoded = cv2.imdecode(np.frombuffer(result.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (540, 960)
    # The source x1=480 becomes x=240 in the 960-wide preview.
    assert decoded[135, 240, 1] > 120
