"""ops/coworkify: the shape of the nightly pipeline and the logic of its Python steps."""

from __future__ import annotations

import importlib.util
import json
import re
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops" / "coworkify"
RUN = {"sha": "abc1234", "conclusion": "failure"}


def load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NIGHTLY = load(OPS / "nightly.py")
DEFINITION = NIGHTLY.definition()
STEPS = {s["key"]: s for s in DEFINITION["steps"]}


def job(name: str, conclusion: str) -> dict[str, Any]:
    steps = [
        {"name": "pytest", "conclusion": conclusion},
        {"name": "setup", "conclusion": "success"},
    ]
    return {
        "name": name,
        "conclusion": conclusion,
        "check_run_url": f"https://x/{name}",
        "steps": steps,
    }


def digest_inputs(*jobs: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {"shape": RUN, "jobs": {"status_code": status, "response_body": {"jobs": list(jobs)}}}


def ancestors(key: str) -> set[str]:
    found: set[str] = set()
    for dep in STEPS[key]["depends_on"]:
        found |= {dep} | ancestors(dep)
    return found


def test_every_dependency_exists_and_steps_are_unique() -> None:
    assert len(STEPS) == len(DEFINITION["steps"])
    for key, step in STEPS.items():
        assert set(step["depends_on"]) <= set(STEPS), key
        assert key not in ancestors(key)


def test_a_referenced_step_is_always_a_dependency() -> None:
    for key, step in STEPS.items():
        text = json.dumps({k: v for k, v in step["payload"].items() if k != "code"})
        for ref in re.findall(r"\{\{\s*steps\.(\w+)\.", text):
            assert ref in step["depends_on"], f"{key} uses {ref}"


def test_python_steps_compile() -> None:
    for key, step in STEPS.items():
        if step["task_type"] == "python":
            compile(step["payload"]["code"], key, "exec")


def test_no_for_each_while_coworkify_leaves_a_skipped_reduce_pending() -> None:
    assert not any(s.get("for_each") or s.get("reduce_of") for s in STEPS.values())


def test_the_token_is_only_a_connection_reference_in_headers() -> None:
    for key, step in STEPS.items():
        payload = dict(step["payload"])
        headers = payload.pop("headers", None)
        assert "connections" not in json.dumps(payload), key
        if headers:
            assert headers["Authorization"] == "Bearer {{connections.github}}"


def test_polling_is_authenticated() -> None:
    # Every 30 s is 120 requests an hour; GitHub allows 60 without a token.
    assert "Authorization" in STEPS["wait"]["payload"]["headers"]


def test_the_failure_report_does_not_depend_on_the_model() -> None:
    assert "triage" not in ancestors("notify_red")
    assert "triage" in ancestors("notify_triage")


def test_a_refused_dispatch_is_reported() -> None:
    assert STEPS["dispatched"]["payload"]["right"] == 204
    assert STEPS["notify_dispatch_failed"]["branch_when"] == "false"
    assert STEPS["wait"]["branch_when"] == "true"


def test_ci_is_asked_to_repeat_the_suite() -> None:
    assert STEPS["dispatch"]["payload"]["body"] == {"ref": "main", "inputs": {"repeat": "5"}}


def test_notify_levels_are_ones_coworkify_knows() -> None:
    for step in STEPS.values():
        if step["task_type"] == "notify":
            assert step["payload"]["level"] in ("info", "success", "warning", "critical")


def test_window_is_a_github_timestamp() -> None:
    since = load(OPS / "steps" / "window.py").main({})["since"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", since)


@pytest.mark.parametrize(
    ("conclusion", "passed"), [("success", True), ("failure", False), ("cancelled", False)]
)
def test_shape(conclusion: str, passed: bool) -> None:
    run = {"conclusion": conclusion, "html_url": "u", "head_sha": "abcdef123", "jobs_url": "j"}
    inputs = {"wait": {"response_body": {"workflow_runs": [run]}}}
    assert load(OPS / "steps" / "shape.py").main(inputs) == {
        "passed": passed,
        "conclusion": conclusion,
        "url": "u",
        "sha": "abcdef1",
        "jobs_url": "j",
    }


def test_digest_lists_only_failed_jobs_and_useful_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = load(OPS / "steps" / "digest.py")
    notes = [
        {"path": ".github", "start_line": 1, "message": "Process completed with exit code 1."},
        {"path": "tests/x.py", "start_line": 12, "title": "test_y failed", "message": "assert 0"},
    ]
    asked: list[str] = []
    monkeypatch.setattr(digest, "fetch_annotations", lambda url: asked.append(url) or notes)
    text = digest.main(digest_inputs(job("asan", "failure"), job("lint", "success")))["text"]
    assert asked == ["https://x/asan/annotations"]
    assert text.startswith("mini-bmc nightly at abc1234: failure")
    assert "Failed steps: pytest\n" in text
    assert "- test_y failed (tests/x.py:12): assert 0" in text
    assert "exit code" not in text
    assert "lint" not in text


@pytest.mark.parametrize(
    "error", [urllib.error.URLError("down"), TimeoutError("slow"), ValueError("bad")]
)
def test_digest_survives_annotations_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    digest = load(OPS / "steps" / "digest.py")

    def fail(url: str) -> None:
        raise error

    monkeypatch.setattr(digest, "fetch_annotations", fail)
    assert "annotations unavailable" in digest.main(digest_inputs(job("asan", "failure")))["text"]


def test_digest_when_the_jobs_cannot_be_listed() -> None:
    digest = load(OPS / "steps" / "digest.py")
    assert "did not list the jobs: 403" in digest.main(digest_inputs(status=403))["text"]


def test_digest_when_no_job_is_marked_failed() -> None:
    digest = load(OPS / "steps" / "digest.py")
    assert "no failed jobs" in digest.main(digest_inputs(job("lint", "cancelled")))["text"]


def test_digest_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = load(OPS / "steps" / "digest.py")
    notes = [{"path": "t", "start_line": 1, "message": "x" * 9000}]
    monkeypatch.setattr(digest, "fetch_annotations", lambda url: notes)
    assert len(digest.main(digest_inputs(job("asan", "failure")))["text"]) == digest.MAX_TEXT
