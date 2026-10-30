"""sensord's too_many_clients reply must reach clients that write late (issue #6).

A turned-away connection is half-closed (SHUT_WR) and kept open for reading for a while,
so a client can still send its request and then read the reply instead of getting EPIPE.
"""

from __future__ import annotations

import asyncio
import socket
import time

from app.sensord_client import SensordClient, SensordUnavailable
from conftest import Sensord

MAX_CLIENTS = 16  # sensord/src/server.c
MAX_REJECTS = 4
LINGER_S = 1.0
BUSY = b'{"ok":false,"error":"too_many_clients"}\n'
REQUEST = b'{"cmd":"read","sensor":"cpu_temp"}\n'


def fill_client_slots(sensord: Sensord) -> None:
    for _ in range(MAX_CLIENTS):
        assert sensord.client().call(cmd="read_all")["ok"]  # accepted, not just queued


def connect_rejected(sensord: Sensord) -> socket.socket:
    """Connects once all slots are taken and reads the busy reply (so it was accepted)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(sensord.socket_path)
    assert s.recv(4096) == BUSY
    return s


def is_closed_by_sensord(s: socket.socket) -> bool:
    try:
        s.sendall(REQUEST)
    except (BrokenPipeError, ConnectionResetError):
        return True
    return False


def test_redfishd_client_sees_busy_every_time(sensord: Sensord) -> None:
    fill_client_slots(sensord)
    client = SensordClient(sensord.socket_path)

    async def attempts() -> list[str]:
        results = []
        for _ in range(20):
            try:
                await client.read("cpu_temp")
                results.append("ok")
            except SensordUnavailable as exc:
                results.append(str(exc))
        return results

    # Before the fix this was "sensord I/O failed: Connection lost" every time.
    assert asyncio.run(attempts()) == ["sensord is busy: too_many_clients"] * 20


def test_late_writer_can_still_send_and_read_the_reply(sensord: Sensord) -> None:
    fill_client_slots(sensord)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(sensord.socket_path)
        time.sleep(0.2)  # sensord has replied and half-closed by now
        s.sendall(REQUEST)
        assert s.recv(4096) == BUSY
        assert s.recv(4096) == b""  # then EOF


def test_silent_rejected_client_is_closed_after_linger(sensord: Sensord) -> None:
    fill_client_slots(sensord)
    with connect_rejected(sensord) as s:
        assert not is_closed_by_sensord(s)  # still lingering: writes are accepted
        time.sleep(LINGER_S + 0.5)
        assert is_closed_by_sensord(s)


def test_reject_slots_are_bounded(sensord: Sensord) -> None:
    fill_client_slots(sensord)
    lingering = [connect_rejected(sensord) for _ in range(MAX_REJECTS)]
    try:
        with connect_rejected(sensord) as overflow:
            time.sleep(0.1)
            # No reject slot left: replied and closed at once, so fds cannot pile up
            assert is_closed_by_sensord(overflow)
        assert not any(is_closed_by_sensord(s) for s in lingering)
    finally:
        for s in lingering:
            s.close()


def test_rejected_clients_that_hang_up_free_their_slot(sensord: Sensord) -> None:
    fill_client_slots(sensord)
    for _ in range(MAX_REJECTS * 3):
        connect_rejected(sensord).close()
    time.sleep(0.1)
    with connect_rejected(sensord) as s:
        # It got a lingering slot, so the earlier rejected clients released theirs.
        assert not is_closed_by_sensord(s)
