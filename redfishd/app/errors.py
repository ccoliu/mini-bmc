"""Redfish error responses (DSP0266 "Error responses"), using the DMTF Base message registry.

Every failure the API returns has the same shape:

    {"error": {"code": "Base.1.24.<Key>", "message": "...",
               "@Message.ExtendedInfo": [{"@odata.type": "#Message...", "MessageId": ..., ...}]}}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .odata import TYPES
from .sensord_client import SensordUnavailable

REGISTRY_PREFIX = "Base.1.24"

# Seconds a client should wait before retrying while sensord is unreachable.
RETRY_AFTER = 5

# The subset of Base.1.24.0 this service emits: key -> (message template, severity).
MESSAGES: dict[str, tuple[str, str]] = {
    "GeneralError": (
        "A general error has occurred.  See Resolution for information on how to resolve "
        "the error, or @Message.ExtendedInfo if Resolution is not provided.",
        "Critical",
    ),
    "InternalError": (
        "The request failed due to an internal service error.  The service is still operational.",
        "Critical",
    ),
    "ResourceNotFound": (
        "The requested resource of type '%1' named '%2' was not found.",
        "Critical",
    ),
    "OperationNotAllowed": ("The HTTP method is not allowed on this resource.", "Critical"),
    "MalformedJSON": (
        "The request body submitted was malformed JSON and could not be parsed by the "
        "receiving service.",
        "Critical",
    ),
    "NoValidSession": (
        "There is no valid session established with the implementation.",
        "Critical",
    ),
    "PropertyValueTypeError": (
        "The value '%1' for the property '%2' is not a type that the property can accept.",
        "Warning",
    ),
    "PropertyMissing": (
        "The property '%1' is a required property and must be included in the request.",
        "Warning",
    ),
    "ActionParameterUnknown": (
        "The action '%1' was submitted with the invalid parameter '%2'.",
        "Warning",
    ),
    "ActionParameterMissing": (
        "The action '%1' requires the parameter '%2' to be present in the request body.",
        "Critical",
    ),
    "ActionParameterValueNotInList": (
        "The value '%1' for the parameter '%2' in the action '%3' is not in the list of "
        "acceptable values.",
        "Warning",
    ),
    "ServiceTemporarilyUnavailable": (
        "The service is temporarily unavailable.  Retry in %1 seconds.",
        "Critical",
    ),
}


def message(key: str, *args: str) -> dict[str, Any]:
    """One entry for @Message.ExtendedInfo, with %1..%n filled in from args."""
    template, severity = MESSAGES[key]
    text = template
    for i, arg in enumerate(args, start=1):
        text = text.replace(f"%{i}", arg)
    return {
        "@odata.type": TYPES["Message"],
        "MessageId": f"{REGISTRY_PREFIX}.{key}",
        "Message": text,
        "MessageArgs": list(args),
        "MessageSeverity": severity,
    }


class RedfishError(Exception):
    """Raise from a route to return a Redfish error body with the given status."""

    def __init__(
        self, status: int, key: str, *args: str, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(key)
        self.status = status
        self.key = key
        self.args_ = args
        self.headers = headers


def error_body(key: str, *args: str) -> dict[str, Any]:
    info = message(key, *args)
    return {
        "error": {
            "code": info["MessageId"],
            "message": info["Message"],
            "@Message.ExtendedInfo": [info],
        }
    }


def error_response(
    status: int, key: str, *args: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(key, *args), status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    """Routes every failure path (ours, routing, validation, crashes) to the Redfish format."""

    @app.exception_handler(RedfishError)
    async def _redfish(_: Request, exc: RedfishError) -> JSONResponse:
        return error_response(exc.status, exc.key, *exc.args_, headers=exc.headers)

    @app.exception_handler(SensordUnavailable)
    async def _sensord_down(_: Request, exc: SensordUnavailable) -> JSONResponse:
        return error_response(
            503,
            "ServiceTemporarilyUnavailable",
            str(RETRY_AFTER),
            headers={"Retry-After": str(RETRY_AFTER)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, "ResourceNotFound", "Resource", request.url.path)
        if exc.status_code == 405:
            return error_response(405, "OperationNotAllowed", headers=exc.headers)
        return error_response(exc.status_code, "GeneralError", headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI only reaches here for bodies it could not decode; routes validate
        # their own fields so they can name the Redfish property in the error.
        return error_response(400, "MalformedJSON")

    @app.exception_handler(Exception)
    async def _crash(_: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "InternalError")
