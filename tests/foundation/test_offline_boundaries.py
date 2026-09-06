import socket

import pytest


def test_stdlib_local_socketpair_can_wake_asyncio_without_allowing_other_connections():
    reader, writer = socket.socketpair()
    try:
        writer.sendall(b"wake")
        assert reader.recv(4) == b"wake"
    finally:
        reader.close()
        writer.close()
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="Offline test"):
            sock.connect(("127.0.0.1", 65534))
        with pytest.raises(AssertionError, match="Offline test"):
            sock.connect(("example.invalid", 443))
