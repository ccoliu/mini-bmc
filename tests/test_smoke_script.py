"""scripts/smoke_test.sh must report every failure as a FAIL line, never crash (issue #3)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.sh"
CHECKS = 6


def run_smoke(base_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BASE_URL": base_url}
    return subprocess.run([SCRIPT], env=env, capture_output=True, text=True, timeout=60)


def assert_reports_failures(result: subprocess.CompletedProcess[str], failed: int):
    output = result.stdout + result.stderr
    assert "Traceback" not in output
    lines = result.stdout.splitlines()
    assert len(lines) == CHECKS + 1, "every check ran and the summary was printed"
    assert sum(line.startswith("FAIL") for line in lines) == failed
    assert lines[-1] == f"{failed} check(s) failed"
    assert result.returncode == 1


@pytest.fixture
def closed_port() -> int:
    """A port nothing listens on (bound and released, so it was free a moment ago)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AlwaysUnauthorized(BaseHTTPRequestHandler):
    """Answers every request with 401 and a Redfish error body (which has no Reading)."""

    def _reply(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"error": {"code": "Base.1.24.NoValidSession"}}).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def unauthorized_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AlwaysUnauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_stack_down_reports_every_check_as_failed(closed_port: int) -> None:
    result = run_smoke(f"http://127.0.0.1:{closed_port}")
    assert_reports_failures(result, CHECKS)
    assert "is the stack running?" in result.stdout


def test_error_body_without_reading_is_a_fail_line(unauthorized_server: str) -> None:
    result = run_smoke(unauthorized_server)
    # Only "chassis requires auth" and "wrong password is rejected" expect a 401.
    assert_reports_failures(result, failed=CHECKS - 2)
    assert "FAIL  cpu_temp reading: HTTP 401" in result.stdout
