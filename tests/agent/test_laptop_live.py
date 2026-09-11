"""Explicit paid-model check over controlled laptop facts; never camera acceptance."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from visual_ai_agent.agent import AgentService
from visual_ai_agent.behavior_store import BehaviorStore
from visual_ai_agent.config import Config
from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.laptop_store import LaptopStore
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import utcnow
from visual_ai_agent.watches import WatchService


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_laptop_query_rule_and_event_notification(tmp_path):
    import os

    if os.getenv("VAA_RUN_LIVE_API") != "1":
        pytest.skip("explicit paid API opt-in required")
    config = replace(Config.from_env(), data_dir=tmp_path, laptop_enabled=False)
    assert config.agent_connected, "VAA_RUN_LIVE_API=1 requires real API configuration"
    memory = MemoryStore(tmp_path)
    laptop = LaptopStore(memory)
    laptop.set_capability(configured=True, available=True, reason="controlled test capability")
    rules = ContextRuleService(
        memory,
        BehaviorStore(memory),
        config.timezone,
        laptop=laptop,
    )
    service = AgentService(
        config,
        memory,
        WatchService(memory),
        behavior=BehaviorStore(memory),
        laptop=laptop,
        rules=rules,
    )
    try:
        queried = await service.run_user(
            "请查询当前笔记本开合能力。没有当前画面就明确说无法确认。",
            uuid4().hex,
        )
        assert queried.status == "completed"
        assert any(
            call["tool"] == "get_current_scene" and call["ok"] for call in queried.tool_calls
        )

        created = await service.run_user(
            "创建长期规则：确认笔记本完全合盖时，在页面提醒我‘笔记本已合盖’。",
            uuid4().hex,
        )
        assert created.status == "completed"
        saved = rules.list_rules().data["rules"]
        assert len(saved) == 1 and saved[0]["trigger"] == "laptop_closed"

        event_id = uuid4().hex
        event_at = utcnow() - timedelta(milliseconds=10)
        evidence = memory._write_evidence(b"controlled-test-evidence")
        assert evidence is not None
        with memory._transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?)",
                (evidence[0], evidence[1], event_at.isoformat(), evidence[2], evidence[3]),
            )
        jobs = rules.process_event(
            {
                "event_id": event_id,
                "kind": "laptop_closed",
                "state": "closed",
                "evidence_id": evidence[0],
                "confirmed_at": event_at,
                "model_version": "controlled-state",
                "presence_model_version": "controlled-presence",
                "scene_id": "controlled",
                "source": "test",
            }
        )
        assert len(jobs) == 1
        notified = await service.run_rule(jobs[0])
        assert notified.status == "completed"
        assert notified.notification_created
        assert [call["tool"] for call in notified.tool_calls] == [
            "get_current_scene",
            "search_events",
            "notify_user",
        ]
        notices = rules.list_notifications().data["notifications"]
        assert len(notices) == 1 and notices[0]["source"] == "agent"
    finally:
        await service.close()
