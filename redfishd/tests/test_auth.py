from __future__ import annotations

import re

import pytest
from conftest import basic_auth
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import parse_basic
from app.config import Settings

# Everything reachable without credentials. Any route not listed here must answer 401.
PUBLIC = {
    ("GET", "/redfish"),
    ("GET", "/redfish/v1"),
    ("GET", "/redfish/v1/"),
    ("POST", "/redfish/v1/SessionService/Sessions"),
}


def all_routes(app: FastAPI) -> list[tuple[str, str]]:
    """(method, concrete path) for every API route, with path parameters filled in.
    Uses the generated OpenAPI document, FastAPI's public view of its route table."""
    routes = []
    for path, operations in app.openapi()["paths"].items():
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        routes.extend((method.upper(), concrete) for method in operations)
    return routes


def test_every_route_outside_the_allowlist_requires_auth(
    app: FastAPI, anon_client: TestClient
) -> None:
    routes = all_routes(app)
    assert len(routes) > len(PUBLIC)  # sanity: the walk found the protected routes
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        r = anon_client.request(method, path)
        assert r.status_code == 401, f"{method} {path} is reachable without auth"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_framework_docs_are_not_exposed(anon_client: TestClient, path: str) -> None:
    assert anon_client.get(path).status_code == 404


def test_public_routes_need_no_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/redfish").status_code == 200
    assert anon_client.get("/redfish/v1").status_code == 200


def test_401_carries_challenge_and_redfish_error(anon_client: TestClient):
    r = anon_client.get("/redfish/v1/Chassis")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Basic realm="mini-bmc"'
    assert r.json()["error"]["code"] == "Base.1.24.NoValidSession"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(basic_auth("admin", "wrong"), id="wrong-password"),
        pytest.param(basic_auth("root", "pw"), id="wrong-user"),
        pytest.param(basic_auth("admin", ""), id="empty-password"),
        pytest.param({"Authorization": "Basic !!!notbase64"}, id="bad-base64"),
        pytest.param({"Authorization": "Basic YWRtaW4="}, id="no-colon"),
        pytest.param({"Authorization": "Bearer abc"}, id="other-scheme"),
        pytest.param({"X-Auth-Token": "made-up"}, id="unknown-token"),
    ],
)
def test_bad_credentials_are_rejected(anon_client: TestClient, headers: dict[str, str]) -> None:
    assert anon_client.get("/redfish/v1/Chassis", headers=headers).status_code == 401


def test_bad_token_does_not_fall_back_to_valid_basic(
    anon_client: TestClient, settings: Settings
) -> None:
    headers = {**basic_auth(settings.username, settings.password), "X-Auth-Token": "made-up"}
    assert anon_client.get("/redfish/v1/Chassis", headers=headers).status_code == 401


def test_parse_basic() -> None:
    assert parse_basic("Basic YWRtaW46cDpx") == ("admin", "p:q")  # colons allowed in password
    assert parse_basic("basic YWRtaW46cHc=") == ("admin", "pw")  # scheme is case-insensitive
    assert parse_basic("") is None
    assert parse_basic("Basic //79") is None  # valid base64, invalid UTF-8
