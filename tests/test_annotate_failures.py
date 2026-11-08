"""scripts/annotate_failures.py: failed tests become GitHub Actions annotations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "annotate_failures.py"


def case(name: str, problem: str = "", line: int = 9) -> str:
    attrs = f'classname="tests.test_x" name="{name}" file="tests/test_x.py" line="{line}"'
    return f"<testcase {attrs}>{problem}</testcase>"


FAILED = '<failure message="assert 1 == 2">traceback</failure>'


def report(tmp_path: Path, name: str, *cases: str) -> Path:
    path = tmp_path / name
    path.write_text(f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>")
    return path


def annotate(*args: object) -> list[str]:
    result = subprocess.run(
        [sys.executable, SCRIPT, *map(str, args)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_a_failure_becomes_one_annotation_on_its_line(tmp_path: Path) -> None:
    path = report(tmp_path, "a.xml", case("test_ok"), case("test_bad", FAILED))
    assert annotate(path) == [
        "::error file=tests/test_x.py,line=10,title=test_bad failed::assert 1 == 2"
    ]


def test_root_is_prefixed_for_a_job_that_ran_in_a_subdirectory(tmp_path: Path) -> None:
    path = report(tmp_path, "a.xml", case("test_bad", FAILED))
    assert "file=redfishd/tests/test_x.py," in annotate("--root", "redfishd", path)[0]


def test_an_error_counts_like_a_failure(tmp_path: Path) -> None:
    path = report(tmp_path, "a.xml", case("test_bad", '<error message="fixture broke"/>'))
    assert annotate(path)[0].endswith("::fixture broke")


def test_repeated_runs_show_how_often_a_test_failed(tmp_path: Path) -> None:
    first = report(tmp_path, "1.xml", case("test_flaky", FAILED), case("test_broken", FAILED))
    second = report(tmp_path, "2.xml", case("test_flaky"), case("test_broken", FAILED))
    lines = annotate(first, second)
    assert "title=test_broken failed in 2 of 2 runs::" in lines[0]  # the most frequent first
    assert "title=test_flaky failed in 1 of 2 runs::" in lines[1]


def test_workflow_command_characters_are_escaped(tmp_path: Path) -> None:
    problem = '<failure message="50% off&#10;second line"/>'
    path = report(tmp_path, "a.xml", case("test[a,b:c]", problem))
    line = annotate(path)[0]
    assert "title=test[a%2Cb%3Ac] failed::" in line  # , and : would end a property
    assert line.endswith("::50%25 off%0Asecond line")  # a newline would end the command


def test_long_messages_are_cut(tmp_path: Path) -> None:
    path = report(tmp_path, "a.xml", case("test_bad", f'<failure message="{"x" * 5000}"/>'))
    assert annotate(path)[0].endswith("::" + "x" * 1000)


def test_github_limit_is_respected_and_the_rest_counted(tmp_path: Path) -> None:
    cases = [case(f"test_{i}", FAILED) for i in range(13)]
    lines = annotate(report(tmp_path, "a.xml", *cases))
    assert len(lines) == 11
    assert lines[-1] == "::notice::3 more failing tests are not shown; see the job log"


def test_a_missing_or_broken_report_is_skipped(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites>")
    good = report(tmp_path, "a.xml", case("test_bad", FAILED))
    lines = annotate(tmp_path / "missing.xml", broken, good)
    assert "title=test_bad failed::" in lines[0]  # one readable report: one run


def test_a_passing_run_prints_nothing(tmp_path: Path) -> None:
    assert annotate(report(tmp_path, "a.xml", case("test_ok"))) == []
