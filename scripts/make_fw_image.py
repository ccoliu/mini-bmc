"""Builds a simulated firmware image for demos and manual testing.

.venv/bin/python scripts/make_fw_image.py 1.1.0 -o fw-1.1.0.bin
.venv/bin/python scripts/make_fw_image.py 1.2.0 --fail-selftest -o broken.bin
.venv/bin/python scripts/make_fw_image.py 1.3.0 --corrupt -o corrupt.bin
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.firmware_image import build_image, parse_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="MAJOR.MINOR.PATCH")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=64 * 1024, help="payload bytes")
    parser.add_argument(
        "--fail-selftest", action="store_true", help="image installs but fails its first boot"
    )
    parser.add_argument(
        "--corrupt", action="store_true", help="manifest checksum does not match the payload"
    )

    args = parser.parse_args()
    parse_version(args.version)  # fail early with a clear message
    payload = os.urandom(args.size)
    image = build_image(
        args.version,
        payload,
        selftest_passes=not args.fail_selftest,
        sha256="0" * 64 if args.corrupt else None,
    )
    args.output.write_bytes(image)
    print(f"wrote {args.output} ({len(image)} bytes, version {args.version})")


if __name__ == "__main__":
    main()
