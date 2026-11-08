"""Turns the failed tests in pytest JUnit reports into GitHub Actions error annotations.

    python3 scripts/annotate_failures.py [--root DIR] REPORT.xml...

Run it after a failed test step (pytest -o junit_family=xunit1, which records file and line).
Each report is one run of the suite, so a test that failed in only some of them shows up as
"failed in 2 of 5 runs": probably flaky. Annotations appear on the run's page and can be read
back from the public check-runs API, which is how the nightly triage learns what broke.
Standard library only. Always exits 0: the test step has already failed the job.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_ANNOTATIONS = 10  # GitHub keeps at most 10 error annotations per step
MAX_MESSAGE = 1000


@dataclass
class Failure:
    test: str
    file: str | None
    line: int | None
    message: str
    runs: int = 1


def collect(reports: list[Path]) -> tuple[list[Failure], int]:
    """(failures, most frequent first; how many reports could be read)."""
    failures: dict[tuple[str | None, str | None], Failure] = {}
    runs = 0
    for report in reports:
        try:
            root = ET.parse(report).getroot()
        except (OSError, ET.ParseError) as exc:
            print(f"skipping {report}: {exc}", file=sys.stderr)
            continue
        runs += 1
        seen_in_this_run = set()
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            key = (case.get("classname"), case.get("name"))
            if problem is None or key in seen_in_this_run:
                continue
            seen_in_this_run.add(key)
            if key in failures:
                failures[key].runs += 1
            else:
                line = case.get("line")
                failures[key] = Failure(
                    test=case.get("name") or "unknown test",
                    file=case.get("file"),
                    # pytest records a 0-based line; GitHub expects 1-based
                    line=None if line is None else int(line) + 1,
                    message=problem.get("message") or "test failed",
                )
    return sorted(failures.values(), key=lambda f: -f.runs), runs


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(failure: Failure, runs: int, root: str) -> str:
    title = f"{failure.test} failed"
    if runs > 1:
        title += f" in {failure.runs} of {runs} runs"
    properties = []
    if failure.file:
        properties.append(f"file={escape_property(str(PurePosixPath(root, failure.file)))}")
        if failure.line is not None:
            properties.append(f"line={failure.line}")
    properties.append(f"title={escape_property(title)}")
    return f"::error {','.join(properties)}::{escape_data(failure.message[:MAX_MESSAGE])}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="*", type=Path)
    parser.add_argument("--root", default="", help="directory pytest ran in, from the repo root")
    args = parser.parse_args()

    failures, runs = collect(args.reports)
    for failure in failures[:MAX_ANNOTATIONS]:
        print(annotation(failure, runs, args.root))
    hidden = len(failures) - MAX_ANNOTATIONS
    if hidden > 0:
        print(f"::notice::{hidden} more failing tests are not shown; see the job log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
