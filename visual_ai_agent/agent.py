"""Single-agent tool loop over local, text-only visual memory facts."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
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
    for key in ("events", "watches"):
        if isinstance(result.data.get(key), list):
            summary[f"{key}_count"] = len(result.data[key])
    for key in ("watch", "notification"):
        value = result.data.get(key)
        if isinstance(value, dict):
            for id_key in ("watch_id", "event_id", "notification_id", "status"):
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
    ) -> None:
        self.config = config
        self.memory = memory
        self.watch_service = watch_service
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

    @staticmethod
    def _instructions(context: RunContextWrapper[_AgentContext], agent) -> str:
        base = (
            "你是本机视觉记忆助手。只能根据七个业务工具返回的文字事实回答；未知就明确说未知。"
            "物品类别不是身份，多个同类必须展示候选。当前状态必须依据get_current_scene，历史位置必须依据"
            "find_object，历史事件必须依据search_events。不得声称看过图片，也不得推测谁移动了物品。"
            "创建、取消和通知只有工具返回ok才算成功。不要输出本地路径。"
        )
        if context.context.kind == "event":
            return base + (
                "这是自动事件复查。必须先调用get_current_scene，并用search_events或find_object核验目标"
                "事件证据；确认后只能用notify_user提醒给定watch_id/event_id。工具会强制检查这些条件。"
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
        async def get_current_scene(ctx: RunContextWrapper[_AgentContext]) -> str:
            """Read the latest camera state and text detections, including stale/fault state."""

            def reviewed(result: ToolResult) -> None:
                if result.ok:
                    ctx.context.reviewed_scene = True

            return await finish(
                ctx,
                "get_current_scene",
                ctx.context.memory.get_current_scene,
                reviewed,
            )

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def find_object(ctx: RunContextWrapper[_AgentContext], category: Category) -> str:
            """Find the last recorded candidates for one supported object category."""

            def reviewed(result: ToolResult) -> None:
                if (
                    result.ok
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
            category: Category,
            start: datetime,
            end: datetime,
        ) -> str:
            """Search a bounded time range for up to twenty persisted visual events."""

            def reviewed(result: ToolResult) -> None:
                if result.ok and ctx.context.event_id:
                    events = result.data.get("events", [])
                    if any(
                        isinstance(item, dict) and item.get("event_id") == ctx.context.event_id
                        for item in events
                    ):
                        ctx.context.reviewed_event = True

            return await finish(
                ctx,
                "search_events",
                lambda: ctx.context.memory.search_events(category, start, end, limit=20),
                reviewed,
            )

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def create_watch(
            ctx: RunContextWrapper[_AgentContext],
            category: Category,
            condition: Condition,
            duration_minutes: int = 30,
        ) -> str:
            """Create one appeared/missing watch; repeated calls in this request are idempotent."""
            return await finish(
                ctx,
                "create_watch",
                lambda: ctx.context.watches.create_watch(
                    category,
                    condition,
                    duration_minutes,
                    request_id=ctx.context.request_id,
                ),
            )

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def list_watches(ctx: RunContextWrapper[_AgentContext]) -> str:
            """List persisted watches with status and expiry."""
            return await finish(ctx, "list_watches", ctx.context.watches.list_watches)

        @function_tool(timeout=self.config.api_timeout_seconds)
        async def cancel_watch(ctx: RunContextWrapper[_AgentContext], watch_id: str) -> str:
            """Idempotently cancel a watch or report its terminal/not-found state."""
            return await finish(
                ctx,
                "cancel_watch",
                lambda: ctx.context.watches.cancel_watch(watch_id),
            )

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
                lambda: context.watches.notify_user(watch_id, event_id, message, source="agent"),
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
        prompt = (
            "复查并处理以下已匹配关注事件。"
            f"watch_id={watch.watch_id}; event_id={event.event_id}; category={event.category}; "
            f"condition={event.kind}; confirmed_at={event.confirmed_at.isoformat()}; "
            f"evidence_id={event.evidence_id or 'none'}。"
            "先读取当前场景，再查询并核验这个事件；仅当工具事实支持时创建一次提醒。"
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
            event_watch_id=watch.watch_id if watch else None,
            event_id=event.event_id if event else None,
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
