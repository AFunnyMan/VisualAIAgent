"""Single-agent tool loop over local, text-only visual memory facts."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from agents import (
    Agent,
    Model,
    ModelResponse,
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    ToolExecutionConfig,
    function_tool,
)
from agents.model_settings import ModelRetrySettings
from openai import AsyncOpenAI

from .config import Config
from .models import Category, Condition, ToolResult, VisualEvent, WatchTask, utcnow

RunKind = Literal["user", "event"]
RunStatus = Literal[
    "completed",
    "not_connected",
    "limit_reached",
    "timeout",
    "failed",
    "fallback_notified",
]


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    request_id: str
    kind: RunKind
    status: RunStatus
    message: str
    request_attempts: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    usage_complete: bool
    tool_calls: tuple[dict[str, Any], ...] = ()
    notification_created: bool = False


@dataclass
class _AgentContext:
    memory: Any
    watches: Any
    run_id: str
    request_id: str
    kind: RunKind
    behavior: Any = None
    laptop: Any = None
    rules: Any = None
    rule_job: dict[str, Any] | None = None
    event_watch_id: str | None = None
    event_id: str | None = None
    event_category: Category | None = None
    event_evidence_id: str | None = None
    reviewed_scene: bool = False
    reviewed_event: bool = False
    notification_created: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class _AttemptHooks(RunHooks[_AgentContext]):
    def __init__(self) -> None:
        self.attempts = 0
        self.responses: list[ModelResponse] = []

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self.attempts += 1

    async def on_llm_end(self, context, agent, response: ModelResponse) -> None:
        self.responses.append(response)


def _safe_result(result: ToolResult) -> str:
    """Return only the validated business result; it never contains evidence paths."""
    return result.model_dump_json(exclude_none=True)


def _tool_summary(name: str, result: ToolResult) -> dict[str, Any]:
    summary: dict[str, Any] = {"tool": name, "ok": result.ok}
    if result.error:
        summary["error"] = result.error[:160]
    for key in (
        "category",
        "found",
        "current",
        "effective_status",
        "truncated",
        "deduplicated",
        "changed",
        "previous_status",
    ):
        if key in result.data and isinstance(result.data[key], (str, int, float, bool, type(None))):
            summary[key] = result.data[key]
    for key in ("events", "watches", "rules"):
        if isinstance(result.data.get(key), list):
            summary[f"{key}_count"] = len(result.data[key])
    for key in ("watch", "notification", "rule"):
        value = result.data.get(key)
        if isinstance(value, dict):
            for id_key in ("watch_id", "rule_id", "event_id", "notification_id", "status"):
                if isinstance(value.get(id_key), str):
                    summary[id_key] = value[id_key]
    return summary


async def _invoke(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _usage_from_responses(
    responses: list[ModelResponse],
) -> tuple[int | None, int | None, int | None]:
    """Aggregate usage without converting absent provider fields to zero."""
    if not responses:
        return None, None, None

    raw_items = [
        cast(dict[str, Any], response.raw_usage)
        for response in responses
        if isinstance(response.raw_usage, dict)
    ]

    def total(*names: str) -> int | None:
        values: list[int] = []
        for item in raw_items:
            value = next((item[name] for name in names if name in item), None)
            if isinstance(value, int) and not isinstance(value, bool):
                values.append(value)
        return sum(values) if values else None

    return (
        total("input_tokens", "prompt_tokens"),
        total("output_tokens", "completion_tokens"),
        total("total_tokens"),
    )


def _usage_is_complete(responses: list[ModelResponse], attempts: int) -> bool:
    if len(responses) != attempts or not responses:
        return False
    for response in responses:
        raw = response.raw_usage
        if not isinstance(raw, dict):
            return False
        fields = (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
            ("total_tokens",),
        )
        for names in fields:
            value = next((raw[name] for name in names if name in raw), None)
            if not isinstance(value, int) or isinstance(value, bool):
                return False
    return True


def _error_code(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    name = type(error).__name__.lower()
    if "maxturn" in name:
        return "model_turn_limit"
    if "validation" in name or "behavior" in name:
        return "model_behavior"
    return "model_request_failed"


class AgentService:
    """Own exactly one SDK Agent with seven narrow business function tools."""

    def __init__(
        self,
        config: Config,
        memory: Any,
        watch_service: Any,
        model: Model | None = None,
        *,
        behavior: Any = None,
        laptop: Any = None,
        rules: Any = None,
    ) -> None:
        self.config = config
        self.memory = memory
        self.watch_service = watch_service
        self.behavior = behavior
        self.laptop = laptop
        self.rules = rules
        self._injected_model = model is not None
        self._client: AsyncOpenAI | None = None
        self._closed = False
        self._model = model or self._configured_model()
        self._tools = self._build_tools()
        self._agent = (
            Agent[_AgentContext](
                name="VisualMemoryAgent",
                instructions=self._instructions,
                model=self._model,
                tools=self._tools,
                model_settings=ModelSettings(
                    tool_choice="required",
                    parallel_tool_calls=False,
                    include_usage=True,
                    preserve_raw_usage=True,
                    timeout=config.api_timeout_seconds,
                    retry=ModelRetrySettings(max_retries=0),
                ),
            )
            if self._model is not None
            else None
        )

    @property
    def tools(self) -> tuple[Any, ...]:
        return tuple(self._tools)

    def _configured_model(self) -> Model | None:
        if not self.config.agent_connected:
            return None
        client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.api_base_url,
            timeout=self.config.api_timeout_seconds,
            max_retries=0,
        )
        self._client = client
        if self.config.api_mode == "chat_completions":
            return OpenAIChatCompletionsModel(
                model=self.config.agent_model,
                openai_client=client,
            )
        return OpenAIResponsesModel(
            model=self.config.agent_model,
            openai_client=client,
        )

    async def close(self) -> None:
        """Release model transports before the owning event loop is closed."""
        if self._closed:
            return
        self._closed = True
        if self._model is not None:
            await self._model.close()
        if self._client is not None:
            await self._client.close()

    def _instructions(self, context: RunContextWrapper[_AgentContext], agent) -> str:
        now = utcnow().astimezone(ZoneInfo(self.config.timezone))
        base = (
            "你是本机视觉记忆助手。只能根据七个业务工具返回的文字事实回答；未知就明确说未知。"
            "物品类别不是身份，多个同类必须展示候选。当前状态必须依据get_current_scene，历史位置必须依据"
            "find_object，且答复必须引用工具返回的类别、时间、区域和证据编号；多个候选必须逐项列出，"
            "不得认定为同一件或特定身份。历史事件必须依据search_events，答复必须引用事件时间和证据。"
            "不得声称看过图片，也不得推测谁移动了物品。对于missing事件只能表述为“持续未检测到”，"
            "不得推断物品被拿走、发生物理消失或由某人移动。"
            "历史事件只能列出search_events实际返回的记录，不能为凑齐用户问到的事件类型而补写事件。"
            "若结果没有missing记录，必须明确说未找到持续未检测到事件，不得把类型说明写成已发生。"
            "创建、取消和通知只有工具返回ok才算成功。不要输出本地路径。"
            f"当前时间是{now.isoformat()}，时区是{self.config.timezone}。"
            "处理最近一小时等相对时间查询时，以这个当前时间计算带时区的start和end，并确保start<end。"
        )
        base += (
            "行为属于实验模型的观察：在座不等于学习或专注，疑似饮水不证明吞咽或饮水量。"
            "当前行为用get_current_scene(scope='behavior')，每日时长用scope='statistics'及YYYY-MM-DD日期。"
            "行为历史用search_events(scope='behavior')；统计只能引用工具实际返回的有效时长，未知不能补算。"
            "情境规则用create_watch(target='rule')；action=create/update/enable/disable，修改须提供rule_id。"
            "规则触发支持stood_up/sat_down/left_seat/seat_occupied/suspected_drink/seated_duration。"
            "seated_duration需seated_minutes；可选after_time为HH:MM严格晚于，object_category和region限制现有物品区域。"
            "update仅传需要修改的字段，未提供字段保持不变；移除时间条件用clear_after_time=true，"
            "移除物品及区域条件用clear_object_condition=true。先list_watches确定要改的rule_id。"
            "未指定时间条件时传null；region默认any。message是用户希望收到的提醒内容。"
            "规则默认长期有效，创建后必须说明触发、条件、实验性和页面提醒方式。"
            "list_watches也返回rules；取消规则用cancel_watch(target='rule',watch_id=rule_id)。"
            "笔记本开合是独立实验事实：当前状态用get_current_scene(scope='laptop')，历史用"
            "search_events(scope='laptop')。只有工具返回available=true且current=true时才能称为开着或合上；"
            "unknown、遮挡、不在场、过期或能力未验收都必须明确说无法确认。"
            "情境规则可用laptop_closed/laptop_opened触发，但创建前须用scope='laptop'确认能力available=true。"
            "不支持钥匙、手机使用判断、自定义命名区域或外部推送，不得创建此类规则或虚报成功。"
        )
        if context.context.rule_job is not None:
            return base + (
                "这是情境规则自动复查。先get_current_scene，再search_events(scope='rule',rule_id,event_id)"
                "读取触发时已保存的规则与条件事实，最后notify_user(watch_id=rule_id,event_id,message)。"
                "提醒说明触发时间，物品位置只能表述为触发时观察，不能将历史快照称为现在。"
                "不要创建、修改或取消规则。只有给定规则和事件可通知。"
            )
        if context.context.kind == "event":
            return base + (
                "这是自动事件复查。必须先调用get_current_scene，再用search_events核验目标事件证据；"
                "确认后只能用notify_user提醒给定watch_id/event_id。工具会强制检查这些条件。"
                "如果事实不支持提醒，解释原因，不要调用其他任务的通知。"
            )
        return base

    def _build_tools(self) -> list[Any]:
        async def finish(
            wrapper: RunContextWrapper[_AgentContext],
            name: str,
            operation: Callable[[], Any],
            after: Callable[[ToolResult], None] | None = None,
        ) -> str:
            try:
                result = await _invoke(operation())
                if not isinstance(result, ToolResult):
                    result = ToolResult(ok=False, error="business service returned invalid result")
            except (ValueError, TypeError):
                result = ToolResult(ok=False, error="invalid business request")
            except Exception:
                result = ToolResult(ok=False, error="business service unavailable")
            if after is not None:
                after(result)
            summary = _tool_summary(name, result)
            wrapper.context.tool_calls.append(summary)
            try:
                await _invoke(
                    wrapper.context.memory.record_tool_run(
                        run_id=wrapper.context.run_id,
                        tool_name=name,
                        ok=result.ok,
                        summary=summary,
                    )
                )
            except Exception:
                pass
            return _safe_result(result)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def get_current_scene(
            ctx: RunContextWrapper[_AgentContext],
            scope: Literal["scene", "behavior", "statistics", "laptop"] = "scene",
            local_date: str | None = None,
        ) -> str:
            """Read current facts or observed behavior durations; statistics date is YYYY-MM-DD."""

            def operation():
                if scope != "scene":
                    if scope == "laptop":
                        if ctx.context.laptop is None:
                            return ToolResult(ok=False, error="laptop service unavailable")
                        return ctx.context.laptop.current()
                    if ctx.context.behavior is None:
                        return ToolResult(ok=False, error="behavior service unavailable")
                    if scope == "statistics":
                        return ctx.context.behavior.statistics(
                            date.fromisoformat(local_date) if local_date else None
                        )
                    return ctx.context.behavior.current()
                result = ctx.context.memory.get_current_scene()
                if ctx.context.behavior is not None:
                    result = result.model_copy(deep=True)
                    result.data["behavior"] = ctx.context.behavior.current().data
                if ctx.context.laptop is not None:
                    result = result.model_copy(deep=True)
                    result.data["laptop"] = ctx.context.laptop.current().data
                return result

            def reviewed(result: ToolResult) -> None:
                if result.ok and scope == "scene":
                    ctx.context.reviewed_scene = True

            return await finish(ctx, "get_current_scene", operation, reviewed)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def find_object(ctx: RunContextWrapper[_AgentContext], category: Category) -> str:
            """Find the last recorded candidates for one supported object category."""

            def reviewed(result: ToolResult) -> None:
                if (
                    result.ok
                    and ctx.context.reviewed_scene
                    and ctx.context.event_id
                    and category == ctx.context.event_category
                    and result.data.get("evidence_id") == ctx.context.event_evidence_id
                ):
                    ctx.context.reviewed_event = True

            return await finish(
                ctx,
                "find_object",
                lambda: ctx.context.memory.find_object(category),
                reviewed,
            )

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def search_events(
            ctx: RunContextWrapper[_AgentContext],
            category: Category | None = None,
            start: datetime | None = None,
            end: datetime | None = None,
            scope: Literal["objects", "behavior", "laptop", "rule"] = "objects",
            rule_id: str | None = None,
            event_id: str | None = None,
        ) -> str:
            """Search with aware datetimes; require start strictly before end, or inspect a rule."""

            def operation():
                if scope == "rule":
                    if ctx.context.rules is None or not rule_id or not event_id:
                        return ToolResult(ok=False, error="rule_id and event_id required")
                    return ctx.context.rules.get_job(rule_id, event_id)
                if start is None or end is None:
                    return ToolResult(ok=False, error="start and end required")
                if scope == "behavior":
                    if ctx.context.behavior is None:
                        return ToolResult(ok=False, error="behavior service unavailable")
                    return ctx.context.behavior.search_events(start, end, limit=20)
                if scope == "laptop":
                    if ctx.context.laptop is None:
                        return ToolResult(ok=False, error="laptop service unavailable")
                    return ctx.context.laptop.search_events(start, end, limit=20)
                if category is None:
                    return ToolResult(ok=False, error="category required")
                return ctx.context.memory.search_events(category, start, end, limit=20)

            def reviewed(result: ToolResult) -> None:
                context = ctx.context
                if result.ok and context.reviewed_scene and context.event_id:
                    if context.rule_job is not None and (
                        scope != "rule" or rule_id != context.event_watch_id
                    ):
                        return
                    events = result.data.get("events", [])
                    if any(
                        isinstance(item, dict) and item.get("event_id") == context.event_id
                        for item in events
                    ):
                        context.reviewed_event = True

            return await finish(ctx, "search_events", operation, reviewed)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def create_watch(
            ctx: RunContextWrapper[_AgentContext],
            category: Category | None = None,
            condition: Condition | None = None,
            duration_minutes: int = 30,
            target: Literal["watch", "rule"] = "watch",
            action: Literal["create", "update", "enable", "disable"] = "create",
            rule_id: str | None = None,
            trigger: Literal[
                "stood_up",
                "sat_down",
                "left_seat",
                "seat_occupied",
                "suspected_drink",
                "seated_duration",
                "laptop_closed",
                "laptop_opened",
            ]
            | None = None,
            after_time: str | None = None,
            object_category: Category | None = None,
            region: Literal["any", "left", "center", "right"] | None = None,
            seated_minutes: int | None = None,
            message: str | None = None,
            clear_after_time: bool = False,
            clear_object_condition: bool = False,
        ) -> str:
            """Create an object watch or create/update/enable/disable a typed contextual rule."""

            def operation():
                context = ctx.context
                if context.kind != "user":
                    return ToolResult(ok=False, error="automatic runs cannot modify tasks")
                if target == "watch":
                    if category is None or condition is None or action != "create":
                        return ToolResult(ok=False, error="object category and condition required")
                    return context.watches.create_watch(
                        category, condition, duration_minutes, request_id=context.request_id
                    )
                if context.rules is None:
                    return ToolResult(ok=False, error="rule service unavailable")
                if action in ("enable", "disable"):
                    if not rule_id:
                        return ToolResult(ok=False, error="rule_id required")
                    return context.rules.update_rule(
                        rule_id, enabled=action == "enable", request_id=context.request_id
                    )
                values = dict(
                    trigger=trigger,
                    after_time=after_time,
                    object_category=object_category,
                    region=region,
                    seated_minutes=seated_minutes,
                    message=message,
                )
                if action == "update":
                    if not rule_id:
                        return ToolResult(ok=False, error="rule_id required")
                    changes = {key: value for key, value in values.items() if value is not None}
                    if clear_after_time:
                        changes["after_time"] = None
                    if clear_object_condition:
                        changes.update(object_category=None, region="any")
                    if trigger is not None and trigger != "seated_duration":
                        changes["seated_minutes"] = None
                    if not changes:
                        return ToolResult(ok=False, error="no rule changes supplied")
                    return context.rules.update_rule(
                        rule_id, **changes, request_id=context.request_id
                    )
                if trigger is None:
                    return ToolResult(ok=False, error="trigger required")
                values["region"] = region or "any"
                values["message"] = message or "请留意本次行为事件。"
                return context.rules.create_rule(**values, request_id=context.request_id)

            return await finish(ctx, "create_watch", operation)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def list_watches(ctx: RunContextWrapper[_AgentContext]) -> str:
            """List object watches and contextual rules with structured conditions."""

            def operation():
                result = ctx.context.watches.list_watches().model_copy(deep=True)
                if ctx.context.rules is not None:
                    result.data["rules"] = ctx.context.rules.list_rules().data.get("rules", [])
                return result

            return await finish(ctx, "list_watches", operation)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def cancel_watch(
            ctx: RunContextWrapper[_AgentContext],
            watch_id: str,
            target: Literal["watch", "rule"] = "watch",
        ) -> str:
            """Cancel an object watch or a contextual rule (watch_id is the rule_id for rules)."""

            def operation():
                if ctx.context.kind != "user":
                    return ToolResult(ok=False, error="automatic runs cannot modify tasks")
                if target == "rule":
                    if ctx.context.rules is None:
                        return ToolResult(ok=False, error="rule service unavailable")
                    return ctx.context.rules.cancel_rule(watch_id)
                return ctx.context.watches.cancel_watch(watch_id)

            return await finish(ctx, "cancel_watch", operation)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def notify_user(
            ctx: RunContextWrapper[_AgentContext],
            watch_id: str,
            event_id: str,
            message: str,
        ) -> str:
            """Create one validated notification for a watch and its matching visual event."""
            context = ctx.context
            if context.kind == "event":
                if watch_id != context.event_watch_id or event_id != context.event_id:
                    result = ToolResult(ok=False, error="notification target is outside this event")
                    return await finish(ctx, "notify_user", lambda: result)
                if not context.reviewed_scene or not context.reviewed_event:
                    result = ToolResult(
                        ok=False, error="event scene and evidence were not reviewed"
                    )
                    return await finish(ctx, "notify_user", lambda: result)

            def notified(result: ToolResult) -> None:
                if result.ok:
                    context.notification_created = True

            return await finish(
                ctx,
                "notify_user",
                lambda: (
                    context.rules.notify(
                        watch_id,
                        context.rule_job["rule_version"],
                        event_id,
                        message,
                        source="agent",
                    )
                    if context.rule_job is not None
                    else context.watches.notify_user(watch_id, event_id, message, source="agent")
                ),
                notified,
            )

        return [
            get_current_scene,
            find_object,
            search_events,
            create_watch,
            list_watches,
            cancel_watch,
            notify_user,
        ]

    def _history_input(self, message: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            interactions = self.memory.list_chat_interactions(limit=5)
        except Exception:
            interactions = []
        for item in interactions:
            user_message = item.get("user_message")
            assistant_message = item.get("assistant_message")
            if isinstance(user_message, str) and isinstance(assistant_message, str):
                items.extend(
                    [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": assistant_message},
                    ]
                )
        items.append({"role": "user", "content": message})
        return items

    async def run_user(self, message: str, request_id: str) -> AgentRunResult:
        if not message.strip():
            raise ValueError("message is required")
        if not request_id.strip():
            raise ValueError("request_id is required")
        return await self._run(
            kind="user",
            prompt=self._history_input(message),
            request_id=request_id,
            user_message=message,
        )

    async def run_event(self, watch: WatchTask, event: VisualEvent) -> AgentRunResult:
        request_id = f"event:{watch.watch_id}:{event.event_id}"
        run_id = uuid4().hex
        now = utcnow()
        local_date = now.astimezone(ZoneInfo(self.config.timezone)).date()
        reserved = await _invoke(
            self.memory.reserve_event_agent_run(
                run_id,
                request_id,
                local_date,
                self.config.daily_auto_limit,
                started_at=now,
                model=self.config.agent_model or ("injected" if self._injected_model else None),
            )
        )
        if not reserved:
            return await self._fallback(
                run_id=run_id,
                request_id=request_id,
                watch=watch,
                event=event,
                reason="automatic model limit or duplicate event run",
                status="limit_reached",
                started_at=now,
                reserved=False,
                local_date=local_date,
            )
        search_start = event.confirmed_at - timedelta(minutes=2)
        search_end = max(event.confirmed_at + timedelta(minutes=2), now + timedelta(seconds=1))
        prompt = (
            "复查并处理以下已匹配关注事件。"
            f"watch_id={watch.watch_id}; event_id={event.event_id}; category={event.category}; "
            f"condition={event.kind}; confirmed_at={event.confirmed_at.isoformat()}; "
            f"evidence_id={event.evidence_id or 'none'}; current_time={now.isoformat()}; "
            f"search_start={search_start.isoformat()}; search_end={search_end.isoformat()}。"
            "最多只有三次模型请求，必须按请求顺序完成：第一轮get_current_scene；第二轮用上述"
            "search_start和search_end调用search_events核验目标event_id（时间均带时区且start<end）；"
            "第三轮仅当工具事实支持时调用notify_user创建一次提醒。"
        )
        return await self._run(
            kind="event",
            prompt=prompt,
            request_id=request_id,
            run_id=run_id,
            started_at=now,
            watch=watch,
            event=event,
            local_date=local_date,
        )

    async def run_rule(self, job: dict[str, Any]) -> AgentRunResult:
        """Review one durable contextual trigger using the same seven tools and daily budget."""
        request_id = f"rule:{job['rule_id']}:{job['rule_version']}:{job['event_id']}"
        now = utcnow()
        run_id = uuid4().hex
        local_date = now.astimezone(ZoneInfo(self.config.timezone)).date()
        claim = self.rules.claim_job(job["rule_id"], job["rule_version"], job["event_id"])
        active = claim.ok and claim.data.get("claimed") is True
        if not active:
            return AgentRunResult(
                run_id=run_id,
                request_id=request_id,
                kind="event",
                status="failed",
                message="规则已停用、修改、取消或通知已处理；未调用模型。",
                request_attempts=0,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                usage_complete=False,
            )
        reserved = await _invoke(
            self.memory.reserve_event_agent_run(
                run_id,
                request_id,
                local_date,
                self.config.daily_auto_limit,
                started_at=now,
                model=self.config.agent_model or ("injected" if self._injected_model else None),
            )
        )
        if not reserved:
            result = self.rules.fallback(job)
            return AgentRunResult(
                run_id=run_id,
                request_id=request_id,
                kind="event",
                status="fallback_notified" if result.ok else "limit_reached",
                message="自动额度不足或重复任务；已尝试本地降级提醒。",
                request_attempts=0,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                usage_complete=False,
                notification_created=result.ok,
            )
        prompt = (
            f"复核情境规则 rule_id={job['rule_id']}; event_id={job['event_id']}; "
            f"rule_version={job['rule_version']}。最多三轮：第一轮get_current_scene；"
            "第二轮search_events(scope='rule',rule_id=上述编号,event_id=上述编号)；"
            "第三轮依据返回的触发时事实notify_user(watch_id=rule_id,event_id,message)。"
            "提醒注明触发时间，不将触发时物品事实表述为现在。"
        )
        return await self._run(
            kind="event",
            prompt=prompt,
            request_id=request_id,
            run_id=run_id,
            started_at=now,
            local_date=local_date,
            rule_job=job,
        )

    async def _rule_fallback(
        self, context, started_at, local_date, reason, attempts, usage, usage_complete
    ) -> AgentRunResult:
        try:
            result = self.rules.fallback(context.rule_job)
        except Exception:
            result = ToolResult(ok=False, error="local rule notification unavailable")
        context.notification_created = result.ok
        context.tool_calls.append(_tool_summary("notify_user_fallback", result))
        return await self._finish_local(
            context,
            status="fallback_notified" if result.ok else "failed",
            message="本地规则降级提醒已写入。" if result.ok else "规则已失效或提醒未写入。",
            started_at=started_at,
            attempts=attempts,
            usage=usage,
            usage_complete=usage_complete,
            error=reason,
            user_message=None,
            local_date=local_date,
        )

    async def _run(
        self,
        *,
        kind: RunKind,
        prompt: str | list[dict[str, Any]],
        request_id: str,
        user_message: str | None = None,
        run_id: str | None = None,
        started_at: datetime | None = None,
        watch: WatchTask | None = None,
        event: VisualEvent | None = None,
        rule_job: dict[str, Any] | None = None,
        local_date=None,
    ) -> AgentRunResult:
        run_id = run_id or uuid4().hex
        started_at = started_at or utcnow()
        local_date = local_date or started_at.astimezone(ZoneInfo(self.config.timezone)).date()
        model_name = self.config.agent_model or ("injected" if self._injected_model else None)
        context = _AgentContext(
            memory=self.memory,
            watches=self.watch_service,
            run_id=run_id,
            request_id=request_id,
            kind=kind,
            behavior=self.behavior,
            laptop=self.laptop,
            rules=self.rules,
            rule_job=rule_job,
            event_watch_id=rule_job["rule_id"] if rule_job else (watch.watch_id if watch else None),
            event_id=rule_job["event_id"] if rule_job else (event.event_id if event else None),
            event_category=event.category if event else None,
            event_evidence_id=event.evidence_id if event else None,
        )
        if kind == "user":
            await _invoke(
                self.memory.record_agent_run(
                    run_id=run_id,
                    request_id=request_id,
                    kind=kind,
                    status="running",
                    started_at=started_at,
                    finished_at=None,
                    model=model_name,
                    request_attempts=0,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    error=None,
                    tool_summary=[],
                )
            )
        if self._agent is None:
            if rule_job is not None:
                return await self._rule_fallback(
                    context,
                    started_at,
                    local_date,
                    "agent_not_connected",
                    0,
                    (None, None, None),
                    False,
                )
            if kind == "event" and watch is not None and event is not None:
                return await self._fallback(
                    run_id=run_id,
                    request_id=request_id,
                    watch=watch,
                    event=event,
                    reason="agent not connected",
                    status="not_connected",
                    started_at=started_at,
                    reserved=True,
                    local_date=local_date,
                )
            return await self._finish_local(
                context,
                status="not_connected",
                message="Agent 未连接：请配置明确的 API 地址、模型名称和密钥。",
                started_at=started_at,
                attempts=0,
                usage=(None, None, None),
                usage_complete=False,
                error="agent_not_connected",
                user_message=user_message,
                local_date=local_date,
            )

        hooks = _AttemptHooks()
        responses: list[ModelResponse] = []
        try:
            async with asyncio.timeout(self.config.api_timeout_seconds):
                sdk_result = await Runner.run(
                    self._agent,
                    cast(Any, prompt),
                    context=context,
                    max_turns=3,
                    hooks=hooks,
                    run_config=RunConfig(
                        tracing_disabled=True,
                        trace_include_sensitive_data=False,
                        workflow_name="VisualMemoryAgent",
                        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    ),
                )
            responses = sdk_result.raw_responses
            usage = _usage_from_responses(responses)
            message = str(sdk_result.final_output).strip() or "模型未返回文字答复。"
            if kind == "user" and not context.tool_calls:
                return await self._finish_local(
                    context,
                    status="failed",
                    message="模型未执行业务工具，无法确认请求结果。",
                    started_at=started_at,
                    attempts=hooks.attempts,
                    usage=usage,
                    usage_complete=_usage_is_complete(responses, hooks.attempts),
                    error="model_completed_without_tool",
                    user_message=user_message,
                    local_date=local_date,
                )
            if rule_job is not None and not context.notification_created:
                return await self._rule_fallback(
                    context,
                    started_at,
                    local_date,
                    "missing_validated_notification",
                    hooks.attempts,
                    usage,
                    _usage_is_complete(responses, hooks.attempts),
                )
            if kind == "event" and not context.notification_created and watch and event:
                return await self._fallback(
                    run_id=run_id,
                    request_id=request_id,
                    watch=watch,
                    event=event,
                    reason="agent completed without a validated notification",
                    status="failed",
                    started_at=started_at,
                    reserved=True,
                    local_date=local_date,
                    attempts=hooks.attempts,
                    usage=usage,
                    usage_complete=_usage_is_complete(responses, hooks.attempts),
                    tool_calls=context.tool_calls,
                )
            return await self._finish_local(
                context,
                status="completed",
                message=message,
                started_at=started_at,
                attempts=hooks.attempts,
                usage=usage,
                usage_complete=_usage_is_complete(responses, hooks.attempts),
                error=None,
                user_message=user_message,
                local_date=local_date,
            )
        except TimeoutError:
            responses = hooks.responses
            status: RunStatus = "timeout"
            code = "timeout"
        except Exception as error:
            responses = hooks.responses
            status = "failed"
            code = _error_code(error)

        usage = _usage_from_responses(responses)
        usage_complete = _usage_is_complete(responses, hooks.attempts)
        if kind == "event" and context.notification_created:
            return await self._finish_local(
                context,
                status="completed",
                message="提醒工具已成功写入；模型在生成最终答复前结束。",
                started_at=started_at,
                attempts=hooks.attempts,
                usage=usage,
                usage_complete=usage_complete,
                error=code,
                user_message=None,
                local_date=local_date,
            )

        if rule_job is not None:
            return await self._rule_fallback(
                context, started_at, local_date, code, hooks.attempts, usage, usage_complete
            )
        if kind == "event" and watch is not None and event is not None:
            return await self._fallback(
                run_id=run_id,
                request_id=request_id,
                watch=watch,
                event=event,
                reason=code,
                status=status,
                started_at=started_at,
                reserved=True,
                local_date=local_date,
                attempts=hooks.attempts,
                usage=usage,
                usage_complete=usage_complete,
                tool_calls=context.tool_calls,
            )
        return await self._finish_local(
            context,
            status=status,
            message="模型请求超时。" if status == "timeout" else "模型请求失败。",
            started_at=started_at,
            attempts=hooks.attempts,
            usage=usage,
            usage_complete=usage_complete,
            error=code,
            user_message=user_message,
            local_date=local_date,
        )

    async def _finish_local(
        self,
        context: _AgentContext,
        *,
        status: RunStatus,
        message: str,
        started_at: datetime,
        attempts: int,
        usage: tuple[int | None, int | None, int | None],
        usage_complete: bool,
        error: str | None,
        user_message: str | None,
        local_date,
    ) -> AgentRunResult:
        await _invoke(
            self.memory.record_agent_run(
                run_id=context.run_id,
                request_id=context.request_id,
                kind=context.kind,
                status=status,
                started_at=started_at,
                finished_at=utcnow(),
                model=self.config.agent_model or ("injected" if self._injected_model else None),
                request_attempts=attempts,
                input_tokens=usage[0],
                output_tokens=usage[1],
                total_tokens=usage[2],
                usage_complete=usage_complete,
                error=error,
                tool_summary=context.tool_calls,
                local_date=local_date,
            )
        )
        if context.kind == "user" and user_message is not None:
            await _invoke(
                self.memory.append_chat_interaction(
                    context.request_id,
                    user_message,
                    message,
                )
            )
        return AgentRunResult(
            run_id=context.run_id,
            request_id=context.request_id,
            kind=context.kind,
            status=status,
            message=message,
            request_attempts=attempts,
            input_tokens=usage[0],
            output_tokens=usage[1],
            total_tokens=usage[2],
            usage_complete=usage_complete,
            tool_calls=tuple(context.tool_calls),
            notification_created=context.notification_created,
        )

    async def _fallback(
        self,
        *,
        run_id: str,
        request_id: str,
        watch: WatchTask,
        event: VisualEvent,
        reason: str,
        status: RunStatus,
        started_at: datetime,
        reserved: bool,
        local_date,
        attempts: int = 0,
        usage: tuple[int | None, int | None, int | None] = (None, None, None),
        usage_complete: bool = False,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> AgentRunResult:
        summaries = list(tool_calls or [])
        try:
            scene_result = await _invoke(self.memory.get_current_scene())
            if isinstance(scene_result, ToolResult):
                summaries.append(_tool_summary("get_current_scene_fallback", scene_result))
            persisted_event = await _invoke(self.memory.get_event(event.event_id))
        except Exception:
            scene_result = ToolResult(ok=False, error="business service unavailable")
            persisted_event = None
        if not isinstance(persisted_event, VisualEvent):
            notification = ToolResult(ok=False, error="persisted event was not found")
            persisted_event = event
        else:
            notification = None
        evidence = persisted_event.evidence_id or "无截图证据编号"
        verb = "已出现" if persisted_event.kind == "appeared" else "持续未检测到"
        message = (
            f"本地规则降级提醒：关注的 {persisted_event.category} {verb}；"
            f"事件时间 {persisted_event.confirmed_at.isoformat()}，证据 {evidence}。"
        )
        if notification is None:
            try:
                notification = await _invoke(
                    self.watch_service.notify_user(
                        watch.watch_id,
                        persisted_event.event_id,
                        message,
                        source="fallback",
                    )
                )
            except Exception:
                notification = ToolResult(ok=False, error="business service unavailable")
        if not isinstance(notification, ToolResult):
            notification = ToolResult(ok=False, error="business service returned invalid result")
        created = isinstance(notification, ToolResult) and notification.ok
        final_status: RunStatus = "fallback_notified" if created else status
        summaries.append(_tool_summary("notify_user_fallback", notification))
        if reserved:
            await _invoke(
                self.memory.record_agent_run(
                    run_id=run_id,
                    request_id=request_id,
                    kind="event",
                    status=final_status,
                    started_at=started_at,
                    finished_at=utcnow(),
                    model=self.config.agent_model or ("injected" if self._injected_model else None),
                    request_attempts=attempts,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    total_tokens=usage[2],
                    usage_complete=usage_complete,
                    error=reason,
                    tool_summary=summaries,
                    local_date=local_date,
                )
            )
        return AgentRunResult(
            run_id=run_id,
            request_id=request_id,
            kind="event",
            status=final_status,
            message=message if created else "自动提醒未创建；任务可能已取消、到期或处理完成。",
            request_attempts=attempts,
            input_tokens=usage[0],
            output_tokens=usage[1],
            total_tokens=usage[2],
            usage_complete=usage_complete,
            tool_calls=tuple(summaries),
            notification_created=created,
        )
