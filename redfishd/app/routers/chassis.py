"""The chassis collection, the single chassis, and its Sensors collection."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import require_auth
from ..errors import RedfishError
from ..odata import ROOT, collection, link, registry_message, resource
from ..query import expand_levels
from ..sensor_catalog import CATALOG
from ..sensord_client import SensordClient, SensordRequestError, SensorReading

CHASSIS_ID = "1"
CHASSIS = f"{ROOT}/Chassis/{CHASSIS_ID}"
SENSORS = f"{CHASSIS}/Sensors"

log = logging.getLogger(__name__)

# sensord status -> Redfish Status. An unreadable sensor has no meaningful Health.
STATUS_MAP: dict[str, dict[str, str]] = {
    "ok": {"State": "Enabled", "Health": "OK"},
    "warning": {"State": "Enabled", "Health": "Warning"},
    "critical": {"State": "Enabled", "Health": "Critical"},
    "unavailable": {"State": "UnavailableOffline"},
}
SEVERITY = {"OK": 0, "Warning": 1, "Critical": 2}

# The subset of the DMTF SensorEvent.1.2.0 registry this service emits.
SENSOR_EVENT = "SensorEvent.1.2"
SENSOR_EVENT_MESSAGES: dict[str, tuple[str, str]] = {
    "InvalidSensorReading": ("Invalid reading received from sensor '%1'.", "Warning"),
}


router = APIRouter(dependencies=[Depends(require_auth)])


def sensord(request: Request) -> SensordClient:
    return request.app.state.sensord


def check_chassis_id(chassis_id: str) -> None:
    if chassis_id != CHASSIS_ID:
        raise RedfishError(404, "ResourceNotFound", "Chassis", chassis_id)


def worst(*healths: str) -> str:
    return max(healths, key=SEVERITY.__getitem__)


def stuck_condition(sensor_name: str) -> dict[str, Any]:
    """A Status.Conditions entry. A Condition is not a Message: no @odata.type, and
    the severity goes in Severity, not MessageSeverity."""
    msg = registry_message(SENSOR_EVENT, SENSOR_EVENT_MESSAGES, "InvalidSensorReading", sensor_name)
    return {
        "MessageId": msg["MessageId"],
        "Message": msg["Message"],
        "MessageArgs": msg["MessageArgs"],
        "Severity": msg["MessageSeverity"],
        "Resolution": "The reading has stopped changing, so Reading shows the last value "
        "received. Check the sensor and its connection.",
    }


def sensor_status(reading: SensorReading) -> dict[str, Any]:
    # Copy, so callers can never mutate the shared table.
    status: dict[str, Any] = dict(STATUS_MAP.get(reading.status, STATUS_MAP["unavailable"]))
    # A frozen value cannot be trusted, even when it is within the thresholds.
    if reading.stuck and "Health" in status:
        status["Health"] = worst(status["Health"], "Warning")
        status["Conditions"] = [stuck_condition(reading.name)]
    return status


def health_rollup(readings: list[SensorReading]) -> str:
    """Worst health across all sensors. A sensor we can no longer read is itself a
    problem (monitoring is lost), so it counts as Warning."""
    return worst("OK", *(sensor_status(r).get("Health", "Warning") for r in readings))


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
    # Roll up only what the API exposes: a Critical sensor that has no Redfish resource
    # would make the chassis Critical with no visible cause.
    unknown = sorted(r.name for r in readings if r.name not in CATALOG)
    if unknown:
        log.warning("sensord reports sensors missing from CATALOG: %s:", ", ".join(unknown))
    readings = [r for r in readings if r.name in CATALOG]
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


@router.get(f"{ROOT}/Chassis/{{chassis_id}}/Sensors")  # in query.EXPANDABLE
async def sensor_collection(
    chassis_id: str, request: Request, levels: int = Depends(expand_levels)
) -> dict[str, Any]:
    check_chassis_id(chassis_id)
    if not levels:
        members = [f"{SENSORS}/{sensor_id}" for sensor_id in CATALOG]
        return collection("SensorCollection", SENSORS, "Sensors", members)
    # Expanded: one read_all, so every member is a reading from the same moment. A sensor
    # sensord does not report is left out, just as a GET on it would be a 404.
    readings = {r.name: r for r in await sensord(request).read_all()}
    bodies = [sensor_body(readings[name]) for name in CATALOG if name in readings]
    body = collection("SensorCollection", SENSORS, "Sensors", [b["@odata.id"] for b in bodies])
    body["Members"] = bodies
    return body


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
