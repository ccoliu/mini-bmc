"""The simulated firmware image format, and the checks every image must pass.

Layout (all integers big-endian):

    offset 0   4 bytes   magic b"MBFW"
           4   1 byte    format version (1)
           5   2 bytes   manifest length N
           7   N bytes   manifest: UTF-8 JSON object
         7+N   rest      payload (the "firmware")

Manifest keys: "component" (must be "bmc"), "version" ("MAJOR.MINOR.PATCH"),
"sha256" (hex digest of the payload) and, for the simulation only, an optional
"selftest" ("pass" or "fail") deciding whether the image survives its first boot.
Unknown keys are ignored so a newer image format can add fields.

The SHA-256 proves the payload was not corrupted in transit or storage (integrity).
It does not prove who built the image (authenticity); that would need a signature.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from typing import Any

MAGIC = b"MBFW"
FORMAT_VERSION = 1
HEADER = struct.Struct(">4sBH")
COMPONENT = "bmc"
MAX_IMAGE_BYTES = 1024 * 1024  # small on purpose: this is a simulator

_VERSION = re.compile(r"(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class ImageError(Exception):
    """An image that must not be installed. `reason` is a stable code for callers."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FirmwareImage:
    version: str
    sha256: str
    selftest_passes: bool
    payload: bytes


def parse_version(text: str) -> tuple[int, int, int]:
    """'1.10.0' -> (1, 10, 0), so versions compare numerically, not as strings."""
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ImageError("bad_version", f"{text!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _manifest_str(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str):
        raise ImageError("bad_manifest", f"{key!r} must be a string")
    return value


def parse_image(data: bytes, max_size: int = MAX_IMAGE_BYTES) -> FirmwareImage:
    """Validates a whole image; raises ImageError on the first problem found.
    Cheap structural checks run before hashing, and nothing here has side effects,
    so a rejected image can never leave anything half-installed."""
    if len(data) > max_size:
        raise ImageError("too_large", f"{len(data)} bytes, limit is {max_size}")
    if len(data) < HEADER.size:
        raise ImageError("truncated", "shorter than image header")
    magic, fmt, manifest_len = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ImageError("bad_magic", "not a mini-bmc firmware image")
    if fmt != FORMAT_VERSION:
        raise ImageError("unsupported_format", f"format version {fmt}")
    body = data[HEADER.size :]
    if len(body) < manifest_len:
        raise ImageError("truncated", "manifest is cut short")

    try:
        manifest = json.loads(body[:manifest_len].decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImageError("bad_manifest", "manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ImageError("bad_manifest", "manifest is not a JSON object")

    component = _manifest_str(manifest, "component")
    if component != COMPONENT:
        raise ImageError("wrong_component", f"image is for {component!r}, not {COMPONENT!r}")
    version = _manifest_str(manifest, "version")
    parse_version(version)
    expected = _manifest_str(manifest, "sha256")
    if _SHA256_HEX.fullmatch(expected) is None:
        raise ImageError("bad_manifest", "'sha256' must be 64 lowercase hex digits")
    selftest = manifest.get("selftest", "pass")
    if selftest not in ("pass", "fail"):
        raise ImageError("bad_manifest", "'selftest' must be 'pass' or 'fail'")

    payload = body[manifest_len:]
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ImageError("checksum_mismatch", f"payload hashes to {actual}")

    return FirmwareImage(version, actual, selftest == "pass", payload)


def build_image(
    version: str,
    payload: bytes,
    *,
    selftest_passes: bool = True,
    component: str = COMPONENT,
    sha256: str | None = None,
) -> bytes:
    """The inverse of parse_image, for tests and scripts/make_fw_image.py.
    `sha256` overrides the computed digest, to build a deliberately corrupt image."""
    manifest = {
        "component": component,
        "version": version,
        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
        "selftest": "pass" if selftest_passes else "fail",
    }
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    return HEADER.pack(MAGIC, FORMAT_VERSION, len(raw)) + raw + payload
