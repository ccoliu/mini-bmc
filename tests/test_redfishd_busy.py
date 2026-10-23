"""redfishd answers 503 + Retry-After while sensord is at its client limit (issue #1)."""

from __future__ import annotations

import base64
import time

import pytest
from app.config import Settings
from app.main import create_app
from conftest import Sensord
from fastapi.testclient import TestClient

MAX_CLIENTS = 16  # sensord/src/server.c
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
PATHS = ["/redfish/v1/Chassis/1", "/redfish/v1/Chassis/1/Sensors/cpu_temp"]


@pytest.fixture
def api(sensord: Sensord) -> TestClient:
    settings = Settings(sensord_socket=sensord.socket_path, password="pw")
    return TestClient(create_app(settings), headers=AUTH)


@pytest.mark.parametrize("path", PATHS)
def test_busy_sensord_is_503_and_recovers(sensord: Sensord, api: TestClient, path: str) -> None:
    held = [sensord.client() for _ in range(MAX_CLIENTS)]
    for c in held:
        assert c.call(cmd="read_all")["ok"]  # proves sensord accepted every one of them

    r = api.get(path)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert r.json()["error"]["code"] == "Base.1.24.ServiceTemporarilyUnavailable"

    held[0].close()  # free one slot; sensord notices on its next poll()
    deadline = time.monotonic() + 2
    while (status := api.get(path).status_code) != 200 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert status == 200
