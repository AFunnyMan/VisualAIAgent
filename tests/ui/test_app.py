from pathlib import Path
from types import MethodType

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAA_MODEL_PATH", str(tmp_path / "missing.onnx"))
    # Global cached services intentionally survive Streamlit reruns, but not tests.
    import streamlit as st

    st.cache_resource.clear()
    import visual_ai_agent.runtime as runtime_module

    actual_class = runtime_module.ApplicationRuntime
    created = []

    def tracked_runtime(config):
        instance = actual_class(config)
        created.append(instance)
        return instance

    monkeypatch.setattr(runtime_module, "ApplicationRuntime", tracked_runtime)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    app.run(timeout=15)
    yield app
    assert len(created) == 1
    for instance in created:
        instance.close()
    st.cache_resource.clear()


def test_disconnected_page_and_no_history(app):
    assert not app.exception
    assert any("Agent 未连接" in item.value for item in app.warning)
    assert app.chat_input[0].disabled
    assert any("没有这个类别的历史记录" in item.value for item in app.info)


def test_manual_watch_create_cancel_survives_reruns(app):
    assert not app.exception
    app.button(key="FormSubmitter:create_watch-创建关注").click().run()
    assert not app.exception
    cancel = [button for button in app.button if button.label == "取消"]
    assert len(cancel) == 1
    app.run()
    assert len([button for button in app.button if button.label == "取消"]) == 1
    cancel = [button for button in app.button if button.label == "取消"][0]
    cancel.click().run()
    assert not app.exception
    assert not [button for button in app.button if button.label == "取消"]
    assert any("已取消" in item.value for item in app.markdown)


def test_missing_model_does_not_open_camera(app):
    next(button for button in app.button if button.label == "开始观察").click().run()
    assert not app.exception
    assert any("启动失败" in item.value for item in app.error)


def _configured_app(tmp_path, monkeypatch, *, region="", fake_camera=False):
    monkeypatch.setenv("VAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAA_MODEL_PATH", str(tmp_path / "missing.onnx"))
    monkeypatch.setenv("VAA_OBSERVATION_REGION", region)
    import streamlit as st

    st.cache_resource.clear()
    import visual_ai_agent.runtime as runtime_module

    actual_class = runtime_module.ApplicationRuntime
    created = []
    calls = []

    if fake_camera:

        class FakeWorker:
            running = False

            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                self.running = True

            def stop(self):
                self.running = False

            def snapshot(self):
                return None, None

        monkeypatch.setattr(runtime_module, "CameraSource", lambda **_kwargs: object())
        monkeypatch.setattr(runtime_module, "YoloOnnxDetector", lambda *_args, **_kwargs: object())
        monkeypatch.setattr(runtime_module, "VisionWorker", FakeWorker)

    def tracked_runtime(config):
        instance = actual_class(config)
        created.append(instance)
        original_start = instance.start_camera
        original_stop = instance.stop_camera

        def start_camera(self, *args, **kwargs):
            calls.append(("start", args, kwargs))
            return original_start(*args, **kwargs)

        def stop_camera(self):
            calls.append(("stop", (), {}))
            return original_stop()

        instance.start_camera = MethodType(start_camera, instance)
        instance.stop_camera = MethodType(stop_camera, instance)
        return instance

    monkeypatch.setattr(runtime_module, "ApplicationRuntime", tracked_runtime)
    tested_app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    tested_app.run(timeout=15)
    return tested_app, created, calls, st


def test_fractional_configured_region_is_preserved(tmp_path, monkeypatch):
    tested_app, created, _calls, st = _configured_app(
        tmp_path, monkeypatch, region="0.1234,0.2345,0.8765,0.9876"
    )
    try:
        assert not tested_app.exception
        values = {item.label: item.value for item in tested_app.number_input}
        assert values["左边界（%）"] == pytest.approx(12.34)
        assert values["上边界（%）"] == pytest.approx(23.45)
        assert values["右边界（%）"] == pytest.approx(87.65)
        assert values["下边界（%）"] == pytest.approx(98.76)
    finally:
        for instance in created:
            instance.close()
        st.cache_resource.clear()


def test_changed_camera_settings_stop_then_start_with_selected_values(tmp_path, monkeypatch):
    tested_app, created, calls, st = _configured_app(tmp_path, monkeypatch, fake_camera=True)
    try:
        next(button for button in tested_app.button if button.label == "开始观察").click().run()
        assert not tested_app.exception
        next(item for item in tested_app.selectbox if item.label == "采集清晰度").set_value(
            (1280, 720)
        ).run()
        next(item for item in tested_app.radio if item.label == "范围").set_value("自定义").run()
        inputs = {item.label: item for item in tested_app.number_input}
        inputs["左边界（%）"].set_value(12.5).run()
        inputs = {item.label: item for item in tested_app.number_input}
        inputs["右边界（%）"].set_value(87.5).run()
        next(button for button in tested_app.button if button.label == "开始观察").click().run()
        assert not tested_app.exception
        action_names = [call[0] for call in calls]
        assert action_names[-2:] == ["stop", "start"]
        _, args, kwargs = calls[-1]
        assert args == (0, 1.0)
        assert kwargs["resolution"] == (1280, 720)
        assert kwargs["observation_region"] == (0.125, 0.0, 0.875, 1.0)
        assert any("当前启用设置（请求）" in item.value for item in tested_app.caption)
    finally:
        for instance in created:
            instance.close()
        st.cache_resource.clear()
