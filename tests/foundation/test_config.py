from datetime import datetime

import pytest

from visual_ai_agent.config import Config
from visual_ai_agent.models import SceneObservation


def test_no_configuration_is_disconnected_and_secret_not_in_repr():
    assert not Config().agent_connected
    assert not Config(api_key="sensitive").agent_connected
    assert "sensitive" not in repr(Config(api_key="sensitive"))
    assert Config(
        api_key="sensitive", api_base_url="https://example.com/v1", agent_model="explicit"
    ).agent_connected


@pytest.mark.parametrize(
    "values",
    [
        {"api_timeout_seconds": 0},
        {"daily_auto_limit": -1},
        {"sample_interval": 0.1},
        {"api_base_url": "http://remote.example/v1"},
        {"api_mode": "unknown"},
    ],
)
def test_invalid_configuration(values):
    with pytest.raises(ValueError):
        Config(**values)


def test_naive_observation_time_is_rejected():
    with pytest.raises(ValueError):
        SceneObservation(
            observed_at=datetime(2026, 1, 1), monotonic_at=1, status="running", fresh=True
        )


@pytest.mark.parametrize("resolution", [(800, 600), (1280, 480), (1920, 720)])
def test_rejects_unsupported_camera_resolution(resolution):
    with pytest.raises(ValueError, match="Camera resolution"):
        Config(camera_width=resolution[0], camera_height=resolution[1])


@pytest.mark.parametrize(
    "region",
    [
        (float("nan"), 0, 1, 1),
        (0, 0, float("inf"), 1),
        (-0.1, 0, 1, 1),
        (0, 0, 1.1, 1),
        (0.8, 0, 0.2, 1),
        (0, 0.7, 1, 0.7),
    ],
)
def test_rejects_invalid_observation_region(region):
    with pytest.raises(ValueError, match="Observation region"):
        Config(observation_region=region)


def test_environment_loads_camera_settings_and_canonicalizes_full_frame(monkeypatch):
    monkeypatch.setenv("VAA_CAMERA_WIDTH", "1280")
    monkeypatch.setenv("VAA_CAMERA_HEIGHT", "720")
    monkeypatch.setenv("VAA_OBSERVATION_REGION", "0, 0, 1, 1")
    config = Config.from_env(dotenv_path=None)
    assert (config.camera_width, config.camera_height) == (1280, 720)
    assert config.observation_region is None


def test_environment_rejects_malformed_observation_region(monkeypatch):
    monkeypatch.setenv("VAA_OBSERVATION_REGION", "0,broken,1,1")
    with pytest.raises(ValueError, match="VAA_OBSERVATION_REGION"):
        Config.from_env(dotenv_path=None)


def test_environment_loads_strict_cup_scale_recheck(monkeypatch):
    monkeypatch.setenv("VAA_CUP_SCALE_RECHECK", "TRUE")
    assert Config.from_env(dotenv_path=None).cup_scale_recheck is True
    monkeypatch.setenv("VAA_CUP_SCALE_RECHECK", "yes")
    with pytest.raises(ValueError, match="VAA_CUP_SCALE_RECHECK"):
        Config.from_env(dotenv_path=None)
