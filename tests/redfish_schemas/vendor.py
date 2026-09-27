"""Re-vendors the DMTF Redfish JSON schemas that the conformance tests need.

    python tests/redfish_schemas/vendor.py

Downloads a pinned DMTF Redfish-Publications release, checks its SHA-256, and copies
the files listed in FILES next to this script. If a test fails with "not vendored",
add the named file to FILES and run this again.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

TAG = "2026.2"
URL = f"https://github.com/DMTF/Redfish-Publications/archive/refs/tags/{TAG}.tar.gz"
SHA256 = "6db17d31a91582b50a32e98f2d26fa8495e70b65bc5c4e05429f71085de2cf6c"

FILES = [
    "Chassis.json",
    "Chassis.v1_29_0.json",
    "ChassisCollection.json",
    "ComputerSystem.json",
    "ComputerSystem.v1_29_0.json",
    "ComputerSystemCollection.json",
    "Message.json",
    # Message.json is an anyOf over one file per minor version; all are needed.
    "Message.v1_0_12.json",
    "Message.v1_1_4.json",
    "Message.v1_2_1.json",
    "Message.v1_3_1.json",
    "Message.v1_4_0.json",
    "PhysicalContext.json",
    "Resource.json",
    "Sensor.json",
    "Sensor.v1_14_0.json",
    "SensorCollection.json",
    "ServiceRoot.v1_22_0.json",
    "Session.json",
    "Session.v1_9_0.json",
    "SessionCollection.json",
    "SessionService.json",
    "SessionService.v1_2_0.json",
    "odata-v4.json",
    "redfish-error.v1_0_2.json",
]


def main() -> None:
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        sys.exit(f"checksum mismatch: expected {SHA256}, got {digest}")

    here = Path(__file__).parent
    prefix = f"Redfish-Publications-{TAG}/json-schema/"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for name in FILES:
            member = tar.extractfile(prefix + name)
            if member is None:
                sys.exit(f"{name} is not a regular file in the {TAG} release")
            (here / name).write_bytes(member.read())
    print(f"vendored {len(FILES)} schemas from DMTF Redfish-Publications {TAG}")


if __name__ == "__main__":
    main()
