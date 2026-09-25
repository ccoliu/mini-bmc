"""Async client for the sensord Unix socket protocol (see CLAUDE.md, "sensord / Interface").

One connection per request: connecting to a local Unix socket is cheap, and it means a
sensord restart needs no reconnect logic here. The whole exchange (connect, write, read)
shares a single timeout so a hung daemon cannot hang the API.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

# sensord's longest response (read_all) is well under this.
MAX_RESPONSE = 64 * 1024


class SensordError(Exception):
    """Base class for everything this client raises."""


class SensordUnavailable(SensordError):
    """sensord could not be reached, timed out, or answered with something unparseable."""


class SensordRequestError(SensordError):
    """sensord answered {"ok": false, "error": code}."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SensorReading:
    name: str
    value: float | None  # None when the sensor is unavailable
    unit: str
    status: str  # ok | warning | critical | unavailable

    @classmethod
    def from_wire(cls, obj: Any) -> SensorReading:
        try:
            value = obj["value"]
            if value is not None:
                value = float(value)
            return cls(
                name=str(obj["name"]), value=value, unit=str(obj["unit"]), status=str(obj["status"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SensordUnavailable(f"malformed sensor object: {obj!r}") from exc


class SensordClient:
    def __init__(self, socket_path: str, timeout: float = 2.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    async def request(self, **fields: str) -> dict[str, Any]:
        """Sends one request and returns the decoded response if it has "ok": true."""
        try:
            async with asyncio.timeout(self.timeout):
                return self._check(await self._exchange(fields))
        except TimeoutError as exc:
            raise SensordUnavailable(f"sensord timed out after {self.timeout}s") from exc

    async def _exchange(self, fields: dict[str, str]) -> Any:
        try:
            reader, writer = await asyncio.open_unix_connection(
                self.socket_path, limit=MAX_RESPONSE
            )
        except OSError as exc:
            raise SensordUnavailable(f"cannot connect to {self.socket_path}: {exc}") from exc
        try:
            writer.write(json.dumps(fields).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        except (OSError, ValueError) as exc:  # ValueError: line longer than limit
            raise SensordUnavailable(f"sensord I/O failed: {exc}") from exc
        finally:
            writer.close()
        if not line.endswith(b"\n"):
            raise SensordUnavailable("sensord closed the connection without a reply")
        try:
            return json.loads(line)
        except ValueError as exc:
            raise SensordUnavailable(f"sensord sent invalid JSON: {line[:200]!r}") from exc

    @staticmethod
    def _check(resp: Any) -> dict[str, Any]:
        if not isinstance(resp, dict) or not isinstance(resp.get("ok"), bool):
            raise SensordUnavailable(f"unexpected response shape: {resp!r}")
        if not resp["ok"]:
            raise SensordRequestError(str(resp.get("error", "unknown")))
        return resp

    async def read_all(self) -> list[SensorReading]:
        resp = await self.request(cmd="read_all")
        sensors = resp.get("sensors")
        if not isinstance(sensors, list):
            raise SensordUnavailable(f"read_all without a sensor list: {resp!r}")
        return [SensorReading.from_wire(s) for s in sensors]

    async def read(self, name: str) -> SensorReading:
        resp = await self.request(cmd="read", sensor=name)
        return SensorReading.from_wire(resp.get("sensor"))

    async def inject_fault(self, name: str, mode: str) -> None:
        await self.request(cmd="inject_fault", sensor=name, mode=mode)

    async def clear_faults(self) -> None:
        await self.request(cmd="clear_faults")
