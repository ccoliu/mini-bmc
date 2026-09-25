"""The chassis collection, the single chassis, and its Sensors collection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import RedfishError
from ..odata import ROOT, collection, link, resource
from ..sensor_catalog import CATALOG
from ..sensord_client import SensordClient, SensordRequestError, SensorReading

CHASSIS_ID = "1"
CHASSIS = f"{ROOT}/Chassis/{CHASSIS_ID}"
SENSORS = f"{CHASSIS}/Sensors"

# sensord status -> Redfish Status. An unreadable sensor has no meaningful Health.
STATUS_MAP: dict[str, dict[str, str]] = {
    "ok": {"State": "Enabled", "Health": "OK"},
    "warning": {"State": "Enabled", "Health": "Warning"},
    "critical": {"State": "Enabled", "Health": "Critical"},
    "unavailable": {"State": "UnavailableOffline"},
}
SEVERITY = {"OK": 0, "Warning": 1, "Critical": 2}

router = APIRouter()


def sensord(request: Request) -> SensordClient:
    return request.app.state.sensord


def check_chassis_id(chassis_id: str) -> None:
    if chassis_id != CHASSIS_ID:
        raise RedfishError(404, "ResourceNotFound", "Chassis", chassis_id)


def sensor_status(reading: SensorReading) -> dict[str, str]:
    # Copy, so callers can never mutate the shared table.
    return dict(STATUS_MAP.get(reading.status, STATUS_MAP["unavailable"]))


def health_rollup(readings: list[SensorReading]) -> str:
    """Worst health across all sensors. A sensor we can no longer read is itself a
    problem (monitoring is lost), so it counts as Warning."""
    healths = [sensor_status(r).get("Health", "Warning") for r in readings]
    return max(healths, key=SEVERITY.__getitem__, default="OK")


def sensor_body(reading: SensorReading) -> dict[str, Any]:
    info = CATALOG[reading.name]
    return resource(
        "Sensor",
        f"{SENSORS}/{reading.name}",
        reading.name,
        info.name,
        Reading=reading.value,
        ReadingType=info.reading_type,
        ReadingUnits=info.reading_units,
        PhysicalContext=info.physical_context,
        Status=sensor_status(reading),
    )


@router.get(f"{ROOT}/Chassis")
def chassis_collection() -> dict[str, Any]:
    return collection("ChassisCollection", f"{ROOT}/Chassis", "Chassis Collection", [CHASSIS])


@router.get(f"{ROOT}/Chassis/{{chassis_id}}")
async def chassis(chassis_id: str, request: Request) -> dict[str, Any]:
    check_chassis_id(chassis_id)
    readings = await sensord(request).read_all()
    return resource(
        "Chassis",
        CHASSIS,
        CHASSIS_ID,
        "mini-bmc Chassis",
        ChassisType="RackMount",
        Status={"State": "Enabled", "Health": "OK", "HealthRollup": health_rollup(readings)},
        Sensors=link(SENSORS),
        # Literal path: importing it from systems.py would be a circular import.
        Links={"ComputerSystems": [link(f"{ROOT}/Systems/1")]},
    )


@router.get(f"{ROOT}/Chassis/{{chassis_id}}/Sensors")
def sensor_collection(chassis_id: str) -> dict[str, Any]:
    check_chassis_id(chassis_id)
    members = [f"{SENSORS}/{sensor_id}" for sensor_id in CATALOG]
    return collection("SensorCollection", SENSORS, "Sensors", members)


@router.get(f"{ROOT}/Chassis/{{chassis_id}}/Sensors/{{sensor_id}}")
async def sensor(chassis_id: str, sensor_id: str, request: Request) -> dict[str, Any]:
    check_chassis_id(chassis_id)
    if sensor_id not in CATALOG:
        raise RedfishError(404, "ResourceNotFound", "Sensor", sensor_id)

    try:
        reading = await sensord(request).read(sensor_id)
    except SensordRequestError as exc:
        if exc.code == "unknown_sensor":
            raise RedfishError(404, "ResourceNotFound", "Sensor", sensor_id) from exc
        raise
    return sensor_body(reading)
