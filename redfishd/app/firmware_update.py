"""A firmware update from upload to commit or rollback, run as a Task.

Verification happens up front, synchronously, so a bad image is rejected before any
Task exists; installing and the trial boot run in the background.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .firmware_image import parse_image
from .firmware_store import FirmwareStore, UpdateInProgress
from .odata import registry_message
from .tasks import Task, TaskFailed, TaskManager

COMPONENT = "BMC"

# The subset of the DMTF Update.1.5.0 registry this service emits.
UPDATE = "Update.1.5"
UPDATE_MESSAGES: dict[str, tuple[str, str]] = {
    "VerificationFailed": ("Verification of image '%1' at '%2' failed.", "Critical"),
    "InstallingOnComponent": ("Image '%1' is being installed on '%2'.", "OK"),
    "ActivatingOnComponent": ("Image '%1' is being activated on '%2'.", "OK"),
    "ActivateFailed": ("Activation of image '%1' on '%2' failed.", "Critical"),
    "UpdateSuccessful": ("Device '%1' successfully updated with image '%2'.", "OK"),
}


def update_message(key: str, *args: str) -> dict[str, Any]:
    return registry_message(UPDATE, UPDATE_MESSAGES, key, *args)


class FirmwareUpdater:
    def __init__(self, store: FirmwareStore, tasks: TaskManager, reboot_seconds: float):
        self.store = store
        self.tasks = tasks
        self.reboot_seconds = reboot_seconds
        self._busy = False

    def start(self, data: bytes, filename: str) -> Task:
        """Verifies the image and starts installing it; returns the Task at once.
        Raises UpdateInProgress, ImageError (nothing written, no Task) or TaskLimitReached."""
        if self._busy or self.store.trial_slot is not None:
            raise UpdateInProgress("a firmware update is already running")
        image = parse_image(data)
        # No await between the check above and this line, so two uploads arriving
        # together cannot both get past it: the event loop runs one at a time.
        self._busy = True

        async def work(task: Task) -> None:
            try:
                await self._install(task, data, filename)
            finally:
                self._busy = False

        try:
            return self.tasks.start(f"Firmware update to {image.version}", work)
        except Exception:
            self._busy = False
            raise

    async def _install(self, task: Task, data: bytes, filename: str) -> None:
        old = self.store.active.version
        task.add_message(update_message("InstallingOnComponent", filename, COMPONENT))
        # File writes and fsync block; keep them off the event loop.
        await asyncio.to_thread(self.store.stage, data)
        task.percent = 30

        task.add_message(update_message("ActivatingOnComponent", filename, COMPONENT))
        await asyncio.sleep(self.reboot_seconds)  # the simulated reboot
        task.percent = 60
        if not await asyncio.to_thread(self.store.boot_trial):
            failed = update_message("ActivateFailed", filename, COMPONENT)
            failed["Resolution"] = f"Rolled back; firmware {old} is still running"
            task.add_message(failed)
            raise TaskFailed
        task.add_message(update_message("UpdateSuccessful", COMPONENT, filename))
