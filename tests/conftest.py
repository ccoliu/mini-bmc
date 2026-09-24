"""Shared fixtures: start a real sensord on a private socket for each test."""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BIN = REPO_ROOT / "sensord" / "build" / "sensord"


class SensordClient:
    """Line-oriented client for the sensord socket protocol."""

    def __init__(self, path: str, timeout: float = 2.0) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.reader = self.sock.makefile("rb")

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_line(self) -> dict:
        line = self.reader.readline()
        if not line:
            raise ConnectionError("sensord closed the connection")
        assert line.endswith(b"\n")
        return json.loads(line)

    def call(self, **request: str) -> dict:
        self.send_raw(json.dumps(request).encode() + b"\n")
        return self.read_line()

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


class Sensord:
    def __init__(self, proc: subprocess.Popen, socket_path: str) -> None:
        self.proc = proc
        self.socket_path = socket_path
        self._clients: list[SensordClient] = []

    def client(self) -> SensordClient:
        c = SensordClient(self.socket_path)
        self._clients.append(c)
        return c

    def stop(self) -> tuple[int, str]:
        """SIGTERM the daemon; returns (exit code, stderr)."""
        for c in self._clients:
            c.close()
        self._clients.clear()
        if self.proc.poll() is None:
            self.proc.terminate()
        _, err = self.proc.communicate(timeout=5)
        return self.proc.returncode, err.decode(errors="replace")


def start_sensord(socket_path: str, *args: str, tick_ms: int = 50) -> Sensord:
    """Starts sensord and waits for its "listening on" line on stderr.

    Waiting on the daemon's own announcement (rather than polling the socket) matters
    when another instance already owns the path: the socket is connectable, yet this
    process is about to exit with an error.
    """
    binary = os.environ.get("SENSORD_BIN", str(DEFAULT_BIN))
    if not Path(binary).exists():
        pytest.fail(f"sensord binary not found at {binary}; run `make -C sensord` first")
    proc = subprocess.Popen(
        [binary, "-s", socket_path, "-t", str(tick_ms), "-S", "1", *args],
        stderr=subprocess.PIPE,
    )
    assert proc.stderr is not None
    fd = proc.stderr.fileno()
    buf = b""
    deadline = time.monotonic() + 5
    while b"\n" not in buf:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            proc.kill()
            proc.wait()
            pytest.fail("sensord did not start listening within 5 s")
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk
    if not buf.startswith(b"sensord: listening on "):
        _, rest = proc.communicate(timeout=5)
        err = (buf + rest).decode(errors="replace")
        pytest.fail(f"sensord exited early ({proc.returncode}): {err}")
    return Sensord(proc, socket_path)


@pytest.fixture
def socket_path():
    # Not tmp_path: sun_path is limited to 108 bytes and pytest's paths embed the test name.
    d = tempfile.mkdtemp(prefix="sensord-")
    yield os.path.join(d, "s.sock")
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sensord(socket_path: str):
    daemon = start_sensord(socket_path)
    yield daemon
    rc, err = daemon.stop()
    # A non-zero exit here means the daemon crashed or a sanitizer reported an error.
    assert rc == 0, f"sensord exited with {rc}:\n{err}"


@pytest.fixture
def client(sensord: Sensord) -> SensordClient:
    return sensord.client()
