"""Behavior and contextual-rule panels; decisions remain in application services."""

from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st

from .models import utcnow

POSTURES = {"seated": "在座", "standing": "站立", "empty": "空座", "unknown": "未知"}
TRIGGERS = {
    "stood_up": "起身",
    "sat_down": "坐下",
    "left_seat": "离座",
    "seat_occupied": "返回在座",
    "suspected_drink": "疑似饮水",
    "seated_duration": "连续有效在座达到时长",
    "laptop_closed": "笔记本确认合盖",
    "laptop_opened": "笔记本确认打开",
}
OBJECTS = {"cup": "杯子", "cell phone": "手机", "bottle": "瓶子"}
REGIONS = {"any": "任意区域", "left": "左侧", "center": "中间", "right": "右侧"}


def duration_label(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}小时 {minutes}分 {seconds}秒" if hours else f"{minutes}分 {seconds}秒"


def behavior_panel(runtime, show_evidence, time_label):
    st.subheader("行为与有效时长 · 实验模型")
    st.caption(
        "在座不代表学习或专注；疑似饮水只表示外观动作，不证明吞咽或饮水量。"
        "未知和采样中断不补算，可能使有效时长偏低。"
    )
    chosen_date = st.date_input(
        "统计日期",
        value=utcnow().astimezone(ZoneInfo(runtime.config.timezone)).date(),
        key="behavior-date",
    )

    @st.fragment(run_every=1)
    def contents():
        current = runtime.behavior.current().data
        diagnostics = runtime.diagnostics()
        if diagnostics.get("behavior_error"):
            st.warning(diagnostics["behavior_error"])
        a, b, c = st.columns(3)
        a.metric("当前姿态", POSTURES.get(current.get("posture"), "未知"))
        b.metric("当前连续有效在座", duration_label(current.get("continuous_seated_seconds")))
        drinking = current.get("drinking")
        c.metric(
            "当前疑似饮水", "疑似饮水" if drinking else ("未确认" if drinking is False else "未知")
        )
        if not current.get("current"):
            st.info("当前行为不可确认。启用实验行为模型并开始观察后，才会累计有效区间。")
        observation = current.get("observation") or {}
        if observation.get("error"):
            st.caption(f"观察说明：{observation['error']}")
        st.caption(f"最近行为观察：{time_label(observation.get('observed_at'))}")
        stats = runtime.behavior.statistics(chosen_date)
        if not stats.ok:
            st.error(stats.error)
            return
        data = stats.data
        counts = data.get("event_counts", {})
        a, b, c = st.columns(3)
        a.metric("累计有效在座", duration_label(data.get("seated_seconds")))
        b.metric("确认离座次数", counts.get("left_seat", 0))
        c.metric("累计有效空座", duration_label(data.get("empty_seconds")))
        a, b, c = st.columns(3)
        a.metric("疑似饮水次数", counts.get("suspected_drink", 0))
        b.metric("未知时长", duration_label(data.get("unknown_seconds")))
        c.metric("累计有效站立", duration_label(data.get("standing_seconds")))
        st.caption("空座时长包括开始观察时已经空座的有效区间；离座次数只计确认的离座事件。")
        st.subheader("最近行为事件")
        end = utcnow()
        events = runtime.behavior.search_events(end - timedelta(days=7), end, limit=20)
        for event in events.data.get("events", []):
            with st.expander(
                f"{time_label(event['confirmed_at'])} · "
                f"{TRIGGERS.get(event['kind'], event['kind'])}"
            ):
                st.caption(f"事件 {event['event_id']} · 来源 {event.get('source', 'unknown')}")
                show_evidence(event.get("evidence_id"))
        if not events.data.get("events"):
            st.caption("暂无确认行为事件。")

    contents()


def laptop_panel(runtime, show_evidence, time_label):
    st.subheader("笔记本开合 · 未验收实验能力")
    st.caption(
        "笔记本状态与行为时长分开记录。只有确认电脑仍在画面中且清晰可见后，"
        "才允许输出开着或合上；半开算开着，低置信、遮挡、移出画面和过期画面均为未知。"
    )

    @st.fragment(run_every=1)
    def contents():
        current = runtime.laptop.current().data
        diagnostics = runtime.diagnostics()
        if diagnostics.get("laptop_error"):
            st.warning(diagnostics["laptop_error"])
        if not current.get("available"):
            st.info(current.get("capability_reason") or "笔记本开合能力不可用。")
        state = current.get("state", "unknown") if current.get("current") else "unknown"
        labels = {"open": "开着（含半开）", "closed": "完全合上", "unknown": "未知"}
        first, second, third = st.columns(3)
        first.metric("当前笔记本状态", labels.get(state, "未知"))
        second.metric("能力状态", "实验可用" if current.get("available") else "未验收/不可用")
        observation = current.get("observation") or {}
        presence = observation.get("presence_verified")
        third.metric("画面确认", "电脑清晰可见" if presence else "未确认")
        st.caption(f"最近笔记本观察：{time_label(observation.get('observed_at'))}")
        st.subheader("最近笔记本开合事件")
        end = utcnow()
        events = runtime.laptop.search_events(end - timedelta(days=7), end, limit=20)
        labels = {"laptop_closed": "确认合盖", "laptop_opened": "确认打开"}
        for event in events.data.get("events", []):
            with st.expander(
                f"{time_label(event['confirmed_at'])} · {labels.get(event['kind'], event['kind'])}"
            ):
                st.caption(
                    f"事件 {event['event_id']} · 场景 {event['scene_id']} · "
                    f"状态模型 {event['model_version']} · "
                    f"画面确认模型 {event['presence_model_version']}"
                )
                show_evidence(event.get("evidence_id"))
        if not events.data.get("events"):
            st.caption("暂无确认的笔记本开合事件。")

    contents()


def _rule_fields(key, rule=None):
    rule = rule or {}
    trigger = st.selectbox(
        "触发条件",
        list(TRIGGERS),
        index=list(TRIGGERS).index(rule.get("trigger", "left_seat")),
        format_func=TRIGGERS.get,
        key=f"{key}-trigger",
    )
    seated_minutes = st.number_input(
        "连续在座阈值（分钟，仅时长触发生效）",
        1,
        1440,
        int(rule.get("seated_minutes") or 45),
        key=f"{key}-minutes",
    )
    after_time = st.text_input(
        "仅在此时间之后触发（HH:MM，留空不限）",
        value=rule.get("after_time") or "",
        max_chars=5,
        key=f"{key}-time",
    )
    categories = [None, *OBJECTS]
    category = st.selectbox(
        "同时要求物品稳定在场",
        categories,
        index=categories.index(rule.get("object_category")),
        format_func=lambda value: OBJECTS.get(value, "不限制物品"),
        key=f"{key}-object",
    )
    region = st.selectbox(
        "物品区域",
        list(REGIONS),
        index=list(REGIONS).index(rule.get("region", "any")),
        format_func=REGIONS.get,
        key=f"{key}-region",
    )
    message = st.text_input(
        "提醒内容",
        value=rule.get("message") or "离开前请检查桌面物品。",
        max_chars=500,
        key=f"{key}-message",
    )
    enabled = st.checkbox("启用此规则", value=rule.get("enabled", True), key=f"{key}-enabled")
    return dict(
        trigger=trigger,
        after_time=after_time.strip() or None,
        object_category=category,
        region=region if category else "any",
        seated_minutes=int(seated_minutes) if trigger == "seated_duration" else None,
        message=message,
        enabled=enabled,
    )


def rules_panel(runtime, show_result, show_evidence, time_label):
    st.subheader("行为与情境提醒")
    st.caption(
        "触发事件发生时才检查条件；晚于指定时间为严格大于。"
        "物品事实须在触发时有效，未知不满足。规则长期有效，提醒仅显示在本页面。"
    )
    rules = runtime.rules.list_rules().data.get("rules", [])
    active_rest = [
        rule
        for rule in rules
        if rule["trigger"] == "seated_duration"
        and rule["enabled"]
        and rule.get("status") != "cancelled"
    ]
    if not active_rest:
        st.info("休息提醒未启用。可创建“连续有效在座达到时长”规则，建议初始阈值 45 分钟。")
    st.caption("同一连续在座期间只提醒一次；短暂未知不恢复提醒资格，确认离座后才重新允许提醒。")
    with st.form("create_context_rule"):
        fields = _rule_fields("new-rule")
        if st.form_submit_button("创建情境规则"):
            result = runtime.rules.create_rule(**fields, request_id=uuid4().hex)
            show_result(result)
            if result.ok:
                st.rerun()
    st.caption(
        "例如：离座＋18:00之后＋手机在右侧。笔记本规则只在实验能力显示可用时创建；"
        "钥匙和自定义区域暂未支持。"
    )
    for rule in rules:
        rule_id = rule["rule_id"]
        state = (
            "已取消"
            if rule.get("status") == "cancelled"
            else ("启用" if rule["enabled"] else "停用")
        )
        object_text = (
            f" · {OBJECTS.get(rule.get('object_category'))}在{REGIONS.get(rule.get('region'))}"
            if rule.get("object_category")
            else ""
        )
        time_text = f" · {rule['after_time']}之后" if rule.get("after_time") else ""
        minutes_text = f" {rule['seated_minutes']}分钟" if rule.get("seated_minutes") else ""
        with st.expander(
            f"{TRIGGERS[rule['trigger']]}{minutes_text}{time_text}{object_text} · {state}"
        ):
            st.caption(f"规则 {rule_id} · 版本 {rule['version']} · 长期有效")
            st.write(rule.get("message", ""))
            if rule.get("status") != "cancelled":
                with st.form(f"edit-rule-{rule_id}-{rule['version']}"):
                    fields = _rule_fields(f"edit-{rule_id}-{rule['version']}", rule)
                    if st.form_submit_button("保存规则修改"):
                        result = runtime.rules.update_rule(
                            rule_id, **fields, request_id=uuid4().hex
                        )
                        show_result(result)
                        if result.ok:
                            st.rerun()
                if st.button("取消规则", key=f"cancel-rule-{rule_id}"):
                    show_result(runtime.rules.cancel_rule(rule_id))
                    st.rerun()

    @st.fragment(run_every=1)
    def notifications():
        st.subheader("行为与情境通知")
        notices = runtime.rules.list_notifications().data.get("notifications", [])
        for notice in notices:
            st.info(notice["message"])
            source = "Agent 工具提醒" if notice["source"] == "agent" else "本地规则降级提醒"
            st.caption(f"{time_label(notice['created_at'])} · {source} · 事件 {notice['event_id']}")
            with st.expander("情境提醒证据", key=f"rule-notice-{notice['notification_id']}"):
                show_evidence(notice.get("evidence_id"))
        if not notices:
            st.caption("暂无行为或情境提醒。")

    notifications()
