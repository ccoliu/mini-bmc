"""$metadata (CSDL) and the OData service document."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.odata import TYPES

EDMX = "{http://docs.oasis-open.org/odata/ns/edmx}"
EDM = "{http://docs.oasis-open.org/odata/ns/edm}"


def test_metadata_is_public_xml(anon_client: TestClient) -> None:
    r = anon_client.get("/redfish/v1/$metadata")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert r.headers["OData-Version"] == "4.0"
    assert ET.fromstring(r.content).tag == f"{EDMX}Edmx"


def test_metadata_references_every_served_type(anon_client: TestClient) -> None:
    root = ET.fromstring(anon_client.get("/redfish/v1/$metadata").content)
    included = {
        inc.get("Namespace"): ref.get("Uri")
        for ref in root.iter(f"{EDMX}Reference")
        for inc in ref.iter(f"{EDMX}Include")
    }
    for odata_type in TYPES.values():
        namespace = odata_type[1:].rpartition(".")[0]  # "Chassis.v1_29_0"
        base = namespace.split(".")[0]
        uri = f"http://redfish.dmtf.org/schemas/v1/{base}_v1.xml"
        assert included[namespace] == uri
        assert included[base] == uri


def test_metadata_container_extends_the_service_root_version(anon_client: TestClient) -> None:
    root = ET.fromstring(anon_client.get("/redfish/v1/$metadata").content)
    (schema,) = root.iter(f"{EDM}Schema")
    assert schema.get("Namespace") == "Service"
    (container,) = schema.iter(f"{EDM}EntityContainer")
    assert container.get("Extends") == "ServiceRoot.v1_22_0.ServiceContainer"
    assert TYPES["ServiceRoot"] == "#ServiceRoot.v1_22_0.ServiceRoot"


def test_service_document_lists_the_service_root_links(
    anon_client: TestClient, client: TestClient
) -> None:
    r = anon_client.get("/redfish/v1/odata")
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.context"] == "/redfish/v1/$metadata"
    root = client.get("/redfish/v1/").json()
    expected = {"Service": "/redfish/v1/"} | {
        prop: value["@odata.id"]
        for prop, value in root.items()
        if isinstance(value, dict) and "@odata.id" in value
    }
    assert {e["name"]: e["url"] for e in body["value"]} == expected
    assert {e["kind"] for e in body["value"]} == {"Singleton"}
    for entry in body["value"]:
        assert client.get(entry["url"]).status_code == 200
