from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sessions import SessionStore

SESSIONS = "/redfish/v1/SessionService/Sessions"


def login(anon_client: TestClient, username: str = "admin", password: str = "pw") -> Any:
    return anon_client.post(SESSIONS, json={"UserName": username, "Password": password})


def test_login_returns_token_location_and_session(anon_client: TestClient) -> None:
    r = login(anon_client)
    assert r.status_code == 201
    token = r.headers["X-Auth-Token"]
    body = r.json()
    assert r.headers["Location"] == body["@odata.id"] == f"{SESSIONS}/{body['Id']}"
    assert body["UserName"] == "admin"
    assert body["Password"] is None
    assert token not in r.text  # the secret travels only in the header
    assert body["Id"] not in token  # the public Id is not derived from the secret


def test_token_grants_access(anon_client: TestClient) -> None:
    token = login(anon_client).headers["X-Auth-Token"]
    r = anon_client.get("/redfish/v1/Chassis/1", headers={"X-Auth-Token": token})
    assert r.status_code == 200


def test_each_login_gets_a_fresh_token(anon_client: TestClient) -> None:
    a = login(anon_client).headers["X-Auth-Token"]
    b = login(anon_client).headers["X-Auth-Token"]
    assert a != b
    assert len(a) >= 40  # 32 random bytes, base64url-encoded


@pytest.mark.parametrize(("username", "password"), [("admin", "nope"), ("nobody", "pw")])
def test_failed_login_creates_nothing_and_does_not_say_why(
    anon_client: TestClient, client: TestClient, username: str, password: str
) -> None:
    r = login(anon_client, username, password)
    assert r.status_code == 401
    assert "X-Auth-Token" not in r.headers
    assert r.json()["error"]["code"] == "Base.1.24.NoValidSession"
    assert client.get(SESSIONS).json()["Members"] == []


@pytest.mark.parametrize(
    ("body", "key", "args"),
    [
        (b"nope", "MalformedJSON", []),
        (b"[]", "MalformedJSON", []),
        (b'{"Password": "pw"}', "PropertyMissing", ["UserName"]),
        (b'{"UserName": "admin"}', "PropertyMissing", ["Password"]),
        (b'{"UserName": "admin", "Password": 123}', "PropertyValueTypeError", ["123", "Password"]),
    ],
)
def test_login_body_validation(
    anon_client: TestClient, body: bytes, key: str, args: list[str]
) -> None:
    r = anon_client.post(SESSIONS, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    (info,) = r.json()["error"]["@Message.ExtendedInfo"]
    assert info["MessageId"] == f"Base.1.24.{key}"
    assert info["MessageArgs"] == args

def test_session_service_and_collection(anon_client: TestClient, client: TestClient) -> None:
    service = client.get("/redfish/v1/SessionService").json()
    assert service["SessionTimeout"] == 1800
    assert service["ServiceEnabled"] is True
    assert service["Sessions"] == {"@odata.id": SESSIONS}

    created = login(anon_client)
    members = client.get(SESSIONS).json()["Members"]
    assert members == [{"@odata.id": created.headers["Location"]}]
    assert client.get(created.headers["Location"]).json()["UserName"] == "admin"


def test_logout_revokes_the_token(anon_client: TestClient) -> None:
    r = login(anon_client)
    auth = {"X-Auth-Token": r.headers["X-Auth-Token"]}
    location = r.headers["Location"]

    assert anon_client.delete(location, headers=auth).status_code == 204
    assert anon_client.get("/redfish/v1/Chassis", headers=auth).status_code == 401


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.get(f"{SESSIONS}/nope").status_code == 404
    assert client.delete(f"{SESSIONS}/nope").status_code == 404


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_idle_session_expires_and_activity_keeps_it_alive(
    app: FastAPI, anon_client: TestClient
) -> None:
    clock = FakeClock()
    app.state.sessions = SessionStore(timeout=60, clock=clock)
    auth = {"X-Auth-Token": login(anon_client).headers["X-Auth-Token"]}

    for _ in range(3):  # 150 s in total, but never 60 s idle
        clock.now += 50
        assert anon_client.get("/redfish/v1/Chassis", headers=auth).status_code == 200

    clock.now += 61
    assert anon_client.get("/redfish/v1/Chassis", headers=auth).status_code == 401
    assert app.state.sessions.all() == []
