"""Socket-level tests for sensord: protocol, robustness, and lifecycle."""

from __future__ import annotations

import os
import socket
import time

import pytest
from conftest import SensordClient, start_sensord

SENSORS = {
    "cpu_temp": "C",
    "gpu_temp": "C",
    "inlet_temp": "C",
    "fan1_rpm": "RPM",
    "fan2_rpm": "RPM",
    "psu_watts": "W",
}
MAX_LINE = 1023


def test_read_all_returns_every_sensor(client: SensordClient) -> None:
    resp = client.call(cmd="read_all")
    assert resp["ok"] is True
    assert {s["name"]: s["unit"] for s in resp["sensors"]} == SENSORS
    for s in resp["sensors"]:
        assert isinstance(s["value"], float)
        assert s["status"] == "ok"


def test_read_single_sensor(client: SensordClient) -> None:
    resp = client.call(cmd="read", sensor="psu_watts")
    assert resp["ok"] is True
    assert resp["sensor"]["name"] == "psu_watts"
    assert resp["sensor"]["unit"] == "W"


def test_values_change_over_time(client: SensordClient) -> None:
    first = client.call(cmd="read", sensor="fan1_rpm")["sensor"]["value"]
    for _ in range(50):
        time.sleep(0.02)
        if client.call(cmd="read", sensor="fan1_rpm")["sensor"]["value"] != first:
            return
    pytest.fail("fan1_rpm never changed; the simulation is not ticking")


@pytest.mark.parametrize(
    ("request_", "error"),
    [
        ({"cmd": "reboot"}, "unknown_cmd"),
        ({"cmd": "read"}, "missing_field"),
        ({"cmd": "read", "sensor": "cpu"}, "unknown_sensor"),
        ({"cmd": "inject_fault", "sensor": "cpu_temp", "mode": "melt"}, "unknown_mode"),
        (
            {"cmd": "inject_fault", "sensor": "fan1_rpm", "mode": "overtemp"},
            "invalid_fault_for_sensor",
        ),
    ],
)
def test_error_responses(client: SensordClient, request_: dict, error: str) -> None:
    assert client.call(**request_) == {"ok": False, "error": error}


@pytest.mark.parametrize(
    "payload",
    [
        b"\n",
        b"garbage\n",
        b'{"cmd": 1}\n',
        b'{"cmd": "read_all"\x00}\n',
        b'{"cmd": "read_all"}\x00\n',
        b"\xff\xfe\xfd\n",
        b"[" * 500 + b"\n",
    ],
)
def test_malformed_input_is_rejected_and_connection_survives(
    client: SensordClient, payload: bytes
) -> None:
    client.send_raw(payload)
    assert client.read_line() == {"ok": False, "error": "bad_request"}
    assert client.call(cmd="read_all")["ok"] is True


def test_crlf_line_endings_are_accepted(client: SensordClient) -> None:
    client.send_raw(b'{"cmd": "read_all"}\r\n')
    assert client.read_line()["ok"] is True


def test_overlong_line_is_rejected_once_and_discarded(client: SensordClient) -> None:
    client.send_raw(b"x" * (MAX_LINE * 3) + b"\n")
    assert client.read_line() == {"ok": False, "error": "line_too_long"}
    # The tail of the long line must not be parsed as further requests.
    assert client.call(cmd="read", sensor="cpu_temp")["ok"] is True


def test_line_of_exactly_max_length_is_accepted(client: SensordClient) -> None:
    req = b'{"cmd": "read_all"}'
    client.send_raw(req + b" " * (MAX_LINE - len(req)) + b"\n")
    assert client.read_line()["ok"] is True


def test_pipelined_and_fragmented_requests(client: SensordClient) -> None:
    client.send_raw(b'{"cmd":"read_all"}\n{"cmd":"read","sensor":"gpu_temp"}\n{"cmd":')
    assert client.read_line()["ok"] is True
    assert client.read_line()["sensor"]["name"] == "gpu_temp"
    client.send_raw(b'"clear_faults"}\n')
    assert client.read_line() == {"ok": True}


def test_faults_are_visible_to_all_clients(sensord) -> None:
    a, b = sensord.client(), sensord.client()
    assert a.call(cmd="inject_fault", sensor="cpu_temp", mode="overtemp") == {"ok": True}
    assert b.call(cmd="read", sensor="cpu_temp")["sensor"]["status"] == "critical"


def test_fault_modes_end_to_end(client: SensordClient) -> None:
    client.call(cmd="inject_fault", sensor="fan2_rpm", mode="disconnected")
    s = client.call(cmd="read", sensor="fan2_rpm")["sensor"]
    assert s["value"] is None
    assert s["status"] == "unavailable"

    client.call(cmd="inject_fault", sensor="psu_watts", mode="stuck")
    frozen = client.call(cmd="read", sensor="psu_watts")["sensor"]["value"]
    time.sleep(0.3)  # several ticks at 50 ms
    assert client.call(cmd="read", sensor="psu_watts")["sensor"]["value"] == frozen

    client.call(cmd="clear_faults")
    for s in client.call(cmd="read_all")["sensors"]:
        assert s["status"] == "ok"


def test_client_disconnect_mid_line_does_not_affect_others(sensord) -> None:
    rude = sensord.client()
    rude.send_raw(b'{"cmd": "read_')
    rude.close()
    assert sensord.client().call(cmd="read_all")["ok"] is True


def test_client_limit(sensord) -> None:
    clients = [sensord.client() for _ in range(16)]
    for c in clients:
        assert c.call(cmd="read", sensor="cpu_temp")["ok"] is True
    extra = SensordClient(sensord.socket_path)
    try:
        assert extra.read_line() == {"ok": False, "error": "too_many_clients"}
    finally:
        extra.close()
    # Freeing a slot lets a new client in.
    clients[0].close()
    sensord._clients.remove(clients[0])
    deadline = time.monotonic() + 2
    while True:
        c = sensord.client()
        try:
            if c.call(cmd="read_all")["ok"]:
                break
        except (ConnectionError, KeyError):
            pass
        assert time.monotonic() < deadline, "slot was never freed"
        time.sleep(0.05)


def test_sigterm_removes_socket(socket_path: str) -> None:
    daemon = start_sensord(socket_path)
    rc, err = daemon.stop()
    assert rc == 0, err
    assert not os.path.exists(socket_path)


def test_stale_socket_is_replaced(socket_path: str) -> None:
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(socket_path)
    stale.close()  # leaves the socket file behind with nobody listening
    daemon = start_sensord(socket_path)
    try:
        assert daemon.client().call(cmd="read_all")["ok"] is True
    finally:
        assert daemon.stop()[0] == 0


def test_refuses_to_steal_live_socket(sensord, socket_path: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="another instance"):
        start_sensord(socket_path)
    assert sensord.client().call(cmd="read_all")["ok"] is True


def test_refuses_non_socket_path(socket_path: str) -> None:
    with open(socket_path, "w") as f:
        f.write("precious data")
    with pytest.raises(pytest.fail.Exception, match="not a socket"):
        start_sensord(socket_path)
    with open(socket_path) as f:
        assert f.read() == "precious data"


@pytest.mark.parametrize("args", [["-t", "0"], ["-t", "abc"], ["-S", "-1"], ["extra"]])
def test_rejects_bad_arguments(socket_path: str, args: list[str]) -> None:
    with pytest.raises(pytest.fail.Exception, match="exited early"):
        start_sensord(socket_path, *args)
