"""scripts/check_protocol_report.py: new failures and stale known failures both fail CI."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_protocol_report.py"
HEADER = "Assertion\tMethod\tStatus code\tURI\tResult\tMessage\tRequirement"
KNOWN = {"SEC_EXAMPLE_ONE": "accepted for a reason", "SEC_EXAMPLE_TWO": "and another"}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_protocol_report", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load_script()


def row(assertion: str, result: str, uri: str = "/redfish/v1/") -> str:
    return f"{assertion}\tGET\t200\t{uri}\t{result}\tsome message\tsome requirement"


def write_report(tmp_path: Path, rows: list[str]) -> Path:
    report = tmp_path / "report.tsv"
    report.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return report


def run_script(report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPT, report], capture_output=True, text=True, timeout=30
    )


def test_the_shipped_list_is_empty() -> None:
    # TLS cleared the last accepted failure; a new entry needs a reason in the README too.
    assert CHECK.KNOWN_FAILURES == {}


def test_a_clean_report_passes(tmp_path: Path) -> None:
    rows = [row("RESP_HEADERS_CACHE_CONTROL", "PASS"), row("PROTO_ETAG_X", "NOT_TESTED")]
    result = run_script(write_report(tmp_path, rows))
    assert result.returncode == 0, result.stdout
    assert "matches the known failures" in result.stdout


@pytest.mark.parametrize("result_kind", ["FAIL", "WARN"])
def test_any_failure_fails_against_the_shipped_list(tmp_path: Path, result_kind: str) -> None:
    rows = [row("RESP_HEADERS_ALLOW_GET_OR_HEAD", result_kind)]
    result = run_script(write_report(tmp_path, rows))
    assert result.returncode == 1
    assert "FAIL  RESP_HEADERS_ALLOW_GET_OR_HEAD" in result.stdout
    assert f"{result_kind} GET /redfish/v1/: some message" in result.stdout  # says what broke


def test_exactly_the_known_failures_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [row(a, "FAIL") for a in KNOWN]
    rows.append(row("SEC_EXAMPLE_ONE", "FAIL", uri="/redfish/v1/x"))  # repeats are fine
    assert CHECK.main(["check", str(write_report(tmp_path, rows))], known=KNOWN) == 0
    assert "known SEC_EXAMPLE_ONE: accepted for a reason" in capsys.readouterr().out


def test_a_known_failure_that_passes_now_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [row("SEC_EXAMPLE_ONE", "FAIL"), row("SEC_EXAMPLE_TWO", "PASS")]
    assert CHECK.main(["check", str(write_report(tmp_path, rows))], known=KNOWN) == 1
    assert "FIXED SEC_EXAMPLE_TWO passes now" in capsys.readouterr().out


def test_compare() -> None:
    found = {"SEC_EXAMPLE_ONE": ["..."], "NEW_ONE": ["..."]}
    assert CHECK.compare(found, KNOWN) == (["NEW_ONE"], ["SEC_EXAMPLE_ONE"], ["SEC_EXAMPLE_TWO"])


def test_usage() -> None:
    result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "usage" in result.stderr
