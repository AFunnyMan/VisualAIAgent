from pathlib import Path

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
