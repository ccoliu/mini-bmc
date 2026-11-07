"""HTTP headers DSP0266 requires: OData-Version, Cache-Control, Allow, Link."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.odata import TYPES
from app.protocol import schema_uri

ROOT = "/redfish/v1/"
SESSIONS = "/redfish/v1/SessionService/Sessions"


@pytest.mark.parametrize("path", [ROOT, "/redfish/v1/$metadata", "/redfish/v1/nope", SESSIONS])
def test_every_response_has_odata_version_and_cache_control(
    anon_client: TestClient, path: str
) -> None:
    # Public, protected (401 here), missing (404), and non-JSON ($metadata) alike.
    r = anon_client.get(path)
    assert r.headers["OData-Version"] == "4.0"
    assert r.headers["Cache-Control"] == "no-cache"


def test_supported_odata_version_is_accepted(client: TestClient) -> None:
    assert client.get(ROOT, headers={"OData-Version": "4.0"}).status_code == 200


@pytest.mark.parametrize("version", ["4.1", "3.0", "4", ""])
def test_other_odata_versions_are_412(anon_client: TestClient, version: str) -> None:
    r = anon_client.get(ROOT, headers={"OData-Version": version})
    assert r.status_code == 412
    info = r.json()["error"]["@Message.ExtendedInfo"][0]
    assert info["MessageId"] == "Base.1.24.HeaderInvalid"
    assert info["MessageArgs"] == [f"OData-Version: {version}"]
    assert r.headers["OData-Version"] == "4.0"


@pytest.mark.parametrize(
    ("path", "allow"),
    [
        (ROOT, "GET"),
        (SESSIONS, "GET, POST"),
        (f"{SESSIONS}/abc", "DELETE, GET"),
        ("/redfish/v1/Chassis/1/Sensors/cpu_temp", "GET"),
        ("/redfish/v1/TaskService/Tasks/1/Monitor", "GET"),
    ],
)
def test_get_responses_list_the_allowed_methods(client: TestClient, path: str, allow: str) -> None:
    # Whatever the outcome (an unknown session is a 404): the URI supports these methods.
    assert client.get(path).headers["Allow"] == allow


def test_405_keeps_its_own_allow(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems/1/Actions/ComputerSystem.Reset")
    assert r.status_code == 405
    assert r.headers["Allow"] == "POST"


def test_no_allow_for_a_uri_that_does_not_exist(client: TestClient) -> None:
    assert "Allow" not in client.get("/redfish/v1/nope").headers


@pytest.mark.parametrize(
    ("odata_type", "uri"),
    [
        ("#Chassis.v1_29_0.Chassis", "http://redfish.dmtf.org/schemas/v1/Chassis.v1_29_0.json"),
        (
            "#SensorCollection.SensorCollection",
            "http://redfish.dmtf.org/schemas/v1/SensorCollection.json",
        ),
    ],
)
def test_schema_uri(odata_type: str, uri: str) -> None:
    assert schema_uri(odata_type) == uri


@pytest.mark.parametrize(
    "path",
    [
        ROOT,
        "/redfish/v1/Chassis/1",
        "/redfish/v1/Chassis/1/Sensors",
        "/redfish/v1/Chassis/1/Sensors/cpu_temp",
        "/redfish/v1/Systems/1",
        SESSIONS,
        "/redfish/v1/UpdateService/FirmwareInventory/BMC",
    ],
)
def test_resources_link_to_their_schema(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    # The same version as @odata.type: the Protocol Validator checks that they match.
    expected = schema_uri(r.json()["@odata.type"])
    assert r.headers["Link"] == f"<{expected}>; rel=describedby"


def test_link_on_responses_built_by_hand(client: TestClient) -> None:
    # Routes that build their response object themselves (login here) get it too.
    r = client.post(SESSIONS, json={"UserName": "admin", "Password": "pw"})
    assert r.status_code == 201
    assert r.headers["Link"] == f"<{schema_uri(TYPES['Session'])}>; rel=describedby"


def test_errors_have_no_link(client: TestClient) -> None:
    r = client.get("/redfish/v1/Chassis/9")
    assert r.status_code == 404
    assert "Link" not in r.headers


def test_login_through_the_members_property(client: TestClient) -> None:
    # DSP0266: a POST to a collection's Members property is the same as one to the collection.
    r = client.post(f"{SESSIONS}/Members", json={"UserName": "admin", "Password": "pw"})
    assert r.status_code == 201
    location = r.headers["Location"]
    assert location.startswith(f"{SESSIONS}/")
    token = {"X-Auth-Token": r.headers["X-Auth-Token"]}
    assert client.get(location, headers={**token, "Authorization": ""}).status_code == 200
