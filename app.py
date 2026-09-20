"""Local Streamlit UI. Business decisions live in the shared application services."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st

from visual_ai_agent.behavior_ui import behavior_panel, laptop_panel, rules_panel
from visual_ai_agent.config import Config
from visual_ai_agent.models import utcnow
from visual_ai_agent.runtime import ApplicationRuntime
from visual_ai_agent.ui_controls import (
    observation_preferences,
    render_camera_settings,
    start_observation,
)
from visual_ai_agent.ui_theme import brand_html, inject_theme, status_html

st.set_page_config(page_title="视觉记忆 · VisualAIAgent", page_icon="◉", layout="wide")


@st.cache_resource(show_spinner=False, validate=lambda runtime: not runtime.closed)
def get_runtime():
    return ApplicationRuntime(Config.from_env())


LABELS = {"cell phone": "手机", "cup": "杯子", "bottle": "瓶子"}
REGIONS = {"left": "左侧", "center": "中间", "right": "右侧"}
STATES = {
    "running": "正在观察",
    "stopped": "已停止",
    "paused": "已暂停",
    "disconnected": "摄像头断开",
    "error": "发生错误",
    "stale": "画面已过期",
    "unknown": "当前状态未知",
    "waiting": "等待事件",
    "processing": "正在复查",
    "completed": "已完成",
    "cancelled": "已取消",
    "expired": "已到期",
}


def time_label(value):
    if not value:
        return "暂无记录"
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp.astimezone(ZoneInfo(runtime.config.timezone)).strftime("%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def show_evidence(evidence_id, control_id=None):
    if not evidence_id:
        st.caption("此条证据图不可用；不会以其他图片替代。")
        return
    load = st.checkbox(
        "加载本地证据图",
        key=f"load-evidence-{evidence_id}-{control_id or 'default'}",
        help="证据图片默认不读取；仅在需要查看时加载。",
    )
    if not load:
        st.caption("证据图默认不加载；勾选后读取本地图片。")
        return
    path = runtime.memory.evidence_path(evidence_id)
    if path:
        st.image(str(path), caption=f"本地证据 · {evidence_id}", width="stretch")
    else:
        st.caption("此条证据图不可用；不会以其他图片替代。")


def show_result(result):
    if result.ok:
        st.success("操作已执行")
    else:
        st.error(result.error or "操作未完成")


try:
    runtime = get_runtime()
except Exception as exc:
    st.error(f"本地服务启动失败（{type(exc).__name__}）。请检查配置，或关闭已运行的同目录实例。")
    st.stop()

inject_theme()
observation_preferences(runtime)
# A full page run has already consumed any navigation request from a fragment.
st.session_state.pop("navigation_requested", None)

PAGES = {
    "工作台": ("home", "看见当下，记住重要时刻"),
    "记忆": ("inventory_2", "回顾物品出现的位置与真实证据"),
    "提醒": ("notifications", "管理关注任务与情境提醒"),
    "行为统计": ("bar_chart", "查看有效观察区间与行为事件"),
    "设置": ("settings", "设备、模型与调用记录"),
}


def navigate(page):
    st.session_state["navigation"] = page
    st.session_state["navigation_requested"] = True


with st.sidebar:
    st.html(brand_html())
    page = st.radio(
        "页面导航",
        list(PAGES),
        key="navigation",
        label_visibility="collapsed",
        format_func=lambda name: f":material/{PAGES[name][0]}: {name}",
    )
    with st.container(key="sidebar-footer"):
        st.caption("画面与截图保存在本机")
        st.caption("Agent 仅接收必要文字事实")


@st.fragment(run_every=1)
def connection_status():
    scene = runtime.memory.get_current_scene().data
    status = scene.get("effective_status", "stopped")
    tone = (
        "accent"
        if status == "running"
        else ("error" if status in {"disconnected", "error"} else "muted")
    )
    agent = "Agent 已配置" if runtime.config.agent_connected else "Agent 未连接"
    st.html(
        '<div class="vaa-header-status">'
        + status_html(STATES.get(status, "当前未知"), tone)
        + status_html(agent, "muted")
        + "</div>"
    )


with st.container(key="workspace-header"):
    title, status, controls = st.columns([2.2, 2, 1.9], vertical_alignment="center")
    with title:
        st.title(page)
        st.caption(PAGES[page][1])
    with controls:
        start, stop = st.columns(2)
        if start.button(
            "开始观察",
            type="primary",
            width="stretch",
            help="应用设置页中已选择的参数；若参数改变，将重新启动观察。",
        ):
            result = start_observation(runtime)
            if result.ok:
                st.toast("观察已开始")
            else:
                st.session_state["observation_error"] = result.error
        if stop.button("停止观察", width="stretch"):
            result = runtime.stop_camera()
            if result.ok:
                st.session_state.pop("observation_error", None)
                st.toast("观察已停止")
            else:
                st.session_state["observation_error"] = result.error
    with status:
        connection_status()

if st.session_state.get("observation_error"):
    st.error(st.session_state.pop("observation_error"))


@st.fragment(run_every=0.2)
def live_preview():
    preview = runtime.preview_snapshot()
    if preview.jpeg:
        st.image(preview.jpeg, output_format="JPEG", width="stretch")
        if preview.detection_age_seconds is not None:
            kind = "跟踪框" if preview.tracked else "最近检测框"
            st.caption(
                f"流畅预览 · {kind} · 识别于 {preview.detection_age_seconds:.1f} 秒前。"
                "跟踪仅用于显示，当前物品以识别结果为准。"
            )
        else:
            st.caption("流畅预览 · 当前无可靠框选；物品识别按原采样档位更新。")
    elif preview.status == "disconnected":
        st.warning("摄像头已断开。请检查连接后，点击「开始观察」重新连接。")
    elif preview.status == "error":
        st.warning("摄像头观察异常。请检查设备后，点击「开始观察」重试。")
    elif preview.status in ("stale", "paused"):
        st.warning("预览已暂停或画面过期；当前画面不可确认。")
    else:
        st.html(
            '<div class="vaa-preview-empty"><div><strong>等待开始观察</strong>'
            "<p>连接摄像头，点击右上角「开始观察」</p></div></div>"
        )


@st.fragment(run_every=1)
def overview():
    scene = runtime.memory.get_current_scene().data
    observation, _ = runtime.snapshot()
    if runtime.last_error:
        st.error(runtime.last_error)
    st.subheader("当前物品")
    raw = scene.get("observation") or {}
    if not scene.get("current"):
        st.caption("当前状态未知 · 等待有效观察")
    elif raw.get("detections"):
        rows = "".join(
            "<tr>"
            f"<td>{escape(LABELS.get(d['category'], d['category']))}</td>"
            f"<td>{escape(REGIONS.get(d['region'], d['region']))}</td>"
            f"<td>{escape(time_label(raw.get('observed_at')))}</td>"
            "</tr>"
            for d in raw["detections"]
        )
        st.html(
            '<table class="vaa-object-table"><thead><tr><th>物品</th><th>位置</th>'
            "<th>观察时间</th></tr></thead><tbody>" + rows + "</tbody></table>"
        )
        if len({d["category"] for d in raw["detections"]}) < len(raw["detections"]):
            st.caption("同类多件均为候选，不代表已识别具体身份。")
    else:
        st.caption("这次有效采样未检测到目标类别。")
    with st.expander("识别详情"):
        st.caption(f"观察时间：{time_label(raw.get('observed_at'))}")
        if scene.get("current"):
            for detection in raw.get("detections", []):
                st.write(
                    f"{LABELS[detection['category']]} · {REGIONS[detection['region']]} · "
                    f"置信度 {detection['confidence']:.0%}"
                )
        st.caption("同类多件均为候选；停止、断连或过期均表示当前未知。")
        if observation and observation.inference_ms is not None:
            st.caption(
                f"最近推理 {observation.inference_ms:.0f} ms · "
                f"实际画面 {observation.width} × {observation.height}"
            )
    model = runtime.diagnostics().get("active_object_model")
    if model:
        st.caption(f"实际模型：{model['label']}")
    else:
        st.caption("实际模型：尚未启用")


def chat_page():
    st.subheader("与视觉记忆对话")
    st.caption("查询物品，回顾事件，设置提醒")

    @st.fragment(run_every=1 if st.session_state.get("pending_chat") else None)
    def conversation():
        if st.session_state.pop("navigation_requested", False):
            # A shortcut inside this fragment must rebuild the application shell,
            # not just redraw the conversation with a changed sidebar value.
            st.rerun(scope="app")
        pending = st.session_state.get("pending_chat")
        if pending and pending["future"].done():
            try:
                result = pending["future"].result()
                st.session_state["last_agent_result"] = (
                    asdict(result) if is_dataclass(result) else result.model_dump()
                )
            except Exception:
                st.session_state["chat_error"] = "本次请求未完成，请检查调用记录后重试。"
            st.session_state.pop("pending_chat", None)
            # Rebuild once to stop the timer after the request has completed.
            st.rerun()
        interactions = runtime.memory.list_chat_interactions(limit=5)
        with st.container(height=360, border=False, key="chat-transcript"):
            if not interactions and not pending:
                st.html(
                    '<div class="vaa-chat-empty"><strong>从一个问题开始</strong>'
                    "<p>最后在哪里看到手机？<br>杯子持续未检测到时，提醒我。</p></div>"
                )
            for interaction in interactions:
                with st.chat_message("user"):
                    st.write(interaction["user_message"])
                with st.chat_message("assistant"):
                    st.write(interaction["assistant_message"])
            if pending:
                with st.chat_message("user"):
                    st.write(pending["message"])
                st.info("Agent 正在处理；画面仍持续刷新。")
            if st.session_state.get("chat_error"):
                st.error(st.session_state.pop("chat_error"))
            last = st.session_state.get("last_agent_result")
            if last:
                if last.get("status") not in ("completed", "fallback_notified"):
                    st.warning(last.get("message", "请求未完成"))
                with st.expander("本次已执行工具与用量"):
                    st.json(
                        {
                            key: last.get(key)
                            for key in (
                                "status",
                                "request_attempts",
                                "input_tokens",
                                "output_tokens",
                                "tool_calls",
                            )
                        }
                    )
        if not runtime.config.agent_connected:
            st.warning("Agent 未连接")
            st.caption("请在设置中查看连接说明；本地观察与记忆仍可使用。")
        shortcuts = st.columns(2)
        shortcuts[0].button(
            "查找物品", icon=":material/search:", on_click=navigate, args=("记忆",), width="stretch"
        )
        shortcuts[1].button(
            "设置提醒",
            icon=":material/notifications:",
            on_click=navigate,
            args=("提醒",),
            width="stretch",
        )
        text = st.chat_input(
            "问问刚才发生了什么…",
            disabled=bool(pending) or not runtime.config.agent_connected,
            max_chars=8000,
        )
        if text:
            try:
                future = runtime.submit_user(text, uuid4().hex)
                st.session_state["pending_chat"] = {"future": future, "message": text}
                st.rerun()
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))

    conversation()


def history_page():
    chosen = st.selectbox("物品类别", list(LABELS), format_func=LABELS.get)
    found = runtime.memory.find_object(chosen)
    if found.ok and found.data.get("found"):
        data = found.data
        st.write(f"最后看到 **{LABELS[chosen]}**：{time_label(data['last_seen_at'])}")
        st.caption("这是历史观察，不保证物品现在仍在原处。")
        if data.get("source") in ("test", "replay"):
            st.caption("来源：测试或回放输入")
        for candidate in data.get("candidates", []):
            st.write(f"{REGIONS[candidate['region']]} · 置信度 {candidate['confidence']:.0%}")
        show_evidence(data.get("evidence_id"), f"last-seen-{chosen}")
    else:
        st.info("没有这个类别的历史记录。")
    st.subheader("最近七天事件")
    end = utcnow()
    events = runtime.memory.search_events(chosen, end - timedelta(days=7), end)
    if not events.data.get("events"):
        st.caption("暂无事件。")
    for event in events.data.get("events", []):
        label = "确认出现" if event["kind"] == "appeared" else "持续未检测到"
        with st.expander(f"{time_label(event['confirmed_at'])} · {label}"):
            st.write(f"事件编号：{event['event_id']}")
            st.caption("持续未检测到不说明原因，不能据此推断被谁拿走。")
            show_evidence(event.get("evidence_id"), f"object-event-{event['event_id']}")
    if events.data.get("truncated"):
        st.caption("仅展示最近 20 条；可在对话中指定更短时间范围。")


def watch_page():
    st.subheader("关注任务")
    with st.form("create_watch"):
        cat = st.selectbox("关注物品", list(LABELS), format_func=LABELS.get)
        condition = st.selectbox(
            "触发条件",
            ["appeared", "missing"],
            format_func=lambda value: (
                "确认出现" if value == "appeared" else "持续未检测到（先有有效出现）"
            ),
        )
        duration = st.number_input("有效分钟", min_value=1, max_value=1440, value=30)
        if st.form_submit_button("创建关注"):
            show_result(
                runtime.watches.create_watch(
                    cat,
                    condition,
                    duration,
                    request_id=uuid4().hex,
                )
            )

    @st.fragment(run_every=1)
    def watch_status():
        watches = runtime.watches.list_watches().data.get("watches", [])
        for watch in watches:
            a, b = st.columns([4, 1])
            label = "出现" if watch["condition"] == "appeared" else "持续未检测到"
            a.write(
                f"**{LABELS[watch['category']]} · {label}** · "
                f"{STATES[watch['status']]} · 到期 {time_label(watch['expires_at'])}"
            )
            a.caption(f"任务 {watch['watch_id']}")
            if watch["status"] in ("waiting", "processing"):
                if b.button("取消", key=f"cancel-{watch['watch_id']}"):
                    show_result(runtime.watches.cancel_watch(watch["watch_id"]))
                    st.rerun()
        if not watches:
            st.caption("暂无关注任务。")
        st.subheader("页面提醒")
        notices = runtime.watches.list_notifications().data.get("notifications", [])
        for notice in notices:
            st.info(notice["message"])
            source = "Agent 工具提醒" if notice["source"] == "agent" else "本地规则降级提醒"
            st.caption(f"{time_label(notice['created_at'])} · {source} · 事件 {notice['event_id']}")
            with st.expander("查看提醒证据", key=f"notice-{notice['notification_id']}"):
                show_evidence(
                    notice.get("evidence_id"), f"watch-notice-{notice['notification_id']}"
                )
        if not notices:
            st.caption("暂无提醒。")

    watch_status()


def usage_page():
    st.subheader("调用与限制")
    st.write("仅用户请求或相关关注事件唤醒 Agent。每次最多 3 次模型请求，自动运行每日有上限。")
    st.caption(
        f"Agent 队列 {runtime.diagnostics()['queued_requests']} · "
        f"自动调用日上限 {runtime.config.daily_auto_limit}"
    )
    st.caption("Token 为提供商实际返回的用量；未知显示为空，不把失败或未返回用量记成零。")
    today = utcnow().astimezone(ZoneInfo(runtime.config.timezone)).date()
    summary = runtime.memory.get_usage_summary(today, runtime.config.timezone)
    first, second, third = st.columns(3)
    first.metric("今日模型请求尝试", summary["total_request_attempts"])
    second.metric("今日自动运行", summary["event_runs"])
    third.metric(
        "今日 Token", summary["total_tokens"] if summary["total_tokens"] is not None else "不可用"
    )
    if not summary["usage_complete"]:
        st.caption(
            f"已知部分 Token：{summary['known_total_tokens']}；完整总量不可用，部分请求未返回用量。"
        )
    st.json(runtime.memory.list_agent_runs(limit=50))


@st.fragment(run_every=5)
def recent_activity():
    end = utcnow()
    items = []
    for category, label in LABELS.items():
        result = runtime.memory.search_events(category, end - timedelta(days=7), end, limit=3)
        for event in result.data.get("events", []):
            if event["kind"] == "appeared":
                region = "、".join(REGIONS.get(value, value) for value in event["regions"])
                message = f"{label}出现在画面{region}"
            else:
                message = f"画面中持续未检测到{label}"
            if event.get("source") in {"test", "replay"}:
                message += "（测试或回放）"
            items.append((event["confirmed_at"], message))
    for notice in runtime.watches.list_notifications(limit=3).data.get("notifications", []):
        items.append((notice["created_at"], "提醒 · " + notice["message"]))
    for notice in runtime.rules.list_notifications(limit=3).data.get("notifications", []):
        items.append((notice["created_at"], "情境提醒 · " + notice["message"]))
    items.sort(key=lambda item: datetime.fromisoformat(item[0]), reverse=True)
    if not items:
        st.caption("暂无动态。开始观察后，已确认的物品事件和提醒会出现在这里。")
    for timestamp, message in items[:3]:
        st.html(
            '<div class="vaa-activity-row"><span class="vaa-activity-time">'
            + escape(time_label(timestamp))
            + '</span><span class="vaa-activity-text">'
            + escape(message)
            + "</span></div>"
        )


if page == "工作台":
    with st.container(key="workspace-grid"):
        image_col, chat_col = st.columns([1.25, 1], gap="medium")
        with image_col, st.container(key="camera-panel"):
            heading, badge = st.columns([3, 1])
            heading.subheader("实时画面")
            with badge:
                st.html(status_html("本地处理", "muted"))
            live_preview()
            overview()
        with chat_col, st.container(key="chat-panel"):
            chat_page()
    with st.container(key="activity-panel"):
        heading, link = st.columns([4, 1.5])
        heading.subheader("最近动态")
        link.button(
            "查看物品记忆",
            icon=":material/arrow_forward:",
            on_click=navigate,
            args=("记忆",),
            type="tertiary",
        )
        recent_activity()
elif page == "记忆":
    with st.container(key="settings-panel"):
        history_page()
elif page == "提醒":
    watch_tab, rule_tab = st.tabs(
        ["关注与提醒", "情境规则"], key="reminder-page", on_change="rerun"
    )
    if watch_tab.open:
        with watch_tab, st.container(key="settings-panel"):
            watch_page()
    if rule_tab.open:
        with rule_tab, st.container(key="settings-panel"):
            rules_panel(runtime, show_result, show_evidence, time_label)
elif page == "行为统计":
    behavior_tab, laptop_tab = st.tabs(
        ["行为与统计", "笔记本开合"], key="behavior-page", on_change="rerun"
    )
    if behavior_tab.open:
        with behavior_tab, st.container(key="settings-panel"):
            behavior_panel(runtime, show_evidence, time_label)
    if laptop_tab.open:
        with laptop_tab, st.container(key="settings-panel"):
            laptop_panel(runtime, show_evidence, time_label)
elif page == "设置":
    settings_tab, usage_tab = st.tabs(
        ["设备与模型", "调用记录"], key="settings-page", on_change="rerun"
    )
    if settings_tab.open:
        with settings_tab, st.container(key="settings-panel"):
            render_camera_settings(runtime)
            st.divider()
            st.subheader("Agent 连接")
            if runtime.config.agent_connected:
                st.info("Agent 已配置；实际连接结果将在请求后显示。")
            else:
                st.warning("Agent 未连接")
                st.caption("在本地 .env 配置 API 地址、模型名称和 Key 后重启应用。")
            st.caption("画面与截图留在本机。Agent 仅接收必要文字事实、时间和证据编号。")
    if usage_tab.open:
        with usage_tab, st.container(key="settings-panel"):
            usage_page()
