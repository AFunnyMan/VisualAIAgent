"""Observation settings shared by the workbench toolbar and settings page."""

from pathlib import Path

import streamlit as st

from .models import ToolResult
from .object_models import OBJECT_MODEL_PRESETS, get_object_model_preset


def observation_preferences(runtime):
    """Keep unapplied choices separate from the actual running configuration."""
    if "observation_preferences" not in st.session_state:
        config = runtime.config
        diagnostics = runtime.diagnostics()
        active = diagnostics.get("camera_settings")
        model = diagnostics.get("active_object_model")
        model_path = Path(model["path"]) if model else config.model_path
        model_id = next(
            (p.id for p in OBJECT_MODEL_PRESETS if p.path.resolve() == model_path.resolve()),
            "configured",
        )
        region = (active[3] if active else config.observation_region) or (0.0, 0.0, 1.0, 1.0)
        behavior = diagnostics.get("behavior_settings")
        laptop = diagnostics.get("laptop_settings")
        posture = behavior[1] if behavior else config.behavior_posture_manifest
        drinking = behavior[2] if behavior else config.behavior_drinking_manifest
        known = Path("harness/artifacts/behavior-20260910-r02")
        if posture is None and (known / "posture-run/training-manifest.json").is_file():
            posture = known / "posture-run/training-manifest.json"
        if drinking is None and (known / "drinking-run/training-manifest.json").is_file():
            drinking = known / "drinking-run/training-manifest.json"
        st.session_state["observation_preferences"] = {
            "camera": active[0] if active else config.camera_index,
            "speed": "标准 · 每秒一次"
            if (active[4] if active else config.sample_interval) == 1
            else "省电 · 每两秒一次",
            "resolution": (active[1], active[2])
            if active
            else (config.camera_width, config.camera_height),
            "model": model_id,
            "cup_recheck": active[5] if active else config.cup_scale_recheck,
            "silver_ignore": active[6] if active else config.silver_object_ignore,
            "range_mode": "完整画面" if region == (0.0, 0.0, 1.0, 1.0) else "自定义",
            "left": region[0] * 100,
            "top": region[1] * 100,
            "right": region[2] * 100,
            "bottom": region[3] * 100,
            "behavior_enabled": behavior[0] if behavior else config.behavior_enabled,
            "posture_manifest": str(posture or ""),
            "drinking_manifest": str(drinking or ""),
            "laptop_enabled": laptop[0] if laptop else config.laptop_enabled,
            "laptop_manifest": str(
                (laptop[1] if laptop else config.laptop_capability_manifest) or ""
            ),
        }
    return st.session_state["observation_preferences"]


def _setting(widget, name, *args, **kwargs):
    key = f"observation-setting-{name}"
    preferences = st.session_state["observation_preferences"]
    if key not in st.session_state:
        st.session_state[key] = preferences[name]

    def remember():
        st.session_state["observation_preferences"][name] = st.session_state[key]

    return widget(*args, key=key, on_change=remember, persist_state="session", **kwargs)


def render_camera_settings(runtime):
    preferences = observation_preferences(runtime)
    st.subheader("观察设置")
    st.caption("选择会保留在当前会话；点击「开始观察」才会应用，运行中应用将重新启动观察。")
    first, second = st.columns(2)
    with first:
        _setting(st.number_input, "camera", "摄像头编号", min_value=0, max_value=20, step=1)
        _setting(st.selectbox, "speed", "采样档位", ["标准 · 每秒一次", "省电 · 每两秒一次"])
    with second:
        _setting(
            st.selectbox,
            "resolution",
            "采集清晰度",
            [(640, 480), (1280, 720), (1920, 1080)],
            format_func=lambda value: f"{value[0]} × {value[1]}",
        )
        labels = {"configured": "沿用配置文件中的模型"}
        labels.update({preset.id: preset.label for preset in OBJECT_MODEL_PRESETS})
        _setting(
            st.selectbox,
            "model",
            "物品识别模型",
            list(labels),
            format_func=labels.get,
            help="选择后点击开始观察才会生效。微调候选尚未完成独立验收。",
        )
    preset = (
        get_object_model_preset(preferences["model"])
        if preferences["model"] != "configured"
        else None
    )
    path = preset.path if preset else runtime.config.model_path
    if not path.is_file():
        st.warning("所选物品模型文件不在本机，请先准备该模型后再开始观察。")
    if preset and preset.experimental:
        st.caption("所选为固定场景微调候选；用于测试，不代表质量验收已通过。")
    st.caption(f"待启用模型：{labels[preferences['model']]}")
    _setting(
        st.checkbox,
        "cup_recheck",
        "增强杯子检测（本地复查）",
        help="增加一次本地运算；开启不保证所有杯子都能检出。",
    )
    _setting(
        st.checkbox,
        "silver_ignore",
        "忽略底部银色物体（固定机位）",
        help="仅用于已确认的机位、1920 × 1080 完整画面；相机移动后请关闭。",
    )
    with st.expander("观察范围"):
        _setting(st.radio, "range_mode", "范围", ["完整画面", "自定义"], horizontal=True)
        first, second = st.columns(2)
        for column, name, label, minimum, maximum in (
            (first, "left", "左边界（%）", 0.0, 99.99),
            (second, "top", "上边界（%）", 0.0, 99.99),
            (first, "right", "右边界（%）", 0.01, 100.0),
            (second, "bottom", "下边界（%）", 0.01, 100.0),
        ):
            with column:
                _setting(
                    st.number_input,
                    name,
                    label,
                    min_value=minimum,
                    max_value=maximum,
                    step=1.0,
                    format="%.2f",
                )
        st.caption("只判断预览范围；持续未检测到不代表物品从整个环境中消失。")
    with st.expander("实验行为识别"):
        _setting(st.checkbox, "behavior_enabled", "启用在座与疑似饮水识别")
        _setting(st.text_input, "posture_manifest", "姿态模型清单")
        _setting(st.text_input, "drinking_manifest", "饮水模型清单")
        st.caption("固定机位实验模型，尚未完成动作质量验收；视角变化后不能沿用旧结论。")
    with st.expander("实验笔记本开合"):
        _setting(st.checkbox, "laptop_enabled", "启用笔记本开合识别")
        _setting(
            st.text_input,
            "laptop_manifest",
            "笔记本能力清单",
            help="需要完整验收后的能力清单，不能使用单独的开合分类训练清单。",
        )
        st.caption("未验收、遮挡、电脑移出画面或画面过期时一律显示未知。")
    render_active_settings(runtime)


def render_active_settings(runtime):
    diagnostics = runtime.diagnostics()
    model = diagnostics.get("active_object_model")
    if model:
        st.info(f"当前实际启用：{model['label']}")
        st.caption(f"模型校验标识：{model['sha256'][:12]}")
    else:
        st.caption("当前实际启用：无（尚未开始或已停止观察）")
    active = diagnostics["camera_settings"]
    if active:
        camera, width, height, region, interval, recheck, ignore = active
        region_text = (
            "完整画面"
            if region is None
            else "左 {:.0%}、上 {:.0%}、右 {:.0%}、下 {:.0%}".format(*region)
        )
        st.caption(
            f"当前启用设置（请求）：摄像头 {camera} · {width} × {height} · {region_text} · "
            f"每 {interval:g} 秒采样 · 杯子增强{'已开启' if recheck else '未开启'} · "
            f"银色物体忽略{'已开启' if ignore else '未开启'}"
        )
        observation, _ = runtime.snapshot()
        if observation and observation.width and observation.height:
            st.caption(f"当前预览尺寸：{observation.width} × {observation.height}")
    st.caption("使用非镜像坐标；停止、断连或过期画面均表示当前未知。")


def start_observation(runtime):
    preferences = observation_preferences(runtime)
    region = tuple(preferences[name] / 100 for name in ("left", "top", "right", "bottom"))
    if preferences["range_mode"] == "完整画面":
        region = (0.0, 0.0, 1.0, 1.0)
    if region[0] >= region[2] or region[1] >= region[3]:
        return ToolResult(ok=False, error="观察范围无效：左边界须小于右边界，上边界须小于下边界。")
    interval = 1.0 if preferences["speed"].startswith("标准") else 2.0
    resolution = preferences["resolution"]
    model_id = preferences["model"]
    model_path = (
        (
            get_object_model_preset(model_id).path
            if model_id != "configured"
            else runtime.config.model_path
        )
        .expanduser()
        .resolve()
    )
    behavior = (
        preferences["behavior_enabled"],
        Path(preferences["posture_manifest"]) if preferences["posture_manifest"].strip() else None,
        Path(preferences["drinking_manifest"])
        if preferences["drinking_manifest"].strip()
        else None,
    )
    laptop = (
        preferences["laptop_enabled"],
        Path(preferences["laptop_manifest"]) if preferences["laptop_manifest"].strip() else None,
    )
    requested = (
        int(preferences["camera"]),
        *resolution,
        None if region == (0.0, 0.0, 1.0, 1.0) else region,
        interval,
        preferences["cup_recheck"],
        preferences["silver_ignore"],
    )
    diagnostics = runtime.diagnostics()
    active_model = diagnostics.get("active_object_model")
    observation, _ = runtime.snapshot()
    reconnect = observation is not None and observation.status in {"disconnected", "error"}
    changed = (
        diagnostics["camera_settings"] != requested
        or bool(
            diagnostics.get("behavior_settings")
            and diagnostics["behavior_settings"][:3] != behavior
        )
        or bool(diagnostics.get("laptop_settings") and diagnostics["laptop_settings"][:2] != laptop)
        or bool(active_model and Path(active_model["path"]).resolve() != model_path)
    )
    if reconnect or (runtime.camera_running and changed):
        stopped = runtime.stop_camera()
        if not stopped.ok:
            return stopped
    return runtime.start_camera(
        int(preferences["camera"]),
        interval,
        resolution=resolution,
        observation_region=region,
        cup_scale_recheck=preferences["cup_recheck"],
        silver_object_ignore=preferences["silver_ignore"],
        object_model_id=model_id if model_id != "configured" else None,
        behavior_enabled=behavior[0],
        behavior_posture_manifest=behavior[1],
        behavior_drinking_manifest=behavior[2],
        laptop_enabled=laptop[0],
        laptop_capability_manifest=laptop[1],
    )
