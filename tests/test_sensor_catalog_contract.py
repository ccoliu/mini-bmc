"""Contract between sensord's sensor table and redfishd's CATALOG (issue #2).

The wire protocol carries only name, value, unit and status; redfishd adds the rest from
CATALOG. Nothing at runtime checks the two agree, so this test does, against the real
daemon. Both components ship from this repo together, so a CI-time check is enough; a
runtime check would only pay off if they were ever deployed and upgraded separately.
"""

from __future__ import annotations

import asyncio

from conftest import Sensord

from app.sensor_catalog import CATALOG
from app.sensord_client import SensordClient, SensorReading

# The contract: each sensord unit, and the UCUM unit and ReadingType Redfish must show for it.
UNIT_CONTRACT = {
    "C": ("Cel", "Temperature"),
    "RPM": ("RPM", "Rotational"),
    "W": ("W", "Power"),
}


def read_all(sensord: Sensord) -> list[SensorReading]:
    return asyncio.run(SensordClient(sensord.socket_path).read_all())


def test_catalog_lists_exactly_the_sensors_sensord_has(sensord: Sensord) -> None:
    names = [r.name for r in read_all(sensord)]
    assert len(names) == len(set(names)), "sensord reports a sensor twice"
    missing = set(names) - set(CATALOG)
    extra = set(CATALOG) - set(names)
    assert not missing, f"add to CATALOG: {sorted(missing)}"
    assert not extra, f"CATALOG list sensors sensord does not have: {sorted(extra)}"


def test_catalog_units_match_sensord_units(sensord: Sensord) -> None:
    problems = []
    for r in read_all(sensord):
        info = CATALOG.get(r.name)
        if info is None:
            continue  # reported by test_catalog_lists_exactly_the_sensors_sensord_has
        if r.unit not in UNIT_CONTRACT:
            problems.append(f"{r.name}: sensord unit {r.unit!r} has no UCLM mapping")
            continue
        expected = UNIT_CONTRACT[r.unit]
        actual = (info.reading_units, info.reading_type)
        if actual != expected:
            problems.append(f"{r.name}: sensord says {r.unit!r}, CATALOG says {actual}")
    assert not problems, "\n".join(problems)
