"""Query parameters: unknown ones ignored, unsupported $ ones refused, $expand on Sensors."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeSensord
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from test_auth import all_routes

from app.query import EXPANDABLE, PROTOCOL_FEATURES
from app.sensor_catalog import CATALOG

SENSORS = "/redfish/v1/Chassis/1/Sensors"


def error_info(r: Response) -> dict[str, Any]:
    return r.json()["error"]["@Message.ExtendedInfo"][0]


def test_unknown_parameters_without_dollar_are_ignored(client: TestClient) -> None:
    assert client.get("/redfish/v1/?foo=1").status_code == 200
    assert client.get(f"{SENSORS}?only&expand=.").json() == client.get(SENSORS).json()


@pytest.mark.parametrize(
    "param", ["$top=1", "$skip=1", "$filter=Id eq 'x'", "$select=Id", "$Expand=."]
)
@pytest.mark.parametrize("path", ["/redfish/v1/", SENSORS, f"{SENSORS}/cpu_temp"])
def test_unsupported_dollar_parameters_are_501(client: TestClient, path: str, param: str) -> None:
    r = client.get(f"{path}?{param}")
    assert r.status_code == 501
    info = error_info(r)
    assert info["MessageId"] == "Base.1.24.QueryParameterUnsupported"
    assert info["MessageArgs"] == [param.split("=")[0]]  # names the parameter


def test_expand_is_refused_on_every_other_route(app: FastAPI, client: TestClient) -> None:
    expandable = {path.replace("{chassis_id}", "1") for path in EXPANDABLE}
    routes = [(m, p) for m, p in all_routes(app) if p not in expandable]
    assert len(routes) > 10  # sanity: the walk found the routes
    for method, path in routes:
        r = client.request(method, f"{path}?$expand=.")
        assert r.status_code == 400, f"{method} {path} accepted $expand"
        assert error_info(r)["MessageId"] == "Base.1.24.QueryNotSupportedOnResource"


def test_every_expandable_route_exists(app: FastAPI) -> None:
    assert set(app.openapi()["paths"]) >= EXPANDABLE


def test_expand_reads_sensord_once(client: TestClient, fake_sensord: FakeSensord) -> None:
    assert client.get(f"{SENSORS}?$expand=.").status_code == 200
    assert fake_sensord.calls == ["read_all"]  # not one read per member


def test_expanded_member_missing_from_sensord_is_left_out(
    client: TestClient, fake_sensord: FakeSensord
) -> None:
    del fake_sensord.readings["gpu_temp"]
    body = client.get(f"{SENSORS}?$expand=.").json()
    ids = [m["Id"] for m in body["Members"]]
    assert "gpu_temp" not in ids
    assert body["Members@odata.count"] == len(ids) == len(CATALOG) - 1


@pytest.mark.parametrize("value", ["*", ".", "*($levels=1)", ".($levels=1)"])
def test_expanded_members_are_the_sensor_resources(client: TestClient, value: str) -> None:
    plain = client.get(SENSORS).json()
    r = client.get(SENSORS, params={"$expand": value})
    assert r.status_code == 200
    body = r.json()
    # Same collection, same order; each link replaced by exactly what a GET on it returns.
    assert [m["@odata.id"] for m in body["Members"]] == [m["@odata.id"] for m in plain["Members"]]
    assert body["Members"] == [client.get(m["@odata.id"]).json() for m in plain["Members"]]
    assert body["Members@odata.count"] == len(CATALOG)
    assert {k: v for k, v in body.items() if k != "Members"} == {
        k: v for k, v in plain.items() if k != "Members"
    }


def test_expanded_collection_needs_sensord(client: TestClient, fake_sensord: FakeSensord) -> None:
    fake_sensord.down = True
    assert client.get(SENSORS).status_code == 200  # plain links still need no sensord
    r = client.get(f"{SENSORS}?$expand=.")
    assert r.status_code == 503
    assert "Retry-After" in r.headers


UNSUPPORTED, RANGE, FORMAT = (
    "QueryParameterUnsupported",
    "QueryParameterOutOfRange",
    "QueryParameterValueFormatError",
)


@pytest.mark.parametrize(
    ("query", "status", "message_id", "args"),
    [
        # Valid Redfish we do not implement: 501. Malformed or out of range: 400.
        ("$expand=~", 501, UNSUPPORTED, ["$expand=~"]),
        ("$expand=~($levels=1)", 501, UNSUPPORTED, ["$expand=~($levels=1)"]),
        ("$expand=.($levels=2)", 400, RANGE, ["2", "$levels", "1 to 1"]),
        ("$expand=.($levels=0)", 400, RANGE, ["0", "$levels", "1 to 1"]),
        ("$expand=all", 400, FORMAT, ["all", "$expand"]),
        ("$expand=", 400, FORMAT, ["", "$expand"]),
        ("$expand=.($levels=x)", 400, FORMAT, [".($levels=x)", "$expand"]),
        ("$expand=.&$expand=*", 400, FORMAT, [".,*", "$expand"]),
    ],
)
def test_bad_expand_values(
    client: TestClient, query: str, status: int, message_id: str, args: list[str]
) -> None:
    r = client.get(f"{SENSORS}?{query}")
    assert r.status_code == status
    info = error_info(r)
    assert info["MessageId"] == f"Base.1.24.{message_id}"
    assert info["MessageArgs"] == args


def test_service_root_advertises_what_is_implemented(anon_client: TestClient) -> None:
    features = anon_client.get("/redfish/v1/").json()["ProtocolFeaturesSupported"]
    assert features == PROTOCOL_FEATURES
    assert features["ExpandQuery"] == {
        "ExpandAll": True,
        "NoLinks": True,
        "Links": False,
        "Levels": True,
        "MaxLevels": 1,
    }
    assert not any(v for k, v in features.items() if k != "ExpandQuery")
