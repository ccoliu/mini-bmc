"""UpdateService: the firmware inventory and the multipart push update (DSP0266)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from ..auth import require_auth
from ..errors import RETRY_AFTER, RedfishError, error_body_for
from ..firmware_image import MAX_IMAGE_BYTES, ImageError
from ..firmware_store import UpdateInProgress, other
from ..firmware_update import COMPONENT, FirmwareUpdater, update_message
from ..odata import ROOT, collection, link, resource
from ..tasks import TaskLimitReached
from .task_service import monitor_uri, task_body

SERVICE = f"{ROOT}/UpdateService"
UPLOAD = f"{SERVICE}/upload"
INVENTORY = f"{SERVICE}/FirmwareInventory"
ACTIVE = f"{INVENTORY}/{COMPONENT}"
BACKUP = f"{INVENTORY}/{COMPONENT}_Backup"
# Room for the multipart framing and the UpdateParameters part around the image.
MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + 64 * 1024


router = APIRouter(dependencies=[Depends(require_auth)])


def updater(request: Request) -> FirmwareUpdater:
    return request.app.state.updater


@router.get(SERVICE)
async def update_service() -> dict[str, Any]:
    return resource(
        "UpdateService",
        SERVICE,
        "UpdateService",
        "Update Service",
        ServiceEnabled=True,
        MultipartHttpPushUri=UPLOAD,
        MaxImageSizeBytes=MAX_IMAGE_BYTES,
        FirmwareInventory=link(INVENTORY),
    )


@router.get(INVENTORY)
async def firmware_inventory() -> dict[str, Any]:
    return collection(
        "SoftwareInventoryCollection", INVENTORY, "Firmware Inventory", [ACTIVE, BACKUP]
    )


@router.get(ACTIVE)
async def active_firmware(request: Request) -> dict[str, Any]:
    store = updater(request).store
    return resource(
        "SoftwareInventory",
        ACTIVE,
        COMPONENT,
        "BMC Firmware",
        Version=store.active.version,
        SoftwareId="mini-bmc",
        Active=True,
        Updateable=True,
        Status={"State": "Enabled", "Health": "OK"},
    )


@router.get(BACKUP)
async def backup_firmware(request: Request) -> dict[str, Any]:
    """The other A/B slot: the previous firmware, the one on trial, or nothing yet."""
    fw = updater(request).store.state  # one snapshot: an update may be committing now
    slot = other(fw.active)
    info = fw.slots[slot]
    if info is None:
        state = "Absent"
    elif fw.trial == slot:
        state = "Updating"
    else:
        state = "StandbySpare"
    body = resource(
        "SoftwareInventory",
        BACKUP,
        f"{COMPONENT}_Backup",
        "BMC Firmware (backup slot)",
        Version=None if info is None else info.version,
        SoftwareId="mini-bmc",
        Active=False,
        Updateable=False,  # written only by an update pushed to the BMC itself
        Status={"State": state},
    )
    if info is not None:
        body["Status"]["Health"] = "OK"
    return body


async def read_upload(request: Request) -> tuple[bytes, str]:
    """The image and its file name from a DSP0266 multipart push request."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        raise RedfishError(413, "PayloadTooLarge")  # before reading the body at all
    # Small caps on the parts, so a request cannot make us parse thousands of them.
    async with request.form(max_files=1, max_fields=2) as form:
        params, file = form.get("UpdateParameters"), form.get("UpdateFile")
        if params is None:
            raise RedfishError(400, "PropertyMissing", "UpdateParameters")
        if not isinstance(file, UploadFile):
            raise RedfishError(400, "PropertyMissing", "UpdateFile")
        raw = await params.read() if isinstance(params, UploadFile) else params
        check_parameters(raw)
        # Read one byte past the limit so an oversized chunked upload is still caught.
        data = await file.read(MAX_IMAGE_BYTES + 1)
        return data, file.filename or "image"


def check_parameters(raw: str | bytes) -> None:
    try:
        params = json.loads(raw)
    except ValueError as exc:
        raise RedfishError(400, "MalformedJSON") from exc
    if not isinstance(params, dict):
        raise RedfishError(400, "MalformedJSON")
    targets = params.get("Targets", [])
    if not isinstance(targets, list):
        raise RedfishError(400, "PropertyValueTypeError", json.dumps(targets), "Targets")
    for target in targets:
        # Empty Targets means "where the image applies", which is always the BMC.
        if target != ACTIVE:
            raise RedfishError(400, "PropertyValueNotInList", json.dumps(target), "Targets")


@router.post(UPLOAD)
async def push_update(request: Request) -> JSONResponse:
    data, filename = await read_upload(request)
    try:
        task = updater(request).start(data, filename)
    except UpdateInProgress as exc:
        raise RedfishError(409, "ResourceInUse") from exc
    except TaskLimitReached as exc:
        raise RedfishError(
            503,
            "ServiceTemporarilyUnavailable",
            str(RETRY_AFTER),
            headers={"Retry-After": str(RETRY_AFTER)},
        ) from exc
    except ImageError as exc:
        if exc.reason == "too_large":
            raise RedfishError(413, "PayloadTooLarge") from exc
        info = update_message("VerificationFailed", filename, COMPONENT)
        info["Resolution"] = f"Image rejected ({exc.reason}): {exc.detail}"
        return JSONResponse(error_body_for(info), status_code=400)
    return JSONResponse(task_body(task), status_code=202, headers={"Location": monitor_uri(task)})
