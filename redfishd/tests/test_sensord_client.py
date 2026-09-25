"""SensordClient against a scriptable fake sensord, to reach failure modes the real
daemon never produces (hangs, garbage, half replies)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.sensord_client import (
    SensordClient,
    SensordRequestError,
    SensordUnavailable,
    SensorReading,
)

# A handler gets the decoded request and returns the raw bytes to send back, or None to
# send nothing (and keep the connection open until the client gives up).
Handler = Callable[[dict[str, Any]], bytes | None]


def reply(obj: Any) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def run_with_fake(
    handler: Handler, body: Callable[[SensordClient], Awaitable[Any]], timeout: float = 1.0
) -> tuple[Any, list[dict[str, Any]]]:
    """Runs body(client) against a fake sensord; returns (body's result, requests seen)."""
    seen: list[dict[str, Any]] = []
    writers: list[asyncio.StreamWriter] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        line = await reader.readline()
        req = json.loads(line)
        seen.append(req)
        out = handler(req)
        if out is None:
            await asyncio.sleep(3600)
            return
        writer.write(out)
        await writer.drain()
        writer.close()

    async def main() -> Any:
        with tempfile.TemporaryDirectory(prefix="fake-") as d:
            path = os.path.join(d, "s.sock")
            server = await asyncio.start_unix_server(serve, path)
            async with server:
                try:
                    return await body(SensordClient(path, timeout=timeout))
                finally:
                    # Newer Pythons wait for open connections on server close; a
                    # handler stuck in sleep() would otherwise hang the test.
                    for w in writers:
                        w.close()

    return asyncio.run(main()), seen


READ_ALL = {
    "ok": True,
    "sensors": [
        {"name": "cpu_temp", "value": 45.5, "unit": "C", "status": "ok"},
        {"name": "fan2_rpm", "value": None, "unit": "RPM", "status": "unavailable"},
    ],
}


def test_read_all_parses_readings() -> None:
    result, seen = run_with_fake(lambda _: reply(READ_ALL), lambda c: c.read_all())
    assert seen == [{"cmd": "read_all"}]
    assert result == [
        SensorReading("cpu_temp", 45.5, "C", "ok"),
        SensorReading("fan2_rpm", None, "RPM", "unavailable"),
    ]


def test_read_single_sensor() -> None:
    resp = {"ok": True, "sensor": {"name": "psu_watts", "value": 350, "unit": "W", "status": "ok"}}
    result, seen = run_with_fake(lambda _: reply(resp), lambda c: c.read("psu_watts"))
    assert seen == [{"cmd": "read", "sensor": "psu_watts"}]
    assert result == SensorReading("psu_watts", 350.0, "W", "ok")
    assert isinstance(result.value, float)


def test_fault_commands_send_expected_requests() -> None:
    async def body(c: SensordClient) -> None:
        await c.inject_fault("cpu_temp", "overtemp")
        await c.clear_faults()

    _, seen = run_with_fake(lambda _: reply({"ok": True}), body)
    assert seen == [
        {"cmd": "inject_fault", "sensor": "cpu_temp", "mode": "overtemp"},
        {"cmd": "clear_faults"},
    ]


def test_error_reply_raises_request_error_with_code() -> None:
    with pytest.raises(SensordRequestError) as exc:
        run_with_fake(
            lambda _: reply({"ok": False, "error": "unknown_sensor"}), lambda c: c.read("x")
        )
    assert exc.value.code == "unknown_sensor"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="closed-without-reply"),
        pytest.param(b'{"ok": true', id="partial-line"),
        pytest.param(b"not json\n", id="invalid-json"),
        pytest.param(b"[1, 2]\n", id="not-an-object"),
        pytest.param(b'{"sensors": []}\n', id="missing-ok"),
        pytest.param(b'{"ok": "yes"}\n', id="ok-not-bool"),
        pytest.param(b'{"ok": true}\n', id="read-all-without-list"),
        pytest.param(b'{"ok": true, "sensors": [{"name": "x"}]}\n', id="sensor-missing-fields"),
        pytest.param(
            b'{"ok": true, "sensors": [{"name": "x", "value": "hot", "unit": "C", '
            b'"status": "ok"}]}\n',
            id="non-numeric-value",
        ),
        pytest.param(b"x" * 100_000 + b"\n", id="oversized-line"),
    ],
)
def test_garbage_replies_mean_unavailable(raw: bytes) -> None:
    with pytest.raises(SensordUnavailable):
        run_with_fake(lambda _: raw, lambda c: c.read_all())


def test_hung_daemon_times_out() -> None:
    loop_time: list[float] = []

    async def body(c: SensordClient) -> None:
        start = asyncio.get_running_loop().time()
        try:
            await c.read_all()
        finally:
            loop_time.append(asyncio.get_running_loop().time() - start)

    with pytest.raises(SensordUnavailable, match="timed out"):
        run_with_fake(lambda _: None, body, timeout=0.2)
    assert 0.15 < loop_time[0] < 1.0


def test_missing_socket_is_unavailable() -> None:
    client = SensordClient("/nonexistent/dir/sensord.sock")
    with pytest.raises(SensordUnavailable, match="cannot connect"):
        asyncio.run(client.read_all())
