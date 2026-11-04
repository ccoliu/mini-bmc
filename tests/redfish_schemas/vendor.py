"""Re-vendors the DMTF Redfish JSON schemas that the conformance tests need.

    python tests/redfish_schemas/vendor.py
    python tests/redfish_schemas/vendor.py --csdl DIR

Downloads a pinned DMTF Redfish-Publications release, checks its SHA-256, and copies
the files listed in FILES next to this script. If a test fails with "not vendored",
add the named file to FILES and run this again.

With --csdl, it instead extracts every CSDL (XML) schema of the same release into DIR, for
the DMTF Redfish Service Validator. Those are not committed: the validator follows
references between them, so it needs the whole set (about 80 MB).
"""

from __future__ import annotations

import argparse
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
    "SoftwareInventory.json",
    "SoftwareInventory.v1_15_0.json",
    "SoftwareInventoryCollection.json",
    "Task.json",
    "Task.v1_7_4.json",
    "TaskCollection.json",
    "TaskService.json",
    "TaskService.v1_3_0.json",
    "UpdateService.json",
    "UpdateService.v1_17_1.json",
    "odata-v4.json",
    "redfish-error.v1_0_2.json",
]


def download() -> tarfile.TarFile:
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        sys.exit(f"checksum mismatch: expected {SHA256}, got {digest}")
    return tarfile.open(fileobj=io.BytesIO(data))


def vendor_json(tar: tarfile.TarFile) -> None:
    here = Path(__file__).parent
    prefix = f"Redfish-Publications-{TAG}/json-schema/"
    for name in FILES:
        member = tar.extractfile(prefix + name)
        if member is None:
            sys.exit(f"{name} is not a regular file in the {TAG} release")
        (here / name).write_bytes(member.read())
    print(f"vendored {len(FILES)} schemas from DMTF Redfish-Publications {TAG}")


def extract_csdl(tar: tarfile.TarFile, out: Path) -> None:
    prefix = f"Redfish-Publications-{TAG}/csdl/"
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for member in tar.getmembers():
        name = member.name.removeprefix(prefix)
        # Regular .xml files directly in csdl/ only, so no archive entry can pick the path.
        if not (member.isfile() and member.name.startswith(prefix) and name.endswith(".xml")):
            continue
        if "/" in name:
            continue
        file = tar.extractfile(member)
        assert file is not None  # a regular file always has contents
        (out / name).write_bytes(file.read())
        count += 1
    if count == 0:
        sys.exit(f"no CSDL schemas in the {TAG} release")
    print(f"extracted {count} CSDL schemas from DMTF Redfish-Publications {TAG} into {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csdl", type=Path, metavar="DIR", help="extract the CSDL schemas here")
    args = parser.parse_args()
    with download() as tar:
        if args.csdl:
            extract_csdl(tar, args.csdl)
        else:
            vendor_json(tar)


if __name__ == "__main__":
    main()
