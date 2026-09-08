"""Offline tests cannot accidentally make network calls or open physical cameras."""

import os
import socket
from pathlib import Path
from tempfile import gettempdir
from threading import local

os.environ.setdefault("MPLCONFIGDIR", str(Path(gettempdir()) / "vaa-matplotlib"))
os.environ.setdefault("AGENTS_DISABLE_TRACING", "1")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def offline_boundaries(request, monkeypatch):
    if request.node.get_closest_marker("live_api") or request.node.get_closest_marker("camera"):
        return

    # Local user settings must not alter offline cases or load private credentials.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for variable in tuple(os.environ):
        if variable.startswith("VAA_"):
            monkeypatch.delenv(variable, raising=False)

    def denied(*args, **kwargs):
        raise AssertionError("Offline test attempted external network or physical camera access")

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    internal_pair = local()

    def guarded_connect(sock, address):
        if getattr(internal_pair, "active", False):
            # Windows implements the stdlib socketpair using its own loopback listener.
            # The allowance exists only inside that trusted constructor on this thread.
            return original_connect(sock, address)
        return denied(sock, address)

    def local_socketpair(*args, **kwargs):
        previous = getattr(internal_pair, "active", False)
        internal_pair.active = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            internal_pair.active = previous

    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", denied)
    import cv2

    monkeypatch.setattr(cv2, "VideoCapture", denied)
    for variable in ("VAA_API_KEY", "VAA_API_BASE_URL", "VAA_AGENT_MODEL"):
        monkeypatch.setenv(variable, "")
