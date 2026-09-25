"""The computer system collection, the single system, and its Reset action."""

import json
from typing import Any

from fastapi import APIRouter, Request, Response

from ..errors import RedfishError
from ..odata import ROOT, link, resource, collection
from ..power import RESET_RESULT, PowerController
from .chassis import CHASSIS

SYSTEM_ID = "1"
SYSTEM = f"{ROOT}/Systems/{SYSTEM_ID}"
ACTION = "ComputerSystem.Reset"
RESET_TARGET = f"{SYSTEM}/Actions/{ACTION}"

router = APIRouter()


def power(request: Request) -> PowerController:
    return request.app.state.power


def check_system_id(system_id: str) -> None:
    if system_id != SYSTEM_ID:
        raise RedfishError(404, "ResourceNotFound", "ComputerSystem", system_id)


def parse_reset_body(raw: bytes) -> str:
    """Validates the action body by hand so each failure maps to a specific Redfish
    message that names the offending parameter."""
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise RedfishError(400, "MalformedJSON") from exc
    if not isinstance(body, dict):
        raise RedfishError(400, "MalformedJSON")
    for name in body:
        if name != "ResetType":
            raise RedfishError(400, "ActionParameterUnknown", ACTION, name)
    if "ResetType" not in body:
        raise RedfishError(400, "ActionParameterMissing", ACTION, "ResetType")

    value = body["ResetType"]
    if not isinstance(value, str) or value not in RESET_RESULT:
        raise RedfishError(400, "ActionParameterValueNotInList", str(value), "ResetType", ACTION)
    return value


@router.get(f"{ROOT}/Systems")
def system_collection() -> dict[str, Any]:
    return collection(
        "ComputerSystemCollection", f"{ROOT}/Systems", "Computer System Collection", [SYSTEM]
    )

@router.get(f"{ROOT}/Systems/{{system_id}}")
def system(system_id: str, request: Request) -> dict[str, Any]:
    check_system_id(system_id)
    p = power(request)
    body = resource(
        "ComputerSystem",
        SYSTEM,
        SYSTEM_ID,
        "mini-bmc Host",
        SystemType="Physical",
        PowerState=p.state,
        Status={"State": "Enabled" if p.state == "On" else "StandbyOffline", "Health": "OK"},
        Links={"Chassis": [link(CHASSIS)]},
        Actions={
            f"#{ACTION}": {
                "target": RESET_TARGET,
                "ResetType@Redfish.AllowableValues": list(RESET_RESULT),
            }
        },
    )
    if p.last_reset is not None:
        body["LastResetTime"] = p.last_reset.isoformat(timespec="seconds")
    return body


@router.post(f"{ROOT}/Systems/{{system_id}}/Actions/{ACTION}")
async def reset(system_id: str, request: Request) -> Response:
    check_system_id(system_id)
    reset_type = parse_reset_body(await request.body())
    power(request).reset(reset_type)
    return Response(status_code=204)
