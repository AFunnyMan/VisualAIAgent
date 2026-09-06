"""Offline tests cannot accidentally make network calls or open physical cameras."""

import os
import socket
from pathlib import Path
from tempfile import gettempdir

os.environ.setdefault("MPLCONFIGDIR", str(Path(gettempdir()) / "vaa-matplotlib"))
os.environ.setdefault("AGENTS_DISABLE_TRACING", "1")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def offline_boundaries(request, monkeypatch):
    if request.node.get_closest_marker("live_api") or request.node.get_closest_marker("camera"):
        return

    def denied(*args, **kwargs):
        raise AssertionError("Offline test attempted external network or physical camera access")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    import cv2

    monkeypatch.setattr(cv2, "VideoCapture", denied)
    for variable in ("VAA_API_KEY", "VAA_API_BASE_URL", "VAA_AGENT_MODEL"):
        monkeypatch.setenv(variable, "")
