from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings


def test_version_document(client: TestClient) -> None:
    r = client.get("/redfish")
    assert r.status_code == 200
    assert r.json() == {"v1": "/redfish/v1/"}


def test_service_root_with_and_without_trailing_slash(client: TestClient) -> None:
    a = client.get("/redfish/v1", follow_redirects=False)
    b = client.get("/redfish/v1/", follow_redirects=False)
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_service_root_content(client: TestClient, settings: Settings) -> None:
    body = client.get("/redfish/v1/").json()
    assert body["@odata.id"] == "/redfish/v1/"
    assert body["@odata.type"].startswith("#ServiceRoot.v1_")
    assert body["UUID"] == settings.service_uuid
    for prop in ("Chassis", "Systems", "SessionService"):
        assert body[prop]["@odata.id"].startswith("/redfish/v1/")
    assert body["Links"]["Sessions"] == {"@odata.id": "/redfish/v1/SessionService/Sessions"}


def test_odata_version_header(client: TestClient) -> None:
    assert client.get("/redfish/v1/").headers["OData-Version"] == "4.0"
    assert client.get("/redfish/v1/nope").headers["OData-Version"] == "4.0"
