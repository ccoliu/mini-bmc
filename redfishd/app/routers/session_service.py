"""SessionService: log in (POST Sessions), log out (DELETE), and inspect sessions."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ..auth import check_credentials, require_auth
from ..errors import RedfishError
from ..odata import ROOT, collection, link, resource
from ..sessions import Session, SessionStore

SERVICE = f"{ROOT}/SessionService"
SESSIONS = f"{SERVICE}/Sessions"

# Logging in is the one write that cannot require being logged in already.
public = APIRouter()
router = APIRouter(dependencies=[Depends(require_auth)])


def store(request: Request) -> SessionStore:
    return request.app.state.sessions


def session_body(session: Session) -> dict[str, Any]:
    return resource(
        "Session",
        f"{SESSIONS}/{session.id}",
        session.id,
        "User Session",
        UserName=session.username,
        Password=None,  # the spec requires null on read; the token is never shown
        SessionType="Redfish",
        CreatedTime=session.created.isoformat(timespec="seconds"),
    )


def parse_login(raw: bytes) -> tuple[str, str]:
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise RedfishError(400, "MalformedJSON") from exc
    if not isinstance(body, dict):
        raise RedfishError(400, "MalformedJSON")
    values = []
    for prop in ("UserName", "Password"):
        if prop not in body:
            raise RedfishError(400, "PropertyMissing", prop)
        if not isinstance(body[prop], str):
            raise RedfishError(400, "PropertyValueTypeError", json.dumps(body[prop]), prop)
        values.append(body[prop])
    return values[0], values[1]


@public.post(SESSIONS)
async def login(request: Request) -> JSONResponse:
    username, password = parse_login(await request.body())
    if not check_credentials(request.app.state.settings, username, password):
        # Same answer for a wrong user name and a wrong password.
        raise RedfishError(401, "NoValidSession")
    session = store(request).create(username)
    return JSONResponse(
        session_body(session),
        status_code=201,
        headers={"X-Auth-Token": session.token, "Location": f"{SESSIONS}/{session.id}"},
    )


@router.get(SERVICE)
async def session_service(request: Request) -> dict[str, Any]:
    return resource(
        "SessionService",
        SERVICE,
        "SessionService",
        "Session Service",
        ServiceEnabled=True,
        SessionTimeout=store(request).timeout,
        Sessions=link(SESSIONS),
    )


@router.get(SESSIONS)
async def session_collection(request: Request) -> dict[str, Any]:
    members = [f"{SESSIONS}/{s.id}" for s in store(request).all()]
    return collection("SessionCollection", SESSIONS, "Session Collection", members)


@router.get(f"{SESSIONS}/{{session_id}}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    session = store(request).get(session_id)
    if session is None:
        raise RedfishError(404, "ResourceNotFound", "Session", session_id)
    return session_body(session)


@router.delete(f"{SESSIONS}/{{session_id}}")
async def delete_session(session_id: str, request: Request) -> Response:
    if not store(request).delete(session_id):
        raise RedfishError(404, "ResourceNotFound", "Session", session_id)
    return Response(status_code=204)
