from __future__ import annotations

import json
import time
from typing import Any

import pytest
from conftest import basic_auth
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from app.config import Settings
from app.firmware_image import MAX_IMAGE_BYTES, build_image
from app.main import create_app

SERVICE = "/redfish/v1/UpdateService"
UPLOAD = f"{SERVICE}/upload"
INVENTORY = f"{SERVICE}/FirmwareInventory"
ACTIVE = f"{INVENTORY}/BMC"
BACKUP = f"{INVENTORY}/BMC_Backup"
PAYLOAD = b"firmware" * 1000


def upload(
    client: TestClient,
    data: bytes,
    params: str = '{"Targets": []}',
    filename: str = "fw.bin",
) -> Response:
    """A DSP0266 multipart push: an UpdateParameters JSON part and an UpdateFile part."""
    return client.post(
        UPLOAD,
        files={
            "UpdateParameters": (None, params, "application/json"),
            "UpdateFile": (filename, data, "application/octet-stream"),
        },
    )


def wait_for(client: TestClient, monitor: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while (r := client.get(monitor)).status_code == 202:
        assert time.monotonic() < deadline, "task did not finish"
        time.sleep(0.01)
    assert r.status_code == 200
    return r.json()


def version(client: TestClient, path: str = ACTIVE) -> str | None:
    return client.get(path).json()["Version"]


def message_ids(task: dict[str, Any]) -> list[str]:
    return [m["MessageId"] for m in task["Messages"]]


def test_update_service(client: TestClient) -> None:
    body = client.get(SERVICE).json()
    assert body["MultipartHttpPushUri"] == UPLOAD
    assert body["FirmwareInventory"] == {"@odata.id": INVENTORY}
    assert body["MaxImageSizeBytes"] == MAX_IMAGE_BYTES


def test_initial_inventory(client: TestClient) -> None:
    members = client.get(INVENTORY).json()["Members"]
    assert members == [{"@odata.id": ACTIVE}, {"@odata.id": BACKUP}]
    active = client.get(ACTIVE).json()
    assert (active["Version"], active["Updateable"], active["Active"]) == ("1.0.0", True, True)
    backup = client.get(BACKUP).json()
    assert (backup["Version"], backup["Status"]) == (None, {"State": "Absent"})


def test_successful_update(live: TestClient) -> None:
    r = upload(live, build_image("1.1.0", PAYLOAD))
    assert r.status_code == 202
    monitor = r.headers["Location"]
    assert monitor == r.json()["TaskMonitor"]

    task = wait_for(live, monitor)
    assert (task["TaskState"], task["TaskStatus"], task["PercentComplete"]) == (
        "Completed",
        "OK",
        100,
    )
    assert "Update.1.5.UpdateSuccessful" in message_ids(task)
    assert version(live) == "1.1.0"
    backup = live.get(BACKUP).json()
    assert (backup["Version"], backup["Status"]["State"]) == ("1.0.0", "StandbySpare")


def test_failed_selftest_rolls_back(live: TestClient) -> None:
    r = upload(live, build_image("1.1.0", PAYLOAD, selftest_passes=False))
    task = wait_for(live, r.headers["Location"])
    assert (task["TaskState"], task["TaskStatus"]) == ("Exception", "Critical")
    (failed,) = [m for m in task["Messages"] if m["MessageId"] == "Update.1.5.ActivateFailed"]
    assert "1.0.0 is still running" in failed["Resolution"]
    assert version(live) == "1.0.0"


def test_update_survives_restart(live: TestClient, settings: Settings) -> None:
    wait_for(live, upload(live, build_image("1.1.0", PAYLOAD)).headers["Location"])
    restarted = TestClient(create_app(settings), headers=basic_auth("admin", "pw"))
    assert version(restarted) == "1.1.0"


@pytest.mark.parametrize(
    ("image", "reason"),
    [
        (build_image("1.1.0", PAYLOAD, sha256="0" * 64), "checksum_mismatch"),
        (build_image("1.1.0", PAYLOAD, component="bios"), "wrong_component"),
        (b"MZ not a firmware image", "bad_magic"),
    ],
)
def test_bad_image_is_rejected_before_any_task(
    client: TestClient, image: bytes, reason: str
) -> None:
    r = upload(client, image, filename="bad.bin")
    assert r.status_code == 400
    (info,) = r.json()["error"]["@Message.ExtendedInfo"]
    assert info["MessageId"] == "Update.1.5.VerificationFailed"
    assert info["MessageArgs"] == ["bad.bin", "BMC"]
    assert reason in info["Resolution"]
    assert client.get("/redfish/v1/TaskService/Tasks").json()["Members"] == []
    assert version(client) == "1.0.0"


def test_oversized_upload_is_413(client: TestClient) -> None:
    # Over the image limit but under the request limit: caught when the image is read.
    r = upload(client, b"x" * (MAX_IMAGE_BYTES + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "Base.1.24.PayloadTooLarge"
    # Over the request limit: refused from Content-Length before the body is read.
    r = upload(client, b"x" * (MAX_IMAGE_BYTES + 128 * 1024))
    assert r.status_code == 413


def test_one_update_at_a_time(live: TestClient, app: FastAPI) -> None:
    app.state.updater.reboot_seconds = 30  # keep the first update in its reboot
    assert upload(live, build_image("1.1.0", PAYLOAD)).status_code == 202
    r = upload(live, build_image("1.2.0", PAYLOAD))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "Base.1.24.ResourceInUse"
    # Leaving `live` shuts the app down, which cancels the sleeping update.


def test_explicit_bmc_target_is_accepted(live: TestClient) -> None:
    params = json.dumps({"Targets": [ACTIVE]})
    r = upload(live, build_image("1.1.0", PAYLOAD), params=params)
    assert r.status_code == 202
    wait_for(live, r.headers["Location"])


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"UpdateFile": ("fw.bin", b"x")}, "PropertyMissing"),
        ({"UpdateParameters": (None, "{}")}, "PropertyMissing"),
        ({"UpdateParameters": (None, "{}"), "UpdateFile": (None, "x")}, "PropertyMissing"),
        ({"UpdateParameters": (None, "{nope"), "UpdateFile": ("f", b"x")}, "MalformedJSON"),
        ({"UpdateParameters": (None, "[]"), "UpdateFile": ("f", b"x")}, "MalformedJSON"),
        (
            {"UpdateParameters": (None, '{"Targets": "BMC"}'), "UpdateFile": ("f", b"x")},
            "PropertyValueTypeError",
        ),
        (
            {"UpdateParameters": (None, '{"Targets": ["/x"]}'), "UpdateFile": ("f", b"x")},
            "PropertyValueNotInList",
        ),
    ],
    ids=["no-params", "no-file", "file-not-a-file", "bad-json", "not-object", "type", "target"],
)
def test_malformed_push_requests(client: TestClient, files: dict[str, Any], code: str) -> None:
    r = client.post(UPLOAD, files=files)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == f"Base.1.24.{code}"


def test_non_multipart_body_is_rejected(client: TestClient) -> None:
    r = client.post(UPLOAD, json={"Targets": []})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "Base.1.24.PropertyMissing"
