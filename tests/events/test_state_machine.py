from datetime import UTC, datetime, timedelta

from visual_ai_agent.events import EventStateMachine
from visual_ai_agent.models import Detection, SceneObservation

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def observation(
    second: float,
    *,
    categories: tuple[str, ...] = (),
    status: str = "running",
    fresh: bool = True,
) -> SceneObservation:
    return SceneObservation(
        observed_at=BASE + timedelta(seconds=second),
        monotonic_at=second,
        status=status,
        fresh=fresh,
        detections=[
            Detection(category=category, confidence=0.9, bbox=(0, 0, 10, 10), region="left")
            for category in categories
        ],
        source="test",
    )


def confirm_phone(machine: EventStateMachine) -> None:
    assert machine.process(observation(0, categories=("cell phone",))) == []
    assert machine.process(observation(1, categories=("cell phone",))) == []
    events = machine.process(observation(2, categories=("cell phone",)))
    assert [event.kind for event in events] == ["appeared"]


def test_three_contiguous_samples_confirm_appearance() -> None:
    machine = EventStateMachine()
    confirm_phone(machine)
    assert machine.is_present("cell phone")


def test_two_second_occlusion_does_not_emit_missing() -> None:
    machine = EventStateMachine()
    confirm_phone(machine)
    assert machine.process(observation(3)) == []
    assert machine.process(observation(5)) == []
    assert machine.process(observation(6, categories=("cell phone",))) == []
    assert machine.is_present("cell phone")


def test_five_seconds_of_contiguous_valid_absence_emits_once() -> None:
    machine = EventStateMachine()
    confirm_phone(machine)
    assert machine.process(observation(3)) == []
    assert machine.process(observation(5)) == []
    events = machine.process(observation(8))
    assert len(events) == 1
    assert events[0].kind == "missing"
    assert events[0].last_seen_at == BASE + timedelta(seconds=2)
    assert machine.process(observation(9)) == []


def test_fault_and_long_gap_reset_absence_interval() -> None:
    machine = EventStateMachine(max_gap_seconds=3)
    confirm_phone(machine)
    machine.process(observation(3))
    machine.process(observation(4, status="disconnected", fresh=False))
    assert machine.process(observation(10)) == []
    assert machine.process(observation(14)) == []  # gap resets and starts at 14
    assert machine.process(observation(17)) == []
    events = machine.process(observation(19))
    assert [event.kind for event in events] == ["missing"]


def test_sampling_gap_and_monotonic_regression_reset_appearance_count() -> None:
    machine = EventStateMachine(max_gap_seconds=3)
    machine.process(observation(0, categories=("cup",)))
    machine.process(observation(1, categories=("cup",)))
    assert machine.process(observation(10, categories=("cup",))) == []
    assert machine.process(observation(9, categories=("cup",))) == []
    assert machine.process(observation(10, categories=("cup",))) == []
    events = machine.process(observation(11, categories=("cup",)))
    assert [event.category for event in events] == ["cup"]
