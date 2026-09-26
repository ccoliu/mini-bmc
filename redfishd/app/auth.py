"""Authentication for protected routes: X-Auth-Token first, then HTTP Basic."""

from __future__ import annotations

import base64
import binascii
import hmac

from fastapi import Request

from .config import Settings
from .errors import RedfishError

CHALLENGE = {"WWW-Authenticate": 'Basic realm="mini-bmc"'}


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    # Constant-time compares, and both always run: neither the time taken nor an
    # early return reveals whether the user name alone was right.
    user_ok = hmac.compare_digest(username.encode(), settings.username.encode())
    password_ok = hmac.compare_digest(password.encode(), settings.password.encode())
    return user_ok and password_ok


def parse_basic(header: str) -> tuple[str, str] | None:
    """Decodes 'Basic base64(user:password)'; None if absent or malformed."""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else None


def unauthorized() -> RedfishError:
    return RedfishError(401, "NoValidSession", headers=CHALLENGE)


async def require_auth(request: Request) -> str:
    """FastAPI dependency: the authenticated user name, or a 401.

    async on purpose: a plain `def` dependency runs in a worker thread, and the
    session store is not thread-safe. On the event loop, requests take turns.
    """
    token = request.headers.get("X-Auth-Token")
    if token is not None:
        # A token that was sent must be valid; never silently fall back to Basic.
        session = request.app.state.sessions.authenticate(token)
        if session is None:
            raise unauthorized()
        return session.username

    credentials = parse_basic(request.headers.get("Authorization", ""))
    if credentials is not None and check_credentials(request.app.state.settings, *credentials):
        return credentials[0]
    raise unauthorized()
