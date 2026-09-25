"""Runtime settings, read once from the environment at startup."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    sensord_socket: str = "/tmp/sensord.sock"
    sensord_timeout: float = 2.0  # seconds, per request to sensord
    username: str = "admin"
    password: str = "admin"  # simulator default; override via REDFISH_PASSWORD
    # Stable across restarts so clients can recognise the same service.
    service_uuid: str = str(uuid.uuid5(uuid.NAMESPACE_DNS, "mini-bmc.local"))

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            sensord_socket=os.environ.get("SENSORD_SOCKET", d.sensord_socket),
            sensord_timeout=float(os.environ.get("SENSORD_TIMEOUT", d.sensord_timeout)),
            username=os.environ.get("REDFISH_USERNAME", d.username),
            password=os.environ.get("REDFISH_PASSWORD", d.password),
            service_uuid=os.environ.get("REDFISH_UUID", d.service_uuid),
        )
