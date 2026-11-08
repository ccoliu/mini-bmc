"""Picks out what the rest of the pipeline needs from the finished CI run."""


def main(inputs):
    run = inputs["wait"]["response_body"]["workflow_runs"][0]
    return {
        "passed": run["conclusion"] == "success",
        "conclusion": run["conclusion"],
        "url": run["html_url"],
        "sha": run["head_sha"][:7],
        "jobs_url": run["jobs_url"],
    }
