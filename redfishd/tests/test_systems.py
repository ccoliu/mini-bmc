from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

SYSTEM = "/redfish/v1/Systems/1"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"


def error_info(r: Any) -> dict[str, Any]:
    """The single @Message.ExtendedInfo entry of a Redfish error response."""
    (info,) = r.json()["error"]["@Message.ExtendedInfo"]
    return info


def test_system_collection(client: TestClient) -> None:
    body = client.get("/redfish/v1/Systems").json()
    assert body["Members"] == [{"@odata.id": SYSTEM}]
    assert body["Members@odata.count"] == 1


def test_system_defaults(client: TestClient) -> None:
    body = client.get(SYSTEM).json()
    assert body["@odata.type"].startswith("#ComputerSystem.v1_")
    assert body["PowerState"] == "On"
    assert body["Status"] == {"State": "Enabled", "Health": "OK"}
    assert body["Links"] == {"Chassis": [{"@odata.id": "/redfish/v1/Chassis/1"}]}
    assert "LastResetTime" not in body
    action = body["Actions"]["#ComputerSystem.Reset"]
    assert action["target"] == RESET
    assert "ForceOff" in action["ResetType@Redfish.AllowableValues"]


def test_chassis_and_system_link_to_each_other(client: TestClient) -> None:
    chassis = client.get("/redfish/v1/Chassis/1").json()
    (system_link,) = chassis["Links"]["ComputerSystems"]
    system = client.get(system_link["@odata.id"]).json()
    assert system["Links"]["Chassis"] == [{"@odata.id": chassis["@odata.id"]}]


@pytest.mark.parametrize(
    ("reset_type", "power_state", "status_state"),
    [
        ("ForceOff", "Off", "StandbyOffline"),
        ("GracefulShutdown", "Off", "StandbyOffline"),
        ("GracefulRestart", "On", "Enabled"),
        ("ForceRestart", "On", "Enabled"),
        ("PowerCycle", "On", "Enabled"),
        ("On", "On", "Enabled"),
        ("ForceOn", "On", "Enabled"),
    ],
)
def test_reset_types(
    client: TestClient, reset_type: str, power_state: str, status_state: str
) -> None:
    r = client.post(RESET, json={"ResetType": reset_type})
    assert r.status_code == 204
    assert r.content == b""
    body = client.get(SYSTEM).json()
    assert body["PowerState"] == power_state
    assert body["Status"]["State"] == status_state


def test_every_allowable_value_is_accepted(client: TestClient) -> None:
    allowed = client.get(SYSTEM).json()["Actions"]["#ComputerSystem.Reset"][
        "ResetType@Redfish.AllowableValues"
    ]

    for reset_type in allowed:
        assert client.post(RESET, json={"ResetType": reset_type}).status_code == 204


def test_last_reset_time_tracks_coming_out_of_reset(client: TestClient) -> None:
    client.post(RESET, json={"ResetType": "ForceOff"})
    assert "LastResetTime" not in client.get(SYSTEM).json()  # powering off is not a reset

    client.post(RESET, json={"ResetType": "On"})
    first = client.get(SYSTEM).json()["LastResetTime"]
    assert first.endswith("+00:00")

    client.post(RESET, json={"ResetType": "On"})
    assert client.get(SYSTEM).json()["LastResetTime"] == first


@pytest.mark.parametrize(
    ("body", "key", "args"),
    [
        (b"not json", "MalformedJSON", []),
        (b'["ForceOff"]', "MalformedJSON", []),
        (b"", "ActionParameterMissing", ["ComputerSystem.Reset", "ResetType"]),
        (b"{}", "ActionParameterMissing", ["ComputerSystem.Reset", "ResetType"]),
        (
            b'{"ResetType": "Explode"}',
            "ActionParameterValueNotInList",
            ["Explode", "ResetType", "ComputerSystem.Reset"],
        ),
        (
            b'{"ResetType": 5}',
            "ActionParameterValueNotInList",
            ["5", "ResetType", "ComputerSystem.Reset"],
        ),
        (
            b'{"ResetType": "Nmi"}',
            "ActionParameterValueNotInList",
            ["Nmi", "ResetType", "ComputerSystem.Reset"],
        ),
        (
            b'{"ResetType": "ForceOff", "Force": true}',
            "ActionParameterUnknown",
            ["ComputerSystem.Reset", "Force"],
        ),
    ],
)
def test_bad_reset_bodies_are_rejected_without_side_effects(
    client: TestClient, body: bytes, key: str, args: list[str]
) -> None:
    r = client.post(RESET, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    info = error_info(r)
    assert info["MessageId"] == f"Base.1.24.{key}"
    assert info["MessageArgs"] == args
    assert client.get(SYSTEM).json()["PowerState"] == "On"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/redfish/v1/Systems/2"),
        ("POST", "/redfish/v1/Systems/2/Actions/ComputerSystem.Reset"),
    ],
)
def test_unknown_system(client: TestClient, method: str, path: str) -> None:
    r = client.request(method, path, json={"ResetType": "ForceOff"})
    assert r.status_code == 404
    assert error_info(r)["MessageArgs"] == ["ComputerSystem", "2"]


def test_action_target_only_accepts_post(client: TestClient) -> None:
    assert client.get(RESET).status_code == 405
