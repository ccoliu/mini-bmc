"""/redfish, /redfish/v1/ and the OData documents beside it — the only resources readable
without authentication (DSP0266 requires $metadata and the service document to be public)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from ..odata import REDFISH_VERSION, ROOT, TYPES, link, metadata_xml

router = APIRouter()

# ServiceRoot property -> path of each top-level resource; the service document lists the same.
TOP_LEVEL = {
    "Chassis": f"{ROOT}/Chassis",
    "Systems": f"{ROOT}/Systems",
    "SessionService": f"{ROOT}/SessionService",
    "Tasks": f"{ROOT}/TaskService",
    "UpdateService": f"{ROOT}/UpdateService",
}

METADATA = metadata_xml()  # built from TYPES, so it cannot drift from what is served


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
        **{prop: link(path) for prop, path in TOP_LEVEL.items()},
        "Links": {"Sessions": link(f"{ROOT}/SessionService/Sessions")},
    }


@router.get(f"{ROOT}/$metadata")
def metadata() -> Response:
    return Response(METADATA, media_type="application/xml")


@router.get(f"{ROOT}/odata")
def service_document() -> dict[str, Any]:
    urls = {"Service": f"{ROOT}/", **TOP_LEVEL}
    entries = [{"name": name, "kind": "Singleton", "url": url} for name, url in urls.items()]
    return {"@odata.context": f"{ROOT}/$metadata", "value": entries}
