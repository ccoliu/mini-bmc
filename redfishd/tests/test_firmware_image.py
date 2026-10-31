from __future__ import annotations

import hashlib
import json

import pytest

from app.firmware_image import (
    HEADER,
    MAGIC,
    MAX_IMAGE_BYTES,
    ImageError,
    build_image,
    parse_image,
    parse_version,
)

PAYLOAD = b"\x7fELF pretend firmware" * 100


def raw_image(manifest: object, payload: bytes = PAYLOAD, fmt: int = 1) -> bytes:
    """Hand-built image, for manifests that build_image would never produce."""
    raw = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
    return HEADER.pack(MAGIC, fmt, len(raw)) + raw + payload


def good_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "component": "bmc",
        "version": "1.2.3",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
    }
    manifest.update(overrides)
    return manifest


def reason_of(data: bytes, **kwargs: int) -> str:
    with pytest.raises(ImageError) as exc:
        parse_image(data, **kwargs)
    return exc.value.reason


def test_round_trip() -> None:
    image = parse_image(build_image("1.2.3", PAYLOAD))
    assert image.version == "1.2.3"
    assert image.payload == PAYLOAD
    assert image.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert image.selftest_passes is True


def test_selftest_flag_and_defaults() -> None:
    assert (
        parse_image(build_image("1.0.0", PAYLOAD, selftest_passes=False)).selftest_passes is False
    )
    # "selftest" is optional and defaults to pass; unknown keys are ignored.
    image = parse_image(raw_image(good_manifest(future_key=1)))
    assert image.selftest_passes is True


def test_empty_payload_is_a_valid_image() -> None:
    assert parse_image(build_image("0.0.1", b"")).payload == b""


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"", "truncated", id="empty"),
        pytest.param(b"MBFW\x01", "truncated", id="short-header"),
        pytest.param(b"ELF!" + build_image("1.0.0", PAYLOAD)[4:], "bad_magic", id="magic"),
        pytest.param(raw_image(good_manifest(), fmt=2), "unsupported_format", id="format-2"),
        pytest.param(HEADER.pack(MAGIC, 1, 500) + b"{}", "truncated", id="manifest-cut"),
        pytest.param(raw_image(b"\xff\xfe"), "bad_manifest", id="not-utf8"),
        pytest.param(raw_image(b"{nope"), "bad_manifest", id="not-json"),
        pytest.param(raw_image([1, 2]), "bad_manifest", id="not-object"),
        pytest.param(raw_image(good_manifest(component=None)), "bad_manifest", id="no-component"),
        pytest.param(
            raw_image(good_manifest(component="bios")), "wrong_component", id="wrong-component"
        ),
        pytest.param(raw_image(good_manifest(version=1)), "bad_manifest", id="version-not-str"),
        pytest.param(raw_image(good_manifest(version="v1.2")), "bad_version", id="version-form"),
        pytest.param(raw_image(good_manifest(sha256="ABC")), "bad_manifest", id="sha-not-hex"),
        pytest.param(raw_image(good_manifest(selftest="maybe")), "bad_manifest", id="selftest"),
        pytest.param(
            build_image("1.0.0", PAYLOAD, sha256="0" * 64), "checksum_mismatch", id="checksum"
        ),
        pytest.param(build_image("1.0.0", PAYLOAD)[:-1], "checksum_mismatch", id="payload-cut"),
    ],
)
def test_rejections(data: bytes, reason: str) -> None:
    assert reason_of(data) == reason


def test_flipping_any_payload_byte_is_detected() -> None:
    image = bytearray(build_image("1.0.0", PAYLOAD))
    for offset in range(
        len(image) - len(PAYLOAD), len(image), 97
    ):  # 只挑 payload 的範圍,每隔 97 bytes 選一個位置
        tampered = bytearray(image)
        tampered[offset] ^= 0x01  # 把那個 byte 的最低位元翻轉(0↔1)
        assert reason_of(bytes(tampered)) == "checksum_mismatch"


def test_size_limit_is_checked_before_anything_else() -> None:
    image = build_image("1.0.0", PAYLOAD)
    assert parse_image(image, max_size=len(image)).version == "1.0.0"
    assert reason_of(image, max_size=len(image) - 1) == "too_large"
    assert reason_of(b"x" * (MAX_IMAGE_BYTES + 1)) == "too_large"  # not "bad_magic"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.0.0", (0, 0, 0)), ("1.10.2", (1, 10, 2)), ("9999.0.1", (9999, 0, 1))],
)
def test_parse_version(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


def test_version_compare_numerically() -> None:
    assert parse_version("1.10.0") > parse_version("1.9.9")  # as strings, "1.10.0" < "1.9.9"


@pytest.mark.parametrize("text", ["1.2", "1.2.3.4", "01.2.3", "1.2.-3", "a.b.c", "", "10000.0.0"])
def test_parse_version_rejects(text: str) -> None:
    with pytest.raises(ImageError) as exc:
        parse_version(text)
    assert exc.value.reason == "bad_version"
