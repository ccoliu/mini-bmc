"""scripts/check_protocol_report.py: new failures and stale known failures both fail CI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_protocol_report.py"
HEADER = "Assertion\tMethod\tStatus code\tURI\tResult\tMessage\tRequirement"
KNOWN = [
    "SEC_BASIC_AUTH_OVER_HTTPS",
    "SEC_SESSION_CREATE_HTTPS_ONLY",
    "SEC_PRIV_SUPPORT_PREDEFINED_ROLES",
]


def row(assertion: str, result: str, uri: str = "/redfish/v1/") -> str:
    return f"{assertion}\tGET\t200\t{uri}\t{result}\tsome message\tsome requirement"


def check(tmp_path: Path, rows: list[str]) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "report.tsv"
    report.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, SCRIPT, report], capture_output=True, text=True, timeout=30
    )


def test_exactly_the_known_failures_pass(tmp_path: Path) -> None:
    rows = [row(a, "FAIL") for a in KNOWN] + [row("RESP_HEADERS_CACHE_CONTROL", "PASS")]
    rows.append(row("SEC_PRIV_SUPPORT_PREDEFINED_ROLES", "FAIL", uri=""))  # repeats are fine
    result = check(tmp_path, rows)
    assert result.returncode == 0, result.stdout
    assert "matches the known failures" in result.stdout


@pytest.mark.parametrize("result_kind", ["FAIL", "WARN"])
def test_a_new_failure_fails(tmp_path: Path, result_kind: str) -> None:
    rows = [row(a, "FAIL") for a in KNOWN] + [row("RESP_HEADERS_ALLOW_GET_OR_HEAD", result_kind)]
    result = check(tmp_path, rows)
    assert result.returncode == 1
    assert "FAIL  RESP_HEADERS_ALLOW_GET_OR_HEAD" in result.stdout
    assert f"{result_kind} GET /redfish/v1/: some message" in result.stdout  # says what broke


def test_a_known_failure_that_passes_now_fails(tmp_path: Path) -> None:
    rows = [row(a, "FAIL") for a in KNOWN[1:]] + [row(KNOWN[0], "PASS")]
    result = check(tmp_path, rows)
    assert result.returncode == 1
    assert f"FIXED {KNOWN[0]} passes now" in result.stdout


def test_not_tested_is_not_a_failure(tmp_path: Path) -> None:
    rows = [row(a, "FAIL") for a in KNOWN] + [row("PROTO_ETAG_CONDITIONAL_GET", "NOT_TESTED")]
    assert check(tmp_path, rows).returncode == 0


def test_usage(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "usage" in result.stderr
