"""Remembers when this run started: dispatching CI does not say which run it created."""

from datetime import UTC, datetime, timedelta


def main(inputs):
    # A little earlier than now, so clock skew against GitHub cannot hide the new run.
    since = datetime.now(UTC) - timedelta(seconds=30)
    return {"since": since.strftime("%Y-%m-%dT%H:%M:%SZ")}
