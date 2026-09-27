"""Every resource redfishd serves, validated against the official DMTF JSON schemas.

The crawler starts at the service root and follows every @odata.id, so a dangling
link fails the test and no URL is hard-coded. Runs against a real sensord.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.main import create_app
from conftest import Sensord
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

SCHEMA_DIR = Path(__file__).parent / "redfish_schemas"
SCHEMA_BASE = "http://redfish.dmtf.org/schemas/v1/"
ERROR_SCHEMA = f"{SCHEMA_BASE}redfish-error.v1_0_2.json#/definitions/RedfishError"

ROOT = "/redfish/v1/"
SESSIONS = "/redfish/v1/SessionService/Sessions"
RESET = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


@cache
def retrieve(uri: str) -> Resource:
    """Resolves a $ref to a vendored file. Never goes to the network."""
    path = SCHEMA_DIR / uri.removeprefix(SCHEMA_BASE)
    if not uri.startswith(SCHEMA_BASE) or not path.is_file():
        raise LookupError(f"{uri} is not vendored: add it to FILES in vendor.py and rerun it")
    return Resource.from_contents(json.loads(path.read_text()), default_specification=DRAFT7)


# Registry is an attrs class; pyrefly cannot see its generated __init__.
REGISTRY = Registry(retrieve=retrieve)  # pyrefly: ignore[unexpected-keyword]


def schema_ref(odata_type: str) -> str:
    """'#Chassis.v1_29_0.Chassis' -> the Chassis definition in Chassis.v1_29_0.json."""
    namespace, _, type_name = odata_type.removeprefix("#").rpartition(".")
    return f"{SCHEMA_BASE}{namespace}.json#/definitions/{type_name}"


def violations(body: Any, ref: str) -> list[str]:
    validator = Draft7Validator({"$ref": ref}, registry=REGISTRY)
    return [
        f"{'/'.join(map(str, e.absolute_path) or '<root>')}: {e.message}"
        for e in validator.iter_errors(body)
    ]


def links(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "@odata.id" and isinstance(value, str):
                yield value
            else:
                yield from links(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from links(value)


def crawl(api: TestClient) -> dict[str, Any]:
    """GETs everything reachable from the service root; returns {path: body}."""
    pages: dict[str, Any] = {}
    todo = [ROOT]
    while todo:
        path = todo.pop()
        if path in pages:
            continue
        r = api.get(path)
        assert r.status_code == 200, f"GET {path} -> {r.status_code}: dangling link?"
        body = r.json()
        assert body["@odata.id"] == path, f"{path} claims to be {body['@odata.id']}"
        pages[path] = body
        todo.extend(links(body))
    return pages


def assert_conforms(pages: dict[str, Any]) -> None:
    problems = [
        f"{path} -> {problem}"
        for path, body in pages.items()
        for problem in violations(body, schema_ref(body["@odata.type"]))
    ]
    assert not problems, "\n".join(problems)


@pytest.fixture
def api(sensord: Sensord) -> TestClient:
    settings = Settings(sensord_socket=sensord.socket_path, username="admin", password="pw")
    return TestClient(create_app(settings), headers=AUTH)


def test_crawl_covers_every_resource_type(api: TestClient) -> None:
    api.post(SESSIONS, json={"UserName": "admin", "Password": "pw"})
    types = {body["@odata.type"].split(".")[0] for body in crawl(api).values()}
    assert types == {
        "#ServiceRoot",
        "#ChassisCollection",
        "#Chassis",
        "#SensorCollection",
        "#Sensor",
        "#ComputerSystemCollection",
        "#ComputerSystem",
        "#SessionService",
        "#SessionCollection",
        "#Session",
    }


def test_healthy_service_conforms(api: TestClient) -> None:
    api.post(SESSIONS, json={"UserName": "admin", "Password": "pw"})
    assert_conforms(crawl(api))


@pytest.mark.parametrize(
    ("sensor", "mode"),
    [
        ("cpu_temp", "overtemp"),
        ("fan1_rpm", "disconnected"),
        ("psu_watts", "noise"),
        ("gpu_temp", "stuck"),
    ],
)
def test_faulted_sensors_conform(api: TestClient, sensord: Sensord, sensor: str, mode: str) -> None:
    assert sensord.client().call(cmd="inject_fault", sensor=sensor, mode=mode)["ok"]
    assert_conforms(crawl(api))


def test_powered_off_and_reset_system_conforms(api: TestClient) -> None:
    for reset_type in ("ForceOff", "On"):
        assert api.post(RESET, json={"ResetType": reset_type}).status_code == 204
        assert_conforms(crawl(api))


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("GET", "/redfish/v1/Chassis/9", None, 404),
        ("DELETE", ROOT, None, 405),
        ("POST", RESET, {"ResetType": "Explode"}, 400),
        ("POST", SESSIONS, {"UserName": "admin"}, 400),
        ("POST", SESSIONS, {"UserName": "admin", "Password": "nope"}, 401),
    ],
)
def test_error_bodies_conform(
    api: TestClient, method: str, path: str, body: dict[str, str] | None, status: int
) -> None:
    r = api.request(method, path, json=body)
    assert r.status_code == status
    assert violations(r.json(), ERROR_SCHEMA) == []


def test_unauthenticated_error_conforms(api: TestClient) -> None:
    r = api.get("/redfish/v1/Chassis", headers={"Authorization": ""})
    assert r.status_code == 401
    assert violations(r.json(), ERROR_SCHEMA) == []


def test_sensord_down_error_conforms() -> None:
    settings = Settings(sensord_socket="/nonexistent/sensord.sock", password="pw")
    r = TestClient(create_app(settings), headers=AUTH).get("/redfish/v1/Chassis/1")
    assert r.status_code == 503
    assert violations(r.json(), ERROR_SCHEMA) == []
