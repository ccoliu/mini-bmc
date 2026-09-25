"""Simulated host power state: the thing ComputerSystem.Reset acts on."""

from __future__ import annotations

from datetime import UTC, datetime

# Every ResetType this service accepts, and the power state it leaves the host in.
# A restart while the host is off simply powers it on, like a real power button.
RESET_RESULT: dict[str, str] = {
    "On": "On",
    "ForceOn": "On",
    "ForceOff": "Off",
    "GracefulShutdown": "Off",
    "GracefulRestart": "On",
    "ForceRestart": "On",
    "PowerCycle": "On",
}
RESTARTS = {"GracefulRestart", "ForceRestart", "PowerCycle"}


class PowerController:
    def __init__(self) -> None:
        self.state = "On"
        self.last_reset: datetime | None = None

    def reset(self, reset_type: str) -> None:
        """Applies a ResetType; callers must validate it against RESET_RESULT first."""
        old, new = self.state, RESET_RESULT[reset_type]
        # LastResetTime means "last came out of reset": a restart, or Off -> On.
        # Powering off, or "On" while already on, does not count.
        if new == "On" and (old == "Off" or reset_type in RESTARTS):
            self.last_reset = datetime.now(UTC)
        self.state = new
