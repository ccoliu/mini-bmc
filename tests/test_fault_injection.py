"""Faults injected into the real sensord, observed through the Redfish API."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.main import create_app
from conftest import Sensord
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
CHASSIS = "/redfish/v1/Chassis/1"
SENSORS = f"{CHASSIS}/Sensors"


@pytest.fixture
def api(sensord: Sensord, tmp_path: Path) -> TestClient:
    settings = Settings(
        sensord_socket=sensord.socket_path, password="pw", firmware_dir=str(tmp_path)
    )
    return TestClient(create_app(settings), headers=AUTH)


def wait_until(api: TestClient, path: str, ready: Callable[[dict[str, Any]], bool]) -> Any:
    """GETs path until ready(body); sensord applies most faults at once, stuck takes ticks."""
    deadline = time.monotonic() + 2
    while not ready(body := api.get(path).json()):
        assert time.monotonic() < deadline, f"{path} never got there: {body['Status']}"
        time.sleep(0.05)
    return body


def assert_all_healthy(api: TestClient) -> None:
    for member in api.get(SENSORS).json()["Members"]:
        assert api.get(member["@odata.id"]).json()["Status"] == {"State": "Enabled", "Health": "OK"}
    assert api.get(CHASSIS).json()["Status"]["HealthRollup"] == "OK"


@pytest.mark.parametrize(
    ("sensor", "mode", "status", "rollup"),
    [
        ("cpu_temp", "overtemp", {"State": "Enabled", "Health": "Critical"}, "Critical"),
        ("fan1_rpm", "disconnected", {"State": "UnavailableOffline"}, "Warning"),
        ("psu_watts", "noise", {"State": "Enabled", "Health": "OK"}, "OK"),  # noisy, not wrong
    ],
)
def test_fault_surfaces_in_redfish_and_clears(
    api: TestClient, sensord: Sensord, sensor: str, mode: str, status: dict, rollup: str
) -> None:
    assert_all_healthy(api)
    assert sensord.client().call(cmd="inject_fault", sensor=sensor, mode=mode)["ok"]
    wait_until(api, f"{SENSORS}/{sensor}", lambda body: body["Status"] == status)
    assert api.get(CHASSIS).json()["Status"]["HealthRollup"] == rollup

    assert sensord.client().call(cmd="clear_faults")["ok"]
    assert_all_healthy(api)


def test_stuck_sensor_is_flagged_once_it_stops_changing(api: TestClient, sensord: Sensord) -> None:
    inlet = f"{SENSORS}/inlet_temp"
    assert sensord.client().call(cmd="inject_fault", sensor="inlet_temp", mode="stuck")["ok"]
    # A silent failure: at first nothing looks wrong.
    first = api.get(inlet).json()
    assert first["Status"] == {"State": "Enabled", "Health": "OK"}

    body = wait_until(api, inlet, lambda body: body["Status"]["Health"] == "Warning")
    assert body["Reading"] == first["Reading"]
    (condition,) = body["Status"]["Conditions"]
    assert condition["MessageId"] == "SensorEvent.1.2.InvalidSensorReading"
    assert condition["MessageArgs"] == ["inlet_temp"]
    assert api.get(CHASSIS).json()["Status"]["HealthRollup"] == "Warning"

    assert sensord.client().call(cmd="clear_faults")["ok"]
    assert_all_healthy(api)  # no Conditions left behind either
