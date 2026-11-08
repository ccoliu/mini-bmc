"""Turns the failed jobs and their annotations into the text for Discord and the model.

Reads the annotations here instead of with a for_each step: Coworkify leaves a reduce step
pending forever when the branch above it is not taken, so a green night would never finish.
The check-runs API is public, so no token is needed in this sandboxed step.
"""

import json
import urllib.request

MAX_TEXT = 6000  # keeps the prompt small enough for a local model
REQUEST_TIMEOUT = 10  # at most five jobs, within this step's 60 s limit
GENERIC = "Process completed with exit code"  # says a step failed; the step name already does


def fetch_annotations(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "mini-bmc-nightly"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.load(response)


def job_section(job):
    failed = [s["name"] for s in job["steps"] if s["conclusion"] == "failure"]
    lines = [f"Job: {job['name']}", f"Failed steps: {', '.join(failed) or 'none'}"]
    try:
        annotations = fetch_annotations(f"{job['check_run_url']}/annotations")
    except (OSError, ValueError) as exc:  # the report must still go out
        return "\n".join([*lines, f"- annotations unavailable: {exc}"])
    for note in annotations:
        if not note["message"].startswith(GENERIC):
            where = f"{note['path']}:{note['start_line']}"
            lines.append(f"- {note.get('title') or 'error'} ({where}): {note['message']}")
    return "\n".join(lines)


def main(inputs):
    run = inputs["shape"]
    header = f"mini-bmc nightly at {run['sha']}: {run['conclusion']}"
    response = inputs["jobs"]
    if response["status_code"] != 200:
        return {"text": f"{header}\n\nGitHub did not list the jobs: {response['status_code']}"}
    jobs = response["response_body"]["jobs"]
    failed = [job for job in jobs if job["conclusion"] in ("failure", "timed_out")]
    body = "\n\n".join(map(job_section, failed)) or "GitHub listed no failed jobs for this run."
    return {"text": f"{header}\n\n{body}"[:MAX_TEXT]}
