"""Paid real-model checks on explicitly controlled local facts, never camera acceptance."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from visual_ai_agent.agent import AgentService
from visual_ai_agent.behavior_models import BehaviorObservation
from visual_ai_agent.behavior_store import BehaviorStore
from visual_ai_agent.config import Config
from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import Detection, SceneObservation, utcnow
from visual_ai_agent.watches import WatchService


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_behavior_query_rule_creation_and_notification(tmp_path):
    import os

    if os.getenv("VAA_RUN_LIVE_API") != "1":
        pytest.skip("explicit paid API opt-in required")
    config = replace(Config.from_env(), data_dir=tmp_path, behavior_enabled=False)
    if not config.agent_connected:
        pytest.skip("real API is not configured")
    store = MemoryStore(tmp_path)
    behavior = BehaviorStore(store)
    rules = ContextRuleService(store, behavior)
    service = AgentService(config, store, WatchService(store), behavior=behavior, rules=rules)
    try:
        base = utcnow() - timedelta(seconds=2)
        for i in range(6):
            behavior.ingest(
                BehaviorObservation(
                    observed_at=base + timedelta(seconds=i / 10),
                    monotonic_at=i / 10,
                    status="running",
                    fresh=True,
                    posture="seated",
                    model_version="test-only",
                    scene_id="controlled",
                    source="test",
                )
            )
        queried = await service.run_user(
            "请查询今天已确认在座的有效时长。不要把它说成学习或专注时长。", uuid4().hex
        )
        assert queried.status == "completed"
        assert any(c["tool"] == "get_current_scene" and c["ok"] for c in queried.tool_calls)
        created = await service.run_user(
            "创建一条长期有效的情境规则：当我离座时，如果手机稳定在画面右侧，"
            "就在页面提醒我‘离开前检查手机’。不限制时间。",
            uuid4().hex,
        )
        assert created.status == "completed"
        assert any(c["tool"] == "create_watch" and c["ok"] for c in created.tool_calls)
        saved = rules.list_rules().data["rules"]
        assert len(saved) == 1
        rule = saved[0]
        assert rule["trigger"] == "left_seat"
        assert rule["object_category"] == "cell phone" and rule["region"] == "right"
        assert rule["after_time"] is None
        frame_base = utcnow() - timedelta(seconds=0.4)
        for i in range(3):
            store.ingest(
                SceneObservation(
                    observed_at=frame_base + timedelta(seconds=i / 10),
                    monotonic_at=10 + i / 10,
                    status="running",
                    fresh=True,
                    scene_id="controlled",
                    source="test",
                    detections=[
                        Detection(
                            category="cell phone",
                            confidence=0.9,
                            bbox=(500, 100, 600, 300),
                            region="right",
                        )
                    ],
                ),
                None,
            )
        # Explicit controlled trigger tests rule/Agent plumbing, not visual action recognition.
        jobs = rules.process_event(
            {
                "kind": "left_seat",
                "event_id": uuid4().hex,
                "confirmed_at": utcnow(),
                "source": "test",
                "scene_id": "controlled",
                "model_version": "test-only",
            }
        )
        assert len(jobs) == 1
        notified = await service.run_rule(jobs[0])
        assert notified.status == "completed"
        assert notified.notification_created
        assert [c["tool"] for c in notified.tool_calls] == [
            "get_current_scene",
            "search_events",
            "notify_user",
        ]
        notices = rules.list_notifications().data["notifications"]
        assert len(notices) == 1 and notices[0]["source"] == "agent"
    finally:
        await service.close()
