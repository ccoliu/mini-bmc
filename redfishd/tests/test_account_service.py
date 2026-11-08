"""AccountService: the single account and DSP0266's three predefined roles, read-only."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import basic_auth
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SERVICE = "/redfish/v1/AccountService"
ACCOUNTS = f"{SERVICE}/Accounts"
ROLES = f"{SERVICE}/Roles"
ACCOUNT = f"{ACCOUNTS}/1"

# DSP0266 "Roles": the DMTF Protocol Validator checks these exact sets.
PREDEFINED = {
    "Administrator": {
        "Login",
        "ConfigureManager",
        "ConfigureUsers",
        "ConfigureComponents",
        "ConfigureSelf",
    },
    "Operator": {"Login", "ConfigureComponents", "ConfigureSelf"},
    "ReadOnly": {"Login", "ConfigureSelf"},
}


def test_service_root_links_the_account_service(client: TestClient) -> None:
    assert client.get("/redfish/v1/").json()["AccountService"] == {"@odata.id": SERVICE}


def test_account_service(client: TestClient) -> None:
    body = client.get(SERVICE).json()
    assert body["@odata.type"].startswith("#AccountService.v1_")
    assert body["ServiceEnabled"] is True
    assert body["Accounts"] == {"@odata.id": ACCOUNTS}
    assert body["Roles"] == {"@odata.id": ROLES}


def test_the_one_account_is_the_configured_user(client: TestClient, settings: Settings) -> None:
    accounts = client.get(ACCOUNTS).json()
    assert accounts["Members"] == [{"@odata.id": ACCOUNT}]
    body = client.get(ACCOUNT).json()
    assert body["UserName"] == settings.username
    assert body["Password"] is None  # never shown; the spec requires null on read
    assert body["RoleId"] == "Administrator"
    assert body["Links"]["Role"] == {"@odata.id": f"{ROLES}/Administrator"}
    assert body["Enabled"] is True


def test_predefined_roles_have_the_spec_privileges(client: TestClient) -> None:
    members = client.get(ROLES).json()["Members"]
    assert [m["@odata.id"] for m in members] == [f"{ROLES}/{r}" for r in PREDEFINED]
    for role_id, privileges in PREDEFINED.items():
        body = client.get(f"{ROLES}/{role_id}").json()
        assert (body["Id"], body["RoleId"]) == (role_id, role_id)
        assert body["IsPredefined"] is True
        assert set(body["AssignedPrivileges"]) == privileges


@pytest.mark.parametrize(
    "path", [f"{ACCOUNTS}/2", f"{ACCOUNTS}/admin", f"{ROLES}/Root", f"{ROLES}/readonly"]
)
def test_unknown_account_or_role_is_404(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["@Message.ExtendedInfo"][0]["MessageId"].endswith("ResourceNotFound")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", ACCOUNTS),  # no new accounts
        ("PATCH", ACCOUNT),  # no password or role changes
        ("DELETE", ACCOUNT),
        ("PATCH", f"{ROLES}/ReadOnly"),  # predefined roles cannot be modified
        ("PATCH", SERVICE),
    ],
)
def test_everything_is_read_only(client: TestClient, method: str, path: str) -> None:
    r = client.request(method, path, json={"Password": "new", "AssignedPrivileges": ["Login"]})
    assert r.status_code == 405
    assert r.headers["Allow"] == "GET"


def test_account_etag_header_and_property_agree_and_are_stable(client: TestClient) -> None:
    first, second = client.get(ACCOUNT), client.get(ACCOUNT)
    etag = first.headers["ETag"]
    assert re.fullmatch(r'W/"[0-9a-f]{16}"', etag)  # an RFC 7232 weak ETag
    assert first.json()["@odata.etag"] == etag  # DSP0266: the same ETag in both places
    assert second.headers["ETag"] == etag  # unchanged content, unchanged ETag


def test_account_etag_follows_the_content(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    # Another user name is other content, so another ETag.
    other_settings = replace(settings, username="operator", firmware_dir=str(tmp_path / "fw2"))
    other = TestClient(create_app(other_settings), headers=basic_auth("operator", "pw"))
    assert other.get(ACCOUNT).json()["UserName"] == "operator"
    assert other.get(ACCOUNT).headers["ETag"] != client.get(ACCOUNT).headers["ETag"]


def test_sensors_carry_no_etag(client: TestClient) -> None:
    # Readings change every tick; an ETag there would change just as often.
    assert "ETag" not in client.get("/redfish/v1/Chassis/1/Sensors/cpu_temp").headers
