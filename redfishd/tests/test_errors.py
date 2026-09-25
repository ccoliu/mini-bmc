from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import MESSAGES, RedfishError, message
from app.main import create_app


def assert_redfish_error(body: dict, key: str) -> dict:
    err = body["error"]
    assert err["code"] == f"Base.1.24.{key}"
    (info,) = err["@Message.ExtendedInfo"]
    assert info["MessageId"] == err["code"]
    assert info["Message"] == err["message"]
    assert info["@odata.type"].startswith("#Message.v1_")
    return info


def test_message_fills_arguments() -> None:
    info = message("ResourceNotFound", "Chassis", "42")
    assert info["Message"] == "The requested resource of type 'Chassis' named '42' was not found."
    assert info["MessageArgs"] == ["Chassis", "42"]
    assert info["MessageSeverity"] == "Critical"


def test_every_template_uses_only_supplied_placeholders() -> None:
    for key, (template, _) in MESSAGES.items():
        n = template.count("%")
        filled = message(key, *(["x"] * n))["Message"]
        assert "%" not in filled, key


def test_unknown_url_is_resource_not_found(client: TestClient) -> None:
    r = client.get("/redfish/v1/Bogus")
    assert r.status_code == 404
    info = assert_redfish_error(r.json(), "ResourceNotFound")
    assert info["MessageArgs"] == ["Resource", "/redfish/v1/Bogus"]


def test_wrong_method_is_operation_not_allowed(client: TestClient) -> None:
    r = client.delete("/redfish/v1/")
    assert r.status_code == 405
    assert "GET" in r.headers["Allow"]
    assert_redfish_error(r.json(), "OperationNotAllowed")


def test_raised_redfish_error_and_unexpected_crash(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/test/raise")
    def _raise() -> None:
        raise RedfishError(503, "ServiceTemporarilyUnavailable", "5")

    @app.get("/test/crash")
    def _crash() -> None:
        raise ZeroDivisionError

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/test/raise")
    assert r.status_code == 503
    assert assert_redfish_error(r.json(), "ServiceTemporarilyUnavailable")["MessageArgs"] == ["5"]

    r = client.get("/test/crash")
    assert r.status_code == 500
    assert_redfish_error(r.json(), "InternalError")
