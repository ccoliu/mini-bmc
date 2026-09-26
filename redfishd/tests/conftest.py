"""Fixtures for redfishd unit tests (no real sensord needed)."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sensord_client import SensordRequestError, SensordUnavailable, SensorReading


class FakeSensord:
    """Stands in for SensordClient; tests edit `readings` or set `down`."""

    def __init__(self) -> None:
        self.down = False
        self.readings = {
            r.name: r
            for r in [
                SensorReading("cpu_temp", 45.0, "C", "ok"),
                SensorReading("gpu_temp", 50.0, "C", "ok"),
                SensorReading("inlet_temp", 24.0, "C", "ok"),
                SensorReading("fan1_rpm", 6000.0, "RPM", "ok"),
                SensorReading("fan2_rpm", 6000.0, "RPM", "ok"),
                SensorReading("psu_watts", 350.0, "W", "ok"),
            ]
        }

    def set(self, name: str, value: float | None, status: str) -> None:
        old = self.readings[name]
        self.readings[name] = SensorReading(name, value, old.unit, status)

    def _check(self) -> None:
        if self.down:
            raise SensordUnavailable("fake sensord is down")

    async def read_all(self) -> list[SensorReading]:
        self._check()
        return list(self.readings.values())

    async def read(self, name: str) -> SensorReading:
        self._check()
        if name not in self.readings:
            raise SensordRequestError("unknown_sensor")
        return self.readings[name]


@pytest.fixture
def settings() -> Settings:
    return Settings(sensord_socket="/nonexistent/sensord.sock", username="admin", password="pw")


@pytest.fixture
def fake_sensord() -> FakeSensord:
    return FakeSensord()


def basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def app(settings: Settings, fake_sensord: FakeSensord) -> FastAPI:
    app = create_app(settings)
    app.state.sensord = fake_sensord
    return app


@pytest.fixture
def client(app: FastAPI, settings: Settings) -> TestClient:
    """Sends valid HTTP Basic credentials on every request."""
    return TestClient(app, headers=basic_auth(settings.username, settings.password))


@pytest.fixture
def anon_client(app: FastAPI) -> TestClient:
    """Same app, no credentials."""
    return TestClient(app)
