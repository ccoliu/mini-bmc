"""A/B firmware slots on disk, and the trial-boot state machine that makes updates safe.

    firmware/slot_a.bin, slot_b.bin   whole images, exactly as uploaded
    firmware/state.json               {"active": "a", "slots": {...}, "trial": null}

An update writes the INACTIVE slot and marks it as "trial"; the active slot is only
switched after the trial boot passes its self-test. A crash at any point leaves the
old firmware active, so the worst case of a failed update is "nothing changed".

Every file is replaced atomically (temp file + fsync + rename + fsync of the directory),
so a reader sees either the old or the new content, never half of it. state.json is
the commit point: it is always written after the slot it describes.

The same holds in memory. Updates run in a worker thread while HTTP handlers read the
store, so the state is one immutable StoreState that is replaced as a whole, and only
after it is on disk: a reader sees the old state or the new one, never a mix, and never
one that a failed write left unsaved.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .firmware_image import FirmwareImage, ImageError, build_image, parse_image

SLOTS = ("a", "b")
STATE_FILE = "state.json"
FACTORY_VERSION = "1.0.0"
FACTORY_PAYLOAD = b"mini-bmc factory firmware\n" * 256

log = logging.getLogger(__name__)


class StoreError(Exception):
    """The firmware store cannot be trusted; the service must not start on it."""


class UpdateInProgress(Exception):
    """An update was staged while another one is still in its trial boot."""


@dataclass(frozen=True)
class SlotInfo:
    version: str
    sha256: str


@dataclass(frozen=True)
class StoreState:
    """One consistent view of the store. Never modified: every change builds a new one."""

    active: str
    trial: str | None
    slots: Mapping[str, SlotInfo | None]

    def with_slot(self, slot: str, info: SlotInfo | None) -> StoreState:
        return replace(self, slots={**self.slots, slot: info})


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # The rename itself lives in the directory; fsync it so it survives a power cut.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def other(slot: str) -> str:
    return "b" if slot == "a" else "a"


class FirmwareStore:
    def __init__(self, directory: Path, factory_version: str = FACTORY_VERSION) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state = StoreState(active="a", trial=None, slots=dict.fromkeys(SLOTS))
        if (self.dir / STATE_FILE).exists():
            self._load()
        else:
            self._install_factory(factory_version)
        self._recover()
        self._check_active()

    # --- read side ---------------------------------------------------------
    # A caller that needs more than one value reads `state` once and uses that snapshot;
    # separate reads may straddle an update running in another thread.

    @property
    def state(self) -> StoreState:
        return self._state

    @property
    def active_slot(self) -> str:
        return self._state.active

    @property
    def active(self) -> SlotInfo:
        state = self._state
        info = state.slots[state.active]
        assert info is not None  # guaranteed by _load / _check_active
        return info

    @property
    def trial_slot(self) -> str | None:
        return self._state.trial

    def slot(self, slot: str) -> SlotInfo | None:
        return self._state.slots[slot]

    # --- update state machine ---------------------------------------------

    def stage(self, data: bytes) -> FirmwareImage:
        """Verifies an image and writes it to the inactive slot as the next trial boot.
        Raises ImageError (nothing is written) or UpdateInProgress."""
        state = self._state
        if state.trial is not None:
            raise UpdateInProgress(f"slot {state.trial} is still in its trial boot")
        image = parse_image(data)
        target = other(state.active)
        # Forget the old contents first: if we crash mid-write, state.json must not
        # describe a slot whose file no longer matches it.
        state = state.with_slot(target, None)
        self._commit(state)
        atomic_write(self._slot_path(target), data)
        state = state.with_slot(target, SlotInfo(image.version, image.sha256))
        self._commit(replace(state, trial=target))
        return image

    def boot_trial(self) -> bool:
        """The simulated reboot onto the trial slot: commit if it boots and passes its
        self-test, otherwise roll back. Returns True on commit."""
        state = self._state
        trial = state.trial
        if trial is None:
            raise RuntimeError("no trial boot pending")
        image = self._read_slot(trial)  # re-verified from disk, like a real bootloader
        passed = image is not None and image.selftest_passes
        # The active slot and the trial flag change together, in one step.
        self._commit(replace(state, active=trial if passed else state.active, trial=None))
        if passed:
            log.info("Firmware %s committed in slot %s", self.active.version, trial)
        else:
            log.warning("Trial boot of slot %s failed; rolled back to slot %s", trial, state.active)
        return passed

    # --- startup ----------------------------------------------------------------

    def _recover(self) -> None:
        # A trial still pending at startup means we went down during the trial boot.
        # Treat it like a boot that never reported success: roll back.
        state = self._state
        if state.trial is not None:
            log.warning("Interrupted trial boot of slot %s; rolling back", state.trial)
            self._commit(replace(state, trial=None))

    def _check_active(self) -> None:
        state = self._state
        if self._read_slot(state.active) is not None:
            return
        fallback = other(state.active)
        if self._read_slot(fallback) is None:
            raise StoreError("no intact firmware slot to boot")
        log.error("Active slot %s is damaged; falling back to slot %s", state.active, fallback)
        self._commit(replace(state.with_slot(state.active, None), active=fallback))

    def _install_factory(self, version: str) -> None:
        data = build_image(version, FACTORY_PAYLOAD)
        image = parse_image(data)
        atomic_write(self._slot_path("a"), data)
        self._commit(self._state.with_slot("a", SlotInfo(image.version, image.sha256)))

    # --- persistence ------------------------------------------------------------

    def _slot_path(self, slot: str) -> Path:
        return self.dir / f"slot_{slot}.bin"

    def _read_slot(self, slot: str) -> FirmwareImage | None:
        """The image in a slot if it is intact and matches state.json, else None."""
        info = self._state.slots[slot]
        if info is None:
            return None
        try:
            image = parse_image(self._slot_path(slot).read_bytes())
        except (OSError, ImageError) as exc:
            log.error("Firmware slot %s is unreadable: %s", slot, exc)
            return None
        if SlotInfo(image.version, image.sha256) != info:
            log.error("Firmware slot %s does not match %s", slot, STATE_FILE)
            return None
        return image

    def _load(self) -> None:
        path = self.dir / STATE_FILE
        try:
            raw = json.loads(path.read_bytes())
            slots = {
                s: None if raw["slots"][s] is None else SlotInfo(**raw["slots"][s]) for s in SLOTS
            }
            active, trial = raw["active"], raw["trial"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoreError(f"{path} is unreadable: {exc}") from exc
        if (
            active not in SLOTS
            or slots[active] is None
            or (
                trial is not None
                and (trial not in SLOTS or trial == active or slots[trial] is None)
            )
        ):
            raise StoreError(f"{path} is inconsistent")
        self._state = StoreState(active=active, trial=trial, slots=slots)

    def _commit(self, state: StoreState) -> None:
        """Saves `state`, then publishes it to readers: disk first, so memory never runs
        ahead of what a restart would find. If the write fails, nothing changes."""
        self._save(state)
        self._state = state  # one reference swap: readers see all of it or none of it

    def _save(self, state: StoreState) -> None:
        raw = {
            "active": state.active,
            "slots": {s: None if i is None else asdict(i) for s, i in state.slots.items()},
            "trial": state.trial,
        }
        atomic_write(self.dir / STATE_FILE, json.dumps(raw, indent=2).encode())
