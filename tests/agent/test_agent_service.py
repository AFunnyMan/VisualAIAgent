from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from agents import Model, ModelResponse, Usage

from visual_ai_agent.agent import AgentService
from visual_ai_agent.config import Config
from visual_ai_agent.context_rules import ContextRuleService
from visual_ai_agent.laptop_store import LaptopStore
from visual_ai_agent.memory import MemoryStore
from visual_ai_agent.models import Detection, SceneObservation
from visual_ai_agent.watches import WatchService


def tool_call(name: str, arguments: str, call_id: str) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "message-id",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class ScriptedModel(Model):
    """Real Agents SDK Model boundary with deterministic provider responses."""

    def __init__(self, outputs: list[list[dict[str, Any]]], *, include_usage: bool = True):
        self.outputs = outputs
        self.include_usage = include_usage
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        index = len(self.calls)
        self.calls.append(
            {
                "system_instructions": system_instructions,
                "input": input,
                "tools": [item.name for item in tools],
                "model_settings": model_settings,
                "tracing": tracing,
            }
        )
        output = self.outputs[min(index, len(self.outputs) - 1)]
        raw_usage = (
            {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
            if self.include_usage
            else None
        )
        return ModelResponse(
            output=output,
            usage=Usage(requests=1, input_tokens=10, output_tokens=4, total_tokens=14),
            response_id=f"response-{index}",
            raw_usage=raw_usage,
        )

    async def stream_response(self, *args, **kwargs):
        if False:
            yield None
        raise AssertionError("streaming is not used")

    async def close(self) -> None:
        self.closed += 1


class ExplodingModel(ScriptedModel):
    async def get_response(self, *args, **kwargs):
        self.calls.append({})
        raise RuntimeError("provider failed with sk-do-not-persist and ?token=private")


class FailAfterModel(ScriptedModel):
    def __init__(self, outputs, *, fail_after: int):
        super().__init__(outputs)
        self.fail_after = fail_after

    async def get_response(self, *args, **kwargs):
        if len(self.calls) >= self.fail_after:
            self.calls.append({})
            raise RuntimeError("provider unavailable")
        return await super().get_response(*args, **kwargs)


@pytest.fixture
def services(tmp_path):
    store = MemoryStore(tmp_path)
    return store, WatchService(store)


def configured(tmp_path, **changes) -> Config:
    values = {
        "data_dir": tmp_path,
        "api_key": "test-only",
        "api_base_url": "https://example.invalid/v1",
        "agent_model": "fake-model",
    }
    values.update(changes)
    return Config(**values)


@pytest.mark.asyncio
async def test_sdk_executes_one_of_exactly_seven_business_tools_and_records_usage(
    tmp_path, services
):
    store, watches = services
    model = ScriptedModel(
        [
            [tool_call("find_object", '{"category":"cup"}', "call-find")],
            [message("没有杯子的历史记录。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("杯子最后在哪里？", "request-find")

    assert [tool.name for tool in service.tools] == [
        "get_current_scene",
        "find_object",
        "search_events",
        "create_watch",
        "list_watches",
        "cancel_watch",
        "notify_user",
    ]
    assert result.status == "completed"
    assert result.request_attempts == 2
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (20, 8, 28)
    assert result.tool_calls == (
        {"tool": "find_object", "ok": True, "category": "cup", "found": False},
    )
    assert len(model.calls) == 2
    assert model.calls[0]["tools"] == [tool.name for tool in service.tools]
    assert model.calls[0]["model_settings"].tool_choice == "required"
    assert model.calls[1]["model_settings"].tool_choice is None
    assert "当前时间是" in model.calls[0]["system_instructions"]
    assert "时区是Asia/Shanghai" in model.calls[0]["system_instructions"]
    assert "最近一小时" in model.calls[0]["system_instructions"]
    assert "类别、时间、区域和证据编号" in model.calls[0]["system_instructions"]
    assert "多个候选必须逐项列出" in model.calls[0]["system_instructions"]
    assert "不得认定为同一件或特定身份" in model.calls[0]["system_instructions"]
    assert "历史事件必须依据search_events" in model.calls[0]["system_instructions"]
    assert "事件时间和证据" in model.calls[0]["system_instructions"]
    assert "只能表述为“持续未检测到”" in model.calls[0]["system_instructions"]
    assert "不得推断物品被拿走、发生物理消失" in model.calls[0]["system_instructions"]
    assert store.list_chat_interactions()[-1]["assistant_message"] == "没有杯子的历史记录。"

    await service.close()
    await service.close()
    assert model.closed == 1


@pytest.mark.asyncio
async def test_laptop_queries_extend_existing_tool_without_adding_an_eighth(tmp_path, services):
    store, watches = services
    laptop = LaptopStore(store)
    model = ScriptedModel(
        [
            [tool_call("get_current_scene", '{"scope":"laptop"}', "call-laptop")],
            [message("笔记本开合实验未启用，当前无法确认。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model, laptop=laptop)
    result = await service.run_user("笔记本现在合上了吗？", "laptop-query")
    assert len(service.tools) == 7
    assert result.status == "completed"
    assert result.tool_calls == ({"tool": "get_current_scene", "ok": True, "current": False},)
    assert "available" in model.calls[0]["system_instructions"]
    await service.close()


@pytest.mark.asyncio
async def test_agent_cannot_create_laptop_rule_while_capability_is_unavailable(tmp_path, services):
    store, watches = services
    laptop = LaptopStore(store)
    rules = ContextRuleService(store, timezone="UTC", laptop=laptop)
    model = ScriptedModel(
        [
            [
                tool_call(
                    "create_watch",
                    '{"target":"rule","trigger":"laptop_closed","message":"提醒我"}',
                    "call-rule",
                )
            ],
            [message("笔记本能力尚未验收，不能创建这条规则。")],
        ]
    )
    service = AgentService(
        configured(tmp_path), store, watches, model=model, laptop=laptop, rules=rules
    )
    result = await service.run_user("合盖时提醒我", "laptop-rule-unavailable")
    assert result.status == "completed"
    assert result.tool_calls[0]["tool"] == "create_watch"
    assert result.tool_calls[0]["ok"] is False
    assert rules.list_rules().data["rules"] == []
    await service.close()


@pytest.mark.asyncio
async def test_create_watch_retries_share_business_request_id(tmp_path, services):
    store, watches = services
    arguments = '{"category":"bottle","condition":"appeared","duration_minutes":30}'
    model = ScriptedModel(
        [
            [tool_call("create_watch", arguments, "call-create-1")],
            [tool_call("create_watch", arguments, "call-create-2")],
            [message("关注已创建。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("瓶子出现时提醒我", "same-create-request")

    assert result.status == "completed"
    listed = watches.list_watches()
    assert listed.ok and len(listed.data["watches"]) == 1
    assert result.tool_calls[0]["deduplicated"] is False
    assert result.tool_calls[1]["deduplicated"] is True


@pytest.mark.asyncio
async def test_three_model_attempts_are_a_hard_limit(tmp_path, services):
    store, watches = services
    model = ScriptedModel(
        [[tool_call("list_watches", "{}", f"call-{index}")] for index in range(3)]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("一直查询", "request-turn-limit")

    assert result.status == "failed"
    assert result.request_attempts == 3
    assert len(model.calls) == 3
    assert result.input_tokens == 30
    assert result.usage_complete is True
    assert result.message == "模型请求失败。"


@pytest.mark.asyncio
async def test_missing_provider_usage_remains_none(tmp_path, services):
    store, watches = services
    model = ScriptedModel(
        [
            [tool_call("get_current_scene", "{}", "call-scene")],
            [message("当前没有有效画面。")],
        ],
        include_usage=False,
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("当前情况", "request-no-usage")

    assert result.status == "completed"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (None, None, None)


@pytest.mark.asyncio
async def test_user_business_success_without_any_tool_is_rejected(tmp_path, services):
    store, watches = services
    created = watches.create_watch("bottle", "appeared", request_id="create-before-cancel")
    assert created.ok
    watch_id = created.data["watch"]["watch_id"]
    model = ScriptedModel([[message("关注任务已取消。")]])
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user(f"请取消关注任务 {watch_id}。", "request-false-cancel")

    assert result.status == "failed"
    assert result.message == "模型未执行业务工具，无法确认请求结果。"
    assert result.tool_calls == ()
    assert watches.list_watches().data["watches"][0]["status"] == "waiting"
    assert model.calls[0]["model_settings"].tool_choice == "required"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, error FROM agent_runs WHERE request_id = ?",
            ("request-false-cancel",),
        ).fetchone()
    assert (row["status"], row["error"]) == ("failed", "model_completed_without_tool")


@pytest.mark.asyncio
async def test_no_configuration_does_not_construct_or_call_a_model(tmp_path, services):
    store, watches = services
    service = AgentService(Config(data_dir=tmp_path), store, watches)

    result = await service.run_user("杯子在哪", "request-offline")

    assert result.status == "not_connected"
    assert result.request_attempts == 0
    assert result.total_tokens is None
    assert "Agent 未连接" in result.message


@pytest.mark.asyncio
async def test_provider_exception_is_reduced_to_a_safe_code(tmp_path, services):
    store, watches = services
    model = ExplodingModel([[message("unused")]])
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("查询", "request-provider-error")

    assert result.status == "failed"
    assert result.request_attempts == 1
    assert "sk-do-not-persist" not in result.message
    with store._connect() as connection:
        row = connection.execute(
            "SELECT error FROM agent_runs WHERE request_id = ?",
            ("request-provider-error",),
        ).fetchone()
    assert row["error"] == "model_request_failed"


@pytest.mark.asyncio
async def test_business_tool_exception_becomes_safe_tool_result(tmp_path, services):
    store, watches = services

    def fail_find(_category):
        raise RuntimeError("private local path and secret must not cross the tool boundary")

    store.find_object = fail_find
    model = ScriptedModel(
        [
            [tool_call("find_object", '{"category":"cup"}', "call-failing-tool")],
            [message("查询服务暂时不可用。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("杯子在哪", "request-tool-error")

    assert result.status == "completed"
    assert result.tool_calls == (
        {"tool": "find_object", "ok": False, "error": "business service unavailable"},
    )


def make_claimed_event(store: MemoryStore, watches: WatchService):
    created = watches.create_watch("cup", "appeared", request_id="watch-request")
    assert created.ok
    base = datetime.now(UTC) + timedelta(milliseconds=10)
    detection = Detection(category="cup", confidence=0.9, bbox=(1, 2, 20, 30), region="left")
    events = []
    for index in range(3):
        events = store.ingest(
            SceneObservation(
                observed_at=base + timedelta(milliseconds=index * 100),
                monotonic_at=float(index),
                status="running",
                fresh=True,
                detections=[detection],
                source="test",
            ),
            b"jpeg",
        )
    assert len(events) == 1
    matched = watches.match_event(events[0])
    assert len(matched) == 1
    return matched[0], events[0]


@pytest.mark.asyncio
async def test_event_requires_scene_and_target_evidence_before_agent_notification(
    tmp_path, services
):
    store, watches = services
    watch, event = make_claimed_event(store, watches)
    start = (event.confirmed_at - timedelta(minutes=1)).isoformat()
    end = (event.confirmed_at + timedelta(minutes=1)).isoformat()
    first_turn = [
        tool_call("get_current_scene", "{}", "call-scene"),
        tool_call(
            "search_events",
            f'{{"category":"cup","start":"{start}","end":"{end}"}}',
            "call-events",
        ),
    ]
    notify_arguments = (
        f'{{"watch_id":"{watch.watch_id}","event_id":"{event.event_id}",'
        '"message":"杯子已出现，证据已核验。"}'
    )
    model = ScriptedModel(
        [
            first_turn,
            [tool_call("notify_user", notify_arguments, "call-notify")],
            [message("已根据核验后的事件提醒。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_event(watch, event)

    assert result.status == "completed"
    assert result.notification_created is True
    notifications = watches.list_notifications().data["notifications"]
    assert len(notifications) == 1
    assert notifications[0]["source"] == "agent"
    assert result.request_attempts == 3
    first_input = model.calls[0]["input"]
    assert isinstance(first_input, list)
    event_prompt = first_input[-1]["content"]
    assert "current_time=" in event_prompt
    assert "search_start=" in event_prompt and "search_end=" in event_prompt
    assert "时间均带时区且start<end" in event_prompt
    assert "最多只有三次模型请求" in event_prompt
    assert "aware datetimes" in service.tools[2].description
    assert "start strictly before end" in service.tools[2].description


@pytest.mark.asyncio
async def test_event_third_turn_notification_is_success_even_without_fourth_final_turn(
    tmp_path, services
):
    store, watches = services
    watch, event = make_claimed_event(store, watches)
    start = (event.confirmed_at - timedelta(minutes=1)).isoformat()
    end = (event.confirmed_at + timedelta(minutes=1)).isoformat()
    model = ScriptedModel(
        [
            [tool_call("get_current_scene", "{}", "call-scene")],
            [
                tool_call(
                    "search_events",
                    f'{{"category":"cup","start":"{start}","end":"{end}"}}',
                    "call-events",
                )
            ],
            [
                tool_call(
                    "notify_user",
                    (
                        f'{{"watch_id":"{watch.watch_id}","event_id":"{event.event_id}",'
                        '"message":"已核验，杯子出现。"}'
                    ),
                    "call-notify",
                )
            ],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_event(watch, event)

    assert result.status == "completed"
    assert result.notification_created is True
    assert result.request_attempts == 3
    assert result.usage_complete is True
    assert watches.list_notifications().data["notifications"][0]["source"] == "agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_tool", ["search_events", "find_object"])
async def test_event_evidence_review_cannot_precede_scene_review(tmp_path, services, evidence_tool):
    store, watches = services
    watch, event = make_claimed_event(store, watches)
    start = (event.confirmed_at - timedelta(minutes=1)).isoformat()
    end = (event.confirmed_at + timedelta(minutes=1)).isoformat()
    notify_arguments = (
        f'{{"watch_id":"{watch.watch_id}","event_id":"{event.event_id}",'
        '"message":"反序核验不应创建 Agent 提醒。"}'
    )
    model = ScriptedModel(
        [
            [
                tool_call(
                    evidence_tool,
                    f'{{"category":"cup","start":"{start}","end":"{end}"}}'
                    if evidence_tool == "search_events"
                    else '{"category":"cup"}',
                    "call-events-first",
                )
            ],
            [tool_call("get_current_scene", "{}", "call-scene-second")],
            [tool_call("notify_user", notify_arguments, "call-notify-third")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_event(watch, event)

    assert result.status == "fallback_notified"
    agent_notify = next(call for call in result.tool_calls if call["tool"] == "notify_user")
    assert agent_notify == {
        "tool": "notify_user",
        "ok": False,
        "error": "event scene and evidence were not reviewed",
    }
    notification = watches.list_notifications().data["notifications"][0]
    assert notification["source"] == "fallback"


@pytest.mark.asyncio
async def test_failed_later_attempt_retains_known_partial_usage(tmp_path, services):
    store, watches = services
    model = FailAfterModel(
        [
            [tool_call("list_watches", "{}", "call-one")],
            [tool_call("list_watches", "{}", "call-two")],
        ],
        fail_after=2,
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_user("查询三次", "request-partial-usage")

    assert result.status == "failed"
    assert result.request_attempts == 3
    assert result.input_tokens == 20
    assert result.total_tokens == 28
    assert result.usage_complete is False
    usage = store.get_usage_summary(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    assert usage["total_tokens"] is None
    assert usage["known_total_tokens"] == 28
    assert usage["usage_complete"] is False


@pytest.mark.asyncio
async def test_event_without_model_uses_persisted_local_fallback(tmp_path, services):
    store, watches = services
    watch, event = make_claimed_event(store, watches)
    service = AgentService(Config(data_dir=tmp_path), store, watches)

    result = await service.run_event(watch, event)

    assert result.status == "fallback_notified"
    assert result.notification_created is True
    assert result.request_attempts == 0
    notification = watches.list_notifications().data["notifications"][0]
    assert notification["source"] == "fallback"
    assert "本地规则降级提醒" in notification["message"]


@pytest.mark.asyncio
async def test_event_agent_cannot_notify_another_target(tmp_path, services):
    store, watches = services
    watch, event = make_claimed_event(store, watches)
    model = ScriptedModel(
        [
            [
                tool_call(
                    "notify_user",
                    '{"watch_id":"wrong","event_id":"wrong","message":"错误提醒"}',
                    "call-wrong",
                )
            ],
            [message("完成")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model)

    result = await service.run_event(watch, event)

    assert result.status == "fallback_notified"
    assert result.notification_created is True
    notifications = watches.list_notifications().data["notifications"]
    assert len(notifications) == 1
    assert notifications[0]["watch_id"] == watch.watch_id
    assert notifications[0]["event_id"] == event.event_id
    assert notifications[0]["source"] == "fallback"
    assert result.request_attempts == 2


@pytest.mark.asyncio
async def test_behavior_statistics_uses_existing_scene_tool(tmp_path, services):
    import json

    from visual_ai_agent.behavior_models import BehaviorObservation
    from visual_ai_agent.behavior_store import BehaviorStore

    store, watches = services
    base = datetime.now(UTC) - timedelta(seconds=1)
    behavior = BehaviorStore(store)
    for i in range(3):
        behavior.ingest(
            BehaviorObservation(
                observed_at=base + timedelta(seconds=i / 10),
                monotonic_at=i / 10,
                status="running",
                fresh=True,
                posture="seated",
                model_version="test",
                scene_id="scene",
                source="test",
            )
        )
    model = ScriptedModel(
        [
            [tool_call("get_current_scene", json.dumps({"scope": "statistics"}), "stats")],
            [message("有效在座时长来自统计工具，不能代表学习或专注。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model, behavior=behavior)
    result = await service.run_user("今天在座多久？", "behavior-stats")
    assert result.status == "completed"
    assert len(service.tools) == 7
    assert result.tool_calls[0]["tool"] == "get_current_scene"
    assert result.tool_calls[0]["ok"]
    assert behavior.statistics().data["seated_seconds"] == pytest.approx(0.2)


def make_context_job(store):
    from visual_ai_agent.context_rules import ContextRuleService

    base = datetime.now(UTC) - timedelta(seconds=2)
    rules = ContextRuleService(store, clock=lambda: base)
    created = rules.create_rule("left_seat", request_id="rule-create", message="离开前检查手机。")
    rules.clock = store.clock
    jobs = rules.process_event(
        {
            "kind": "left_seat",
            "event_id": "behavior-left",
            "source": "test",
            "scene_id": "scene",
            "model_version": "test",
            "confirmed_at": base + timedelta(seconds=1),
        }
    )
    assert created.ok and len(jobs) == 1
    return rules, jobs[0]


@pytest.mark.asyncio
async def test_rule_agent_reviews_snapshot_then_notifies_once(tmp_path, services):
    import json

    store, watches = services
    rules, job = make_context_job(store)
    model = ScriptedModel(
        [
            [tool_call("get_current_scene", "{}", "scene")],
            [
                tool_call(
                    "search_events",
                    json.dumps(
                        {
                            "scope": "rule",
                            "rule_id": job["rule_id"],
                            "event_id": job["event_id"],
                        }
                    ),
                    "trigger",
                )
            ],
            [
                tool_call(
                    "notify_user",
                    json.dumps(
                        {
                            "watch_id": job["rule_id"],
                            "event_id": job["event_id"],
                            "message": "触发时确认离座，请检查物品。",
                        }
                    ),
                    "notify",
                )
            ],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model, rules=rules)
    result = await service.run_rule(job)
    assert result.status == "completed"
    assert result.notification_created
    assert [call["tool"] for call in result.tool_calls] == [
        "get_current_scene",
        "search_events",
        "notify_user",
    ]
    assert rules.list_notifications().data["notifications"][0]["source"] == "agent"
    repeated = await service.run_rule(job)
    assert not repeated.request_attempts
    assert len(rules.list_notifications().data["notifications"]) == 1


@pytest.mark.asyncio
async def test_rule_without_agent_falls_back_and_cancelled_job_never_calls_model(
    tmp_path, services
):
    store, watches = services
    rules, job = make_context_job(store)
    service = AgentService(Config(data_dir=tmp_path), store, watches, rules=rules)
    result = await service.run_rule(job)
    assert result.status == "fallback_notified"
    assert rules.list_notifications().data["notifications"][0]["source"] == "fallback"
    rules.cancel_rule(job["rule_id"])
    model = ScriptedModel([[message("不应调用")]])
    service = AgentService(configured(tmp_path), store, watches, model=model, rules=rules)
    result = await service.run_rule(job)
    assert not model.calls and result.request_attempts == 0


@pytest.mark.asyncio
async def test_natural_rule_tools_create_disable_and_cancel(tmp_path, services):
    import json

    from visual_ai_agent.context_rules import ContextRuleService

    store, watches = services
    rules = ContextRuleService(store)
    model = ScriptedModel(
        [
            [
                tool_call(
                    "create_watch",
                    json.dumps(
                        {
                            "target": "rule",
                            "trigger": "seated_duration",
                            "seated_minutes": 45,
                            "message": "请休息一下。",
                        }
                    ),
                    "create",
                )
            ],
            [message("已创建连续有效在座45分钟的页面提醒。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model, rules=rules)
    result = await service.run_user("连续坐45分钟提醒休息", "create-rule")
    assert result.status == "completed"
    assert len(rules.list_rules().data["rules"]) == 1
    rule = rules.list_rules().data["rules"][0]
    model.outputs = [
        [
            tool_call(
                "create_watch",
                json.dumps(
                    {
                        "target": "rule",
                        "action": "disable",
                        "rule_id": rule["rule_id"],
                    }
                ),
                "disable",
            )
        ],
        [message("已停用。")],
    ]
    model.calls.clear()
    result = await service.run_user("停用休息提醒", "disable-rule")
    assert result.status == "completed"
    assert not rules.get_rule(rule["rule_id"]).data["rule"]["enabled"]
    model.outputs = [
        [
            tool_call(
                "cancel_watch",
                json.dumps(
                    {
                        "target": "rule",
                        "watch_id": rule["rule_id"],
                    }
                ),
                "cancel",
            )
        ],
        [message("已取消。")],
    ]
    model.calls.clear()
    assert (await service.run_user("取消规则", "cancel-rule")).status == "completed"
    assert rules.get_rule(rule["rule_id"]).data["rule"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_partial_rule_update_preserves_unspecified_conditions(tmp_path, services):
    import json

    from visual_ai_agent.context_rules import ContextRuleService

    store, watches = services
    rules = ContextRuleService(store)
    created = rules.create_rule(
        "left_seat",
        request_id="initial",
        message="检查手机",
        after_time="18:00",
        object_category="cell phone",
        region="right",
    )
    rule_id = created.data["rule"]["rule_id"]
    model = ScriptedModel(
        [
            [
                tool_call(
                    "create_watch",
                    json.dumps(
                        {
                            "target": "rule",
                            "action": "update",
                            "rule_id": rule_id,
                            "after_time": "19:00",
                        }
                    ),
                    "patch",
                )
            ],
            [message("已调整时间。")],
        ]
    )
    service = AgentService(configured(tmp_path), store, watches, model=model, rules=rules)
    assert (await service.run_user("改为19点以后", "patch-time")).status == "completed"
    saved = rules.get_rule(rule_id).data["rule"]
    assert saved["after_time"] == "19:00"
    assert saved["message"] == "检查手机"
    assert saved["trigger"] == "left_seat" and saved["object_category"] == "cell phone"
    assert saved["region"] == "right"
