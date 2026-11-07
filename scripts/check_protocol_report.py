"""Compares a DMTF Redfish Protocol Validator report with the failures this service accepts.

    python3 scripts/check_protocol_report.py REPORT.tsv

Exits 1 if an assertion fails (or warns) that is not in KNOWN_FAILURES: a regression. Also
exits 1 if one in KNOWN_FAILURES no longer fails: it was fixed, so remove it and keep the
list true. Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Assertion ID -> why this service does not meet it (README, "Known limitations").
KNOWN_FAILURES = {
    "SEC_BASIC_AUTH_OVER_HTTPS": "plain HTTP: the simulator has no TLS",
    "SEC_SESSION_CREATE_HTTPS_ONLY": "plain HTTP: the simulator has no TLS",
    "SEC_PRIV_SUPPORT_PREDEFINED_ROLES": "no AccountService or Roles: one account from the env",
}


def failures(report: Path) -> dict[str, list[str]]:
    """Assertion ID -> one line per failing (or warning) check. The report is plain
    tab-separated text with no quoting, one result per line."""
    found: dict[str, list[str]] = {}
    lines = report.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:  # after the header
        assertion, method, _status, uri, result, message, _requirement = line.split("\t", 6)
        if result in ("FAIL", "WARN"):
            found.setdefault(assertion, []).append(f"{result} {method} {uri}: {message}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} REPORT.tsv", file=sys.stderr)
        return 2
    found = failures(Path(argv[1]))
    new = sorted(found.keys() - KNOWN_FAILURES.keys())
    fixed = sorted(KNOWN_FAILURES.keys() - found.keys())
    for assertion in sorted(found.keys() & KNOWN_FAILURES.keys()):
        print(f"known {assertion}: {KNOWN_FAILURES[assertion]}")
    for assertion in new:
        print(f"FAIL  {assertion}")
        for detail in found[assertion]:
            print(f"      {detail}")
    for assertion in fixed:
        print(f"FIXED {assertion} passes now: remove it from KNOWN_FAILURES")
    if new or fixed:
        return 1
    print("the report matches the known failures")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
