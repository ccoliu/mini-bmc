"""In-memory Redfish sessions with an idle timeout."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_TIMEOUT = 1800


@dataclass
class Session:
    id: str  # public: appears in URLs and in the session list
    token: str  # secret: what the client sends as X-Auth-Token
    username: str
    created: datetime
    last_used: float  # a clock() reading, not wall time


class SessionStore:
    def __init__(
        self, timeout: int = DEFAULT_TIMEOUT, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.timeout = timeout
        # Injectable so tests can move time forward instead of sleeping.
        self._clock = clock
        self._by_token: dict[str, Session] = {}

    def create(self, username: str) -> Session:
        session = Session(
            id=secrets.token_hex(8),
            token=secrets.token_urlsafe(32),
            username=username,
            created=datetime.now(UTC),
            last_used=self._clock(),
        )
        self._by_token[session.token] = session
        return session

    def authenticate(self, token: str) -> Session | None:
        """Returns the live session for this token and marks it as just used."""
        self._purge_expired()
        session = self._by_token.get(token)
        if session is not None:
            session.last_used = self._clock()
        return session

    def get(self, session_id: str) -> Session | None:
        self._purge_expired()
        return next((s for s in self._by_token.values() if s.id == session_id), None)

    def all(self) -> list[Session]:
        self._purge_expired()
        return list(self._by_token.values())

    def delete(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        del self._by_token[session.token]
        return True

    def _purge_expired(self) -> None:
        # Lazy expiry: no background task, stale sessions vanish on the next lookup.
        now = self._clock()
        for token in [t for t, s in self._by_token.items() if now - s.last_used > self.timeout]:
            del self._by_token[token]
