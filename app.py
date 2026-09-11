"""Local Streamlit UI. Business decisions live in the shared application services."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st

from visual_ai_agent.behavior_ui import behavior_panel, laptop_panel, rules_panel
from visual_ai_agent.config import Config
from visual_ai_agent.models import utcnow
from visual_ai_agent.runtime import ApplicationRuntime

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


def show_evidence(evidence_id):
    path = runtime.memory.evidence_path(evidence_id) if evidence_id else None
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

st.title("视觉记忆")
st.caption("手机、杯子、瓶子 · 本地观察与证据 · 一个会调用工具的 Agent")

with st.sidebar:
    st.subheader("观察控制")
    camera_index = st.number_input(
        "摄像头编号", min_value=0, max_value=20, value=runtime.config.camera_index, step=1
    )
    speed = st.selectbox(
        "采样档位",
        ["标准 · 每秒一次", "省电 · 每两秒一次"],
        index=0 if runtime.config.sample_interval == 1 else 1,
    )
    resolutions = [(640, 480), (1280, 720), (1920, 1080)]
    configured_resolution = (runtime.config.camera_width, runtime.config.camera_height)
    resolution = st.selectbox(
        "采集清晰度",
        resolutions,
        index=resolutions.index(configured_resolution),
        format_func=lambda value: f"{value[0]} × {value[1]}",
    )
    cup_scale_recheck = st.checkbox(
        "增强杯子检测（本地复查）",
        value=runtime.config.cup_scale_recheck,
        help="会增加一次本地运算，仅对当前观察画面起作用；开启不保证所有杯子都能检出。",
    )
    with st.expander("观察范围"):
        configured_region = runtime.config.observation_region
        range_mode = st.radio(
            "范围",
            ["完整画面", "自定义"],
            index=1 if configured_region else 0,
            horizontal=True,
        )
        initial_region = configured_region or (0.0, 0.0, 1.0, 1.0)
        region_left = st.number_input(
            "左边界（%）",
            0.0,
            99.99,
            initial_region[0] * 100,
            1.0,
            format="%.2f",
        )
        region_top = st.number_input(
            "上边界（%）",
            0.0,
            99.99,
            initial_region[1] * 100,
            1.0,
            format="%.2f",
        )
        region_right = st.number_input(
            "右边界（%）",
            0.01,
            100.0,
            initial_region[2] * 100,
            1.0,
            format="%.2f",
        )
        region_bottom = st.number_input(
            "下边界（%）",
            0.01,
            100.0,
            initial_region[3] * 100,
            1.0,
            format="%.2f",
        )
        st.caption(
            "只判断预览范围；物品移出范围只表示在当前画面中持续未检测到，不代表它从整个环境中消失。"
        )
    selected_region = (
        (0.0, 0.0, 1.0, 1.0)
        if range_mode == "完整画面"
        else (
            region_left / 100,
            region_top / 100,
            region_right / 100,
            region_bottom / 100,
        )
    )
    with st.expander("实验行为识别", expanded=False):
        behavior_enabled = st.checkbox(
            "启用在座与疑似饮水识别",
            value=runtime.config.behavior_enabled,
            help="使用当前固定机位的实验模型；不代表动作质量已经通过验收。",
        )
        posture_default = runtime.config.behavior_posture_manifest
        drinking_default = runtime.config.behavior_drinking_manifest
        known_root = Path("harness/artifacts/behavior-20260910-r02")
        if (
            posture_default is None
            and (known_root / "posture-run/training-manifest.json").is_file()
        ):
            posture_default = known_root / "posture-run/training-manifest.json"
        if (
            drinking_default is None
            and (known_root / "drinking-run/training-manifest.json").is_file()
        ):
            drinking_default = known_root / "drinking-run/training-manifest.json"
        posture_manifest = st.text_input("姿态模型清单", value=str(posture_default or ""))
        drinking_manifest = st.text_input("饮水模型清单", value=str(drinking_default or ""))
        st.caption("沿用当前固定机位；视角或取景范围变化后不能沿用旧场景的质量结论。")
        behavior_kwargs = dict(
            behavior_enabled=behavior_enabled,
            behavior_posture_manifest=Path(posture_manifest) if posture_manifest.strip() else None,
            behavior_drinking_manifest=Path(drinking_manifest)
            if drinking_manifest.strip()
            else None,
        )
    with st.expander("实验笔记本开合", expanded=False):
        laptop_enabled = st.checkbox(
            "启用笔记本开合识别",
            value=runtime.config.laptop_enabled,
            help=(
                "只有经独立验证能确认电脑仍在画面中且清晰可见的实验模型才能加载；"
                "当前训练模型不会自动启用。"
            ),
        )
        laptop_manifest = st.text_input(
            "笔记本能力清单",
            value=str(runtime.config.laptop_capability_manifest or ""),
            help="请选择完整验收后的实验能力清单，不能选择单独的开合分类训练清单。",
        )
        st.caption("未验收、遮挡、电脑移出画面或画面过期时一律显示未知。")
        laptop_kwargs = dict(
            laptop_enabled=laptop_enabled,
            laptop_capability_manifest=(Path(laptop_manifest) if laptop_manifest.strip() else None),
        )
    left, right = st.columns(2)
    if left.button("开始观察", type="primary", width="stretch"):
        if range_mode == "自定义" and (region_left >= region_right or region_top >= region_bottom):
            st.error("观察范围无效：左边界须小于右边界，上边界须小于下边界。")
        else:
            requested_interval = 1.0 if speed.startswith("标准") else 2.0
            requested_settings = (
                int(camera_index),
                resolution[0],
                resolution[1],
                None if selected_region == (0.0, 0.0, 1.0, 1.0) else selected_region,
                requested_interval,
                cup_scale_recheck,
            )
            active_settings = runtime.diagnostics()["camera_settings"]
            active_observation, _ = runtime.snapshot()
            needs_reconnect = active_observation is not None and active_observation.status in {
                "disconnected",
                "error",
            }
            active_behavior = runtime.diagnostics().get("behavior_settings")
            behavior_changed = bool(
                active_behavior
                and (
                    active_behavior[0] != behavior_enabled
                    or active_behavior[1] != behavior_kwargs["behavior_posture_manifest"]
                    or active_behavior[2] != behavior_kwargs["behavior_drinking_manifest"]
                )
            )
            active_laptop = runtime.diagnostics().get("laptop_settings")
            laptop_changed = bool(
                active_laptop
                and (
                    active_laptop[0] != laptop_enabled
                    or active_laptop[1] != laptop_kwargs["laptop_capability_manifest"]
                )
            )
            if needs_reconnect or (
                runtime.camera_running
                and (active_settings != requested_settings or behavior_changed or laptop_changed)
            ):
                stopped = runtime.stop_camera()
                if not stopped.ok:
                    show_result(stopped)
                else:
                    show_result(
                        runtime.start_camera(
                            int(camera_index),
                            requested_interval,
                            resolution=resolution,
                            observation_region=selected_region,
                            cup_scale_recheck=cup_scale_recheck,
                            **behavior_kwargs,
                            **laptop_kwargs,
                        )
                    )
            else:
                show_result(
                    runtime.start_camera(
                        int(camera_index),
                        requested_interval,
                        resolution=resolution,
                        observation_region=selected_region,
                        cup_scale_recheck=cup_scale_recheck,
                        **behavior_kwargs,
                        **laptop_kwargs,
                    )
                )
    if right.button("停止观察", width="stretch"):
        show_result(runtime.stop_camera())
    st.caption("启动后使用非镜像坐标。停止、断连或过期画面均表示当前未知。")
    active_settings = runtime.diagnostics()["camera_settings"]
    if active_settings:
        (
            active_camera,
            active_width,
            active_height,
            active_region,
            active_interval,
            active_cup_recheck,
        ) = active_settings
        region_text = (
            "完整画面"
            if active_region is None
            else "左 {:.0%}、上 {:.0%}、右 {:.0%}、下 {:.0%}".format(*active_region)
        )
        st.caption(
            f"当前启用设置（请求）：摄像头 {active_camera} · "
            f"{active_width} × {active_height} · "
            f"{region_text} · 每 {active_interval:g} 秒采样 · "
            f"杯子增强{'已开启' if active_cup_recheck else '未开启'}"
        )
        active_observation, _ = runtime.snapshot()
        if active_observation and active_observation.width and active_observation.height:
            st.caption(f"当前预览尺寸：{active_observation.width} × {active_observation.height}")
    st.divider()
    if runtime.config.agent_connected:
        st.success("Agent 已配置")
        st.caption("实际连接结果将在请求后显示。")
    else:
        st.warning("Agent 未连接")
        st.caption(
            "在本地 .env 配置 API 地址、模型名称和 Key 后重启应用。视觉与历史查询可独立使用。"
        )
    st.caption("画面与截图留在本机。Agent 仅接收必要文字事实、时间和证据编号。")


@st.fragment(run_every=0.5)
def overview():
    scene = runtime.memory.get_current_scene().data
    observation, jpeg = runtime.snapshot()
    a, b, c = st.columns(3)
    a.metric("观察状态", STATES.get(scene.get("effective_status"), "尚未开始"))
    b.metric("Agent 队列", runtime.diagnostics()["queued_requests"])
    c.metric("自动调用日上限", runtime.config.daily_auto_limit)
    if runtime.last_error:
        st.error(runtime.last_error)
    image_col, fact_col = st.columns([1.65, 1])
    with image_col:
        if jpeg:
            st.image(jpeg, channels="RGB", width="stretch")
            if not scene.get("current"):
                st.warning("上方为最后预览，当前画面无效，不能据此判断物品是否仍在。")
        else:
            effective_status = scene.get("effective_status")
            if effective_status == "disconnected":
                st.warning("摄像头已断开。请检查连接后，点击「开始观察」重新连接。")
            elif effective_status == "error":
                st.warning("摄像头观察异常。请检查设备后，点击「开始观察」重试。")
            else:
                st.info("准备好摄像头和模型后，点击「开始观察」。")
    with fact_col:
        st.subheader("最新观察")
        raw = scene.get("observation") or {}
        st.caption(f"观察时间：{time_label(raw.get('observed_at'))}")
        if not scene.get("current"):
            st.write("当前状态未知")
        elif raw.get("detections"):
            for detection in raw["detections"]:
                st.write(
                    f"**{LABELS[detection['category']]}** · "
                    f"{REGIONS[detection['region']]} · "
                    f"置信度 {detection['confidence']:.0%}"
                )
            st.caption("同类多件均为候选，不代表已识别具体身份。")
        else:
            st.write("这次有效采样未检测到目标类别。")
        if observation and observation.inference_ms is not None:
            st.caption(
                f"最近推理 {observation.inference_ms:.0f} ms · "
                f"实际画面 {observation.width} × {observation.height}"
            )


overview()
chat_tab, history_tab, watch_tab, behavior_tab, laptop_tab, rule_tab, usage_tab = st.tabs(
    [
        "对话",
        "历史与证据",
        "关注与提醒",
        "行为与统计",
        "笔记本开合",
        "情境规则",
        "调用记录",
    ]
)

with chat_tab:
    st.subheader("向视觉记忆提问")
    st.caption("例如：最后在哪里看到手机？ / 如果杯子持续未检测到，请在半小时内提醒我。")

    @st.fragment(run_every=0.5)
    def conversation():
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
            pending = None
        for interaction in runtime.memory.list_chat_interactions(limit=5):
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
        text = st.chat_input(
            "输入查询或关注请求",
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

with history_tab:
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
        show_evidence(data.get("evidence_id"))
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
            show_evidence(event.get("evidence_id"))
    if events.data.get("truncated"):
        st.caption("仅展示最近 20 条；可在对话中指定更短时间范围。")

with watch_tab:
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
                show_evidence(notice.get("evidence_id"))
        if not notices:
            st.caption("暂无提醒。")

    watch_status()

with behavior_tab:
    behavior_panel(runtime, show_evidence, time_label)

with laptop_tab:
    laptop_panel(runtime, show_evidence, time_label)

with rule_tab:
    rules_panel(runtime, show_result, show_evidence, time_label)

with usage_tab:
    st.subheader("调用与限制")
    st.write("仅用户请求或相关关注事件唤醒 Agent。每次最多 3 次模型请求，自动运行每日有上限。")
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
