from __future__ import annotations

import pytest
from conftest import FakeSensord
from fastapi.testclient import TestClient

from app.sensor_catalog import CATALOG

CHASSIS = "/redfish/v1/Chassis/1"


def test_chassis_collection(client: TestClient) -> None:
    body = client.get("/redfish/v1/Chassis").json()
    assert body["Members"] == [{"@odata.id": CHASSIS}]
    assert body["Members@odata.count"] == 1


def test_chassis_resource(client: TestClient) -> None:
    body = client.get(CHASSIS).json()
    assert body["@odata.id"] == CHASSIS
    assert body["@odata.type"].startswith("#Chassis.v1_")
    assert body["ChassisType"] == "RackMount"
    assert body["Status"] == {"State": "Enabled", "Health": "OK", "HealthRollup": "OK"}
    assert body["Sensors"] == {"@odata.id": f"{CHASSIS}/Sensors"}


@pytest.mark.parametrize(
    ("faults", "rollup"),
    [
        ([("cpu_temp", 86.0, "warning")], "Warning"),
        ([("cpu_temp", 86.0, "warning"), ("fan1_rpm", 500.0, "critical")], "Critical"),
        ([("psu_watts", None, "unavailable")], "Warning"),
    ],
)
def test_health_rollup_is_worst_sensor(
    client: TestClient, fake_sensord: FakeSensord, faults: list, rollup: str
) -> None:
    for name, value, status in faults:
        fake_sensord.set(name, value, status)
    assert client.get(CHASSIS).json()["Status"]["HealthRollup"] == rollup


@pytest.mark.parametrize(
    "path",
    ["/redfish/v1/Chassis/2", "/redfish/v1/Chassis/2/Sensors", "/redfish/v1/Chassis/2/Sensors/x"],
)
def test_missing_chassis_or_sensor_is_404(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["@Message.ExtendedInfo"][0]["MessageArgs"] == ["Chassis", "2"]


def test_sensor_collection_lists_catalog(client: TestClient) -> None:
    body = client.get(f"{CHASSIS}/Sensors").json()
    assert [m["@odata.id"] for m in body["Members"]] == [
        f"{CHASSIS}/Sensors/{name}" for name in CATALOG
    ]
    assert body["Members@odata.count"] == len(CATALOG)


def test_sensor_resource(client: TestClient) -> None:
    body = client.get(f"{CHASSIS}/Sensors/inlet_temp").json()
    assert body == {
        "@odata.id": f"{CHASSIS}/Sensors/inlet_temp",
        "@odata.type": body["@odata.type"],
        "Id": "inlet_temp",
        "Name": "Inlet Temperature",
        "Reading": 24.0,
        "ReadingType": "Temperature",
        "ReadingUnits": "Cel",
        "PhysicalContext": "Intake",
        "Status": {"State": "Enabled", "Health": "OK"},
    }
    assert body["@odata.type"].startswith("#Sensor.v1_")


@pytest.mark.parametrize(
    ("status", "value", "expected"),
    [
        ("warning", 86.0, {"State": "Enabled", "Health": "Warning"}),
        ("critical", 99.0, {"State": "Enabled", "Health": "Critical"}),
        ("unavailable", None, {"State": "UnavailableOffline"}),
    ],
)
def test_sensor_status_mapping(
    client: TestClient, fake_sensord: FakeSensord, status: str, value: float | None, expected: dict
) -> None:
    fake_sensord.set("cpu_temp", value, status)
    body = client.get(f"{CHASSIS}/Sensors/cpu_temp").json()
    assert body["Status"] == expected
    assert body["Reading"] == value


def test_unknown_sensor(client: TestClient) -> None:
    r = client.get(f"{CHASSIS}/Sensors/nope")
    assert r.status_code == 404
    assert r.json()["error"]["@Message.ExtendedInfo"][0]["MessageArgs"] == ["Sensor", "nope"]


def test_sensor_known_to_catalog_but_not_to_sensord(
    client: TestClient, fake_sensord: FakeSensord
) -> None:
    del fake_sensord.readings["gpu_temp"]
    assert client.get(f"{CHASSIS}/Sensors/gpu_temp").status_code == 404


@pytest.mark.parametrize("path", [CHASSIS, f"{CHASSIS}/Sensors/cpu_temp"])
def test_sensord_down_is_503_with_retry_after(
    client: TestClient, fake_sensord: FakeSensord, path: str
) -> None:
    fake_sensord.down = True
    r = client.get(path)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert r.json()["error"]["code"] == "Base.1.24.ServiceTemporarilyUnavailable"


def test_collections_do_not_need_sensord(client: TestClient, fake_sensord: FakeSensord) -> None:
    fake_sensord.down = True
    assert client.get("/redfish/v1/Chassis").status_code == 200
    assert client.get(f"{CHASSIS}/Sensors").status_code == 200
