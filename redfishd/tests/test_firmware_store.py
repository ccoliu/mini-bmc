from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.firmware_image import ImageError, build_image
from app.firmware_store import STATE_FILE, FirmwareStore, StoreError, UpdateInProgress

PAYLOAD = b"new firmware" * 100


def image(version: str, *, selftest_passes: bool = True) -> bytes:
    return build_image(version, PAYLOAD, selftest_passes=selftest_passes)


def state(store_dir: Path) -> dict[str, object]:
    return json.loads((store_dir / STATE_FILE).read_text())


def test_first_start_installs_factory_firmware_in_slot_a(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path, factory_version="1.0.0")
    assert store.active_slot == "a"
    assert store.active.version == "1.0.0"
    assert store.slot("b") is None
    assert store.trial_slot is None
    assert (tmp_path / "slot_a.bin").exists()


def test_successful_update_switches_slot(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    assert store.trial_slot == "b"
    assert store.active.version == "1.0.0"  # still running the old firmware
    assert store.boot_trial() is True
    assert store.active_slot == "b"
    assert store.active.version == "1.1.0"
    assert store.trial_slot is None


def test_failed_selftest_rolls_back(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0", selftest_passes=False))
    assert store.boot_trial() is False
    assert store.active_slot == "a"
    assert store.active.version == "1.0.0"
    assert store.trial_slot is None


def test_updates_alternate_between_slots(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    for version, slot in [("1.1.0", "b"), ("1.2.0", "a"), ("1.3.0", "b")]:
        store.stage(image(version))
        assert store.boot_trial() is True
        assert (store.active_slot, store.active.version) == (slot, version)


def test_bad_images_writes_nothing(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    before = state(tmp_path)
    with pytest.raises(ImageError):
        store.stage(build_image("1.1.0", PAYLOAD, sha256="0" * 64))
    assert state(tmp_path) == before
    assert not (tmp_path / "slot_b.bin").exists()


def test_one_update_at_a_time(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    with pytest.raises(UpdateInProgress):
        store.stage(image("1.2.0"))
    with pytest.raises(RuntimeError):
        FirmwareStore(tmp_path / "other").boot_trial()  # nothing staged


def test_state_survives_restart(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    store.boot_trial()
    again = FirmwareStore(tmp_path)
    assert (again.active_slot, again.active.version) == ("b", "1.1.0")
    assert again.slot("a") == store.slot("a")


def test_crash_during_trial_boot_rolls_back_on_restart(tmp_path: Path) -> None:
    FirmwareStore(tmp_path).stage(image("1.1.0"))
    store = FirmwareStore(tmp_path)
    assert store.active.version == "1.0.0"
    assert store.trial_slot is None
    assert state(tmp_path)["trial"] is None


def test_trial_slot_damaged_on_disk_rolls_back(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    slot_b = tmp_path / "slot_b.bin"
    slot_b.write_bytes(slot_b.read_bytes()[:-1])
    assert store.boot_trial() is False
    assert store.active.version == "1.0.0"


def test_damaged_active_slot_falls_back_to_the_other(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    store.boot_trial()  # active b = 1.1.0, a still holds 1.0.0
    (tmp_path / "slot_b.bin").write_bytes(b"bit rot")
    again = FirmwareStore(tmp_path)
    assert (again.active_slot, again.active.version) == ("a", "1.0.0")
    assert again.slot("b") is None


def test_no_intact_slot_refuses_to_start(tmp_path: Path) -> None:
    FirmwareStore(tmp_path)
    (tmp_path / "slot_a.bin").unlink()
    with pytest.raises(StoreError):
        FirmwareStore(tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{not json",
        b"[]",
        b'{"active": "a", "slots": {"a": null, "b": null}, "trial": null}',
        b'{"active": "c", "slots": {"a": null, "b": null}, "trial": null}',
        b'{"active": "a", "slots": {"a": {"version": "1.0.0"}, "b": null}, "trial": null}',
        b'{"active": "a", "slots": {"a": {"version": "1", "sha256": "x"}, "b": null},'
        b' "trial": "a"}',
    ],
    ids=["empty", "not-json", "not-object", "active-empty", "bad-slot", "slot-fields", "trial"],
)
def test_corrupt_state_file_refuses_to_start(tmp_path: Path, content: bytes) -> None:
    FirmwareStore(tmp_path)
    (tmp_path / STATE_FILE).write_bytes(content)
    with pytest.raises(StoreError):
        FirmwareStore(tmp_path)


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    store.boot_trial()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["slot_a.bin", "slot_b.bin", STATE_FILE]


def test_slot_swapped_for_another_valid_image_is_detected(tmp_path: Path) -> None:
    store = FirmwareStore(tmp_path)
    store.stage(image("1.1.0"))
    (tmp_path / "slot_b.bin").write_bytes(image("6.6.6"))  # intact, but not what was staged
    assert store.boot_trial() is False
    assert store.active.version == "1.0.0"
