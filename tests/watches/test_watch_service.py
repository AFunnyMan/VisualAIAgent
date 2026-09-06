from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from visual_ai_agent.memory import MemoryStore, _iso
from visual_ai_agent.models import Detection, SceneObservation, VisualEvent
from visual_ai_agent.watches import WatchService


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def ingest_appeared(store: MemoryStore, start: int = 1) -> VisualEvent:
    events = []
    for second in range(start, start + 3):
        events = store.ingest(
            SceneObservation(
                observed_at=BASE + timedelta(seconds=second),
                monotonic_at=float(second),
                status="running",
                fresh=True,
                detections=[
                    Detection(category="bottle", confidence=0.9, bbox=(0, 0, 4, 4), region="right")
                ],
                source="test",
            )
        )
    return events[0]


def test_create_is_request_idempotent_and_cancel_is_state_idempotent(tmp_path) -> None:
    clock = Clock(BASE)
    service = WatchService(MemoryStore(tmp_path, clock=clock), clock=clock)
    first = service.create_watch("bottle", "appeared", request_id="request-1")
    second = service.create_watch("bottle", "appeared", request_id="request-1")
    assert first.ok and second.ok
    assert second.data["deduplicated"] is True
    assert first.data["watch"]["watch_id"] == second.data["watch"]["watch_id"]
    assert not service.create_watch("cup", "appeared", request_id="request-1").ok
    watch_id = first.data["watch"]["watch_id"]
    assert service.cancel_watch(watch_id).data["changed"] is True
    assert service.cancel_watch(watch_id).data["changed"] is False


def test_only_post_creation_event_is_claimed_once(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    old_event = ingest_appeared(store)
    clock.value = BASE + timedelta(seconds=4)
    service = WatchService(store, clock=clock)
    created = service.create_watch("bottle", "appeared", request_id="request-1")
    assert service.match_event(old_event) == []

    # Confirm a missing transition and a new appearance after the watch exists.
    for second in (4, 6, 9):
        store.ingest(
            SceneObservation(
                observed_at=BASE + timedelta(seconds=second),
                monotonic_at=float(second),
                status="running",
                fresh=True,
                source="test",
            )
        )
    new_event = ingest_appeared(store, 10)
    claimed = service.match_event(new_event)
    assert [task.watch_id for task in claimed] == [created.data["watch"]["watch_id"]]
    assert service.match_event(new_event) == []


def test_notification_is_concurrency_safe_and_contains_evidence_id_only(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    service = WatchService(store, clock=clock)
    service.create_watch("bottle", "appeared", request_id="request-1")
    event = ingest_appeared(store)
    clock.value = BASE + timedelta(seconds=4)
    claimed = service.match_event(event)
    assert len(claimed) == 1

    def notify():
        return service.notify_user(claimed[0].watch_id, event.event_id, "Bottle appeared")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: notify(), range(4)))
    assert all(result.ok for result in results)
    assert sum(not result.data["deduplicated"] for result in results) == 1
    notifications = service.list_notifications().data["notifications"]
    assert len(notifications) == 1
    assert "path" not in notifications[0]


def test_cancelled_and_expired_watches_reject_late_notifications(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    service = WatchService(store, clock=clock)
    cancelled = service.create_watch("bottle", "appeared", request_id="cancel")
    event = ingest_appeared(store)
    clock.value = BASE + timedelta(seconds=4)
    claimed = service.match_event(event)
    assert claimed
    service.cancel_watch(cancelled.data["watch"]["watch_id"])
    assert not service.notify_user(claimed[0].watch_id, event.event_id, "late").ok

    expiring = service.create_watch("bottle", "missing", duration_minutes=1, request_id="expire")
    clock.value = BASE + timedelta(minutes=2)
    assert service.list_watches().data["watches"][0]["status"] == "expired"
    assert not service.notify_user(expiring.data["watch"]["watch_id"], event.event_id, "late").ok


def test_missing_event_requires_prior_seen_fact(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    service = WatchService(store, clock=clock)
    service.create_watch("cup", "missing", request_id="request-1")
    fake = VisualEvent(
        kind="missing",
        category="cup",
        observed_at=BASE + timedelta(seconds=1),
        confirmed_at=BASE + timedelta(seconds=6),
        source="test",
    )
    with store._transaction(immediate=True) as connection:
        connection.execute(
            """INSERT INTO visual_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fake.event_id,
                fake.kind,
                fake.category,
                "[]",
                _iso(fake.observed_at),
                _iso(fake.confirmed_at),
                None,
                None,
                fake.source,
            ),
        )
    clock.value = BASE + timedelta(seconds=7)
    assert service.match_event(fake) == []


def test_real_missing_event_with_prior_seen_fact_is_claimed(tmp_path) -> None:
    clock = Clock(BASE - timedelta(seconds=1))
    store = MemoryStore(tmp_path, clock=clock, max_gap_seconds=3)
    service = WatchService(store, clock=clock)
    watch = service.create_watch("bottle", "missing", request_id="missing-watch")
    ingest_appeared(store, 0)
    missing_events = []
    for second in (3, 5, 8):
        missing_events = store.ingest(
            SceneObservation(
                observed_at=BASE + timedelta(seconds=second),
                monotonic_at=float(second),
                status="running",
                fresh=True,
                source="test",
            )
        )
    clock.value = BASE + timedelta(seconds=9)
    claimed = service.match_event(missing_events[0])
    assert claimed[0].watch_id == watch.data["watch"]["watch_id"]


def test_recover_returns_pending_and_reconciles_existing_notification(tmp_path) -> None:
    clock = Clock(BASE)
    store = MemoryStore(tmp_path, clock=clock)
    service = WatchService(store, clock=clock)
    service.create_watch("bottle", "appeared", request_id="waiting")
    processing = service.create_watch("bottle", "appeared", request_id="processing")
    event = ingest_appeared(store)
    clock.value = BASE + timedelta(seconds=4)
    claimed = service.match_event(event)
    assert len(claimed) == 2
    service.notify_user(processing.data["watch"]["watch_id"], event.event_id, "done")
    recovered = WatchService(store, clock=clock).recover()
    assert len(recovered) == 1
    assert recovered[0].status == "processing"
