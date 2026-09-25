"""Static Redfish metadata for each sensord sensor: what the wire protocol does not carry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorInfo:
    name: str  # Redfish "Name"
    reading_type: str  # Sensor.ReadingType enum
    reading_units: str  # UCUM unit string, as Redfish requires ("Cel", not "C")
    physical_context: str  # PhysicalContext enum


# Keyed by sensord sensor name, which is also used as the Redfish sensor Id.
CATALOG: dict[str, SensorInfo] = {
    "cpu_temp": SensorInfo("CPU Temperature", "Temperature", "Cel", "CPU"),
    "gpu_temp": SensorInfo("GPU Temperature", "Temperature", "Cel", "GPU"),
    "inlet_temp": SensorInfo("Inlet Temperature", "Temperature", "Cel", "Intake"),
    "fan1_rpm": SensorInfo("Fan 1 Speed", "Rotational", "RPM", "Fan"),
    "fan2_rpm": SensorInfo("Fan 2 Speed", "Rotational", "RPM", "Fan"),
    "psu_watts": SensorInfo("PSU Input Power", "Power", "W", "PowerSupply"),
}
