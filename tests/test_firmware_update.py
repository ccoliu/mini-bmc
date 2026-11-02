"""A firmware update running alongside normal BMC work, against a real sensord."""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.config import Settings
from app.firmware_image import build_image
from app.main import create_app
from conftest import Sensord
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
UPLOAD = "/redfish/v1/UpdateService/upload"
ACTIVE = "/redfish/v1/UpdateService/FirmwareInventory/BMC"
BACKUP = "/redfish/v1/UpdateService/FirmwareInventory/BMC_Backup"
SENSOR = "/redfish/v1/Chassis/1/Sensors/cpu_temp"
REBOOT_SECONDS = 1.0


@pytest.fixture
def api(sensord: Sensord, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        sensord_socket=sensord.socket_path,
        password="pw",
        firmware_dir=str(tmp_path / "firmware"),
        reboot_seconds=REBOOT_SECONDS,
    )
    with TestClient(create_app(settings), headers=AUTH) as client:
        yield client


def test_bmc_keeps_serving_during_an_update(api: TestClient) -> None:
    files = {"UpdateParameters": (None, "{}"), "UpdateFile": ("fw.bin", build_image("1.1.0", b"x"))}
    r = api.post(UPLOAD, files=files)
    assert r.status_code == 202
    monitor = r.headers["Location"]

    reads_during_update = 0
    deadline = time.monotonic() + REBOOT_SECONDS + 5
    while api.get(monitor).status_code == 202:
        assert time.monotonic() < deadline, "update did not finish"
        start = time.monotonic()
        sensor = api.get(SENSOR)
        assert sensor.status_code == 200
        assert sensor.json()["Reading"] is not None
        # Well under the reboot time: the update is not holding up other requests.
        assert time.monotonic() - start < REBOOT_SECONDS / 2

        backup = api.get(BACKUP).json()
        if backup["Status"]["State"] == "Updating":  # staged, in its trial boot
            assert backup["Version"] == "1.1.0"
            assert api.get(ACTIVE).json()["Version"] == "1.0.0"
            reads_during_update += 1
        time.sleep(0.05)

    assert reads_during_update > 0, "never observed the update is in progress"
    assert api.get(ACTIVE).json()["Version"] == "1.1.0"
