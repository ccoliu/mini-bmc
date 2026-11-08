"""HTTP details DSP0266 requires of every exchange, beyond the JSON bodies themselves.

- `OData-Version`: a request asking for another version is refused (412); every response
  says 4.0.
- `Cache-Control: no-cache`: readings change every tick, so nothing may be reused unchecked.
- `Allow` on GET: the methods the URI supports, from the app's own route table.
- `Link: <schema>; rel=describedby` on every resource: the DMTF schema for its @odata.type.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from .errors import error_response

ODATA_VERSION = "4.0"
SCHEMA_BASE = "http://redfish.dmtf.org/schemas/v1/"


def schema_uri(odata_type: str) -> str:
    """'#Chassis.v1_29_0.Chassis' -> the Chassis.v1_29_0.json schema."""
    namespace = odata_type.removeprefix("#").rpartition(".")[0]
    return f"{SCHEMA_BASE}{namespace}.json"


class RedfishJSONResponse(JSONResponse):
    """The app's JSON response: a resource body also names its schema in a Link header.
    Error bodies have no top-level @odata.type, so they get none."""

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        super().__init__(content, status_code, headers, media_type, background)
        if isinstance(content, dict) and isinstance(content.get("@odata.type"), str):
            self.headers["Link"] = f"<{schema_uri(content['@odata.type'])}>; rel=describedby"


def with_etag(body: dict[str, Any]) -> RedfishJSONResponse:
    """A response whose ETag header and @odata.etag property carry the same weak ETag, a
    hash of the body: unchanged content keeps the same ETag (DSP0266, RFC 7232)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    etag = f'W/"{hashlib.sha256(canonical).hexdigest()[:16]}"'
    return RedfishJSONResponse({**body, "@odata.etag": etag}, headers={"ETag": etag})


def allowed_methods(app: FastAPI) -> list[tuple[re.Pattern[str], str]]:
    """(path regex, Allow header value) per path, from the generated OpenAPI document:
    FastAPI's public view of its route table, with the methods of every route on a path
    (GET and POST on Sessions, say) already grouped together."""
    rules = []
    for path, operations in app.openapi()["paths"].items():
        literal_parts = re.split(r"\{[^}]+\}", path)  # "{task_id}" matches one segment
        regex = re.compile("[^/]+".join(map(re.escape, literal_parts)))
        rules.append((regex, ", ".join(sorted(method.upper() for method in operations))))
    return rules


def install(app: FastAPI) -> None:
    """Adds the header middleware. Call after every router is included: Allow is computed
    from the routes present at this point."""
    allow = allowed_methods(app)

    @app.middleware("http")
    async def protocol_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        version = request.headers.get("OData-Version")
        if version is not None and version != ODATA_VERSION:
            response: Response = error_response(412, "HeaderInvalid", f"OData-Version: {version}")
        else:
            response = await call_next(request)
        response.headers["OData-Version"] = ODATA_VERSION
        response.headers["Cache-Control"] = "no-cache"
        if request.method == "GET" and "Allow" not in response.headers:  # a 405 has it
            path = request.url.path
            methods = next((value for regex, value in allow if regex.fullmatch(path)), None)
            if methods is not None:
                response.headers["Allow"] = methods
        return response
