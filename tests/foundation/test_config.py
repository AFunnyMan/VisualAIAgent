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
