"""/redfish and /redfish/v1/ — the only resources readable without authentication."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..odata import REDFISH_VERSION, ROOT, TYPES, link

router = APIRouter()


@router.get("/redfish")
def versions() -> dict[str, str]:
    return {"v1": f"{ROOT}/"}


@router.get(ROOT)
@router.get(f"{ROOT}/")
def service_root(request: Request) -> dict[str, Any]:
    return {
        "@odata.id": f"{ROOT}/",
        "@odata.type": TYPES["ServiceRoot"],
        "Id": "RootService",
        "Name": "mini-bmc Root Service",
        "RedfishVersion": REDFISH_VERSION,
        "UUID": request.app.state.settings.service_uuid,
        "Chassis": link(f"{ROOT}/Chassis"),
        "Systems": link(f"{ROOT}/Systems"),
        "SessionService": link(f"{ROOT}/SessionService"),
        "Links": {"Sessions": link(f"{ROOT}/SessionService/Sessions")},
    }
