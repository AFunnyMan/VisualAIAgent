import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from visual_ai_agent.agent import AgentService
from visual_ai_agent.config import Config
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import Detection, SceneObservation
from visual_ai_agent.watches import WatchService


def live_config(tmp_path):
    if os.getenv("VAA_RUN_LIVE_API") != "1":
        pytest.skip("set VAA_RUN_LIVE_API=1 to explicitly allow paid requests")
    config = Config.from_env()
    if not config.agent_connected:
        pytest.skip("VAA API URL, model, and key are required")
    return replace(config, data_dir=tmp_path)


def live_service(tmp_path):
    config = live_config(tmp_path)
    store = MemoryStore(tmp_path)
    return AgentService(config, store, WatchService(store)), store


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_model_naturally_queries_unknown_visual_fact(tmp_path):
    service, store = live_service(tmp_path)
    try:
        result = await service.run_user(
            "我以前在什么位置看到过杯子？如果没有记录，请明确告诉我。",
            uuid4().hex,
        )
    finally:
        await service.close()

    assert result.status == "completed"
    assert any(call["tool"] == "find_object" and call["ok"] for call in result.tool_calls)
    assert store.find_object("cup").data["found"] is False
    assert 1 <= result.request_attempts <= 3


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_model_creates_lists_and_cancels_a_watch(tmp_path):
    service, store = live_service(tmp_path)
    try:
        created = await service.run_user(
            "未来三十分钟内，如果确认看到瓶子，请提醒我。",
            uuid4().hex,
        )
        watches = service.watch_service.list_watches().data["watches"]
        assert created.status == "completed"
        assert any(call["tool"] == "create_watch" and call["ok"] for call in created.tool_calls)
        assert len(watches) == 1 and watches[0]["status"] == "waiting"

        listed = await service.run_user("请告诉我目前有哪些关注任务。", uuid4().hex)
        assert listed.status == "completed"
        assert any(call["tool"] == "list_watches" and call["ok"] for call in listed.tool_calls)

        watch_id = watches[0]["watch_id"]
        cancelled = await service.run_user(f"请取消关注任务 {watch_id}。", uuid4().hex)
        assert cancelled.status == "completed"
        assert any(call["tool"] == "cancel_watch" and call["ok"] for call in cancelled.tool_calls)
        assert service.watch_service.list_watches().data["watches"][0]["status"] == "cancelled"
        assert len(store.list_chat_interactions()) == 3
    finally:
        await service.close()


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_event_agent_reviews_test_facts_and_notifies_with_agent_source(tmp_path):
    service, store = live_service(tmp_path)
    watches = service.watch_service
    created = watches.create_watch("cup", "appeared", request_id=uuid4().hex)
    assert created.ok
    base = datetime.now(UTC) + timedelta(milliseconds=10)
    detection = Detection(category="cup", confidence=0.91, bbox=(20, 30, 100, 180), region="left")
    events = []
    for index in range(3):
        events = store.ingest(
            SceneObservation(
                observed_at=base + timedelta(milliseconds=100 * index),
                monotonic_at=float(index),
                status="running",
                fresh=True,
                detections=[detection],
                source="test",
            ),
            b"test-only-jpeg",
        )
    assert len(events) == 1 and events[0].source == "test"
    matched = watches.match_event(events[0])
    assert len(matched) == 1

    try:
        result = await service.run_event(matched[0], events[0])
    finally:
        await service.close()

    tool_names = [call["tool"] for call in result.tool_calls]
    assert result.status == "completed"
    assert result.notification_created is True
    assert "get_current_scene" in tool_names
    assert "search_events" in tool_names or "find_object" in tool_names
    assert "notify_user" in tool_names
    notifications = watches.list_notifications().data["notifications"]
    assert len(notifications) == 1
    assert notifications[0]["source"] == "agent"
    assert notifications[0]["event_id"] == events[0].event_id
