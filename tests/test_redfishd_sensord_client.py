"""redfishd's SensordClient against the real sensord binary (protocol compatibility)."""

from __future__ import annotations

import asyncio

import pytest
from app.sensor_catalog import CATALOG
from app.sensord_client import SensordClient, SensordRequestError
from conftest import Sensord


def test_client_speaks_real_protocol(sensord: Sensord) -> None:
    client = SensordClient(sensord.socket_path)

    async def scenario() -> None:
        readings = await client.read_all()
        assert {r.name for r in readings} == set(CATALOG)
        assert all(r.status == "ok" and isinstance(r.value, float) for r in readings)

        await client.inject_fault("fan1_rpm", "disconnected")
        fan = await client.read("fan1_rpm")
        assert (fan.value, fan.status) == (None, "unavailable")

        with pytest.raises(SensordRequestError) as exc:
            await client.inject_fault("psu_watts", "overtemp")
        assert exc.value.code == "invalid_fault_for_sensor"

        await client.clear_faults()
        assert (await client.read("fan1_rpm")).status == "ok"

    asyncio.run(scenario())
