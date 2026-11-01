"""Long-running operations as Redfish Tasks: start in the background, poll for the result.

The TaskManager knows nothing about HTTP or about what the work does; a route hands it
an async function and returns 202 with the task monitor URI straight away.
Tasks live in memory only, like sessions: they vanish on restart.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .odata import registry_message

MAX_TASKS = 32


# The subset of the DMTF TaskEvent.1.1.0 registry this service emits.
TASK_EVENT = "TaskEvent.1.1"
TASK_EVENTS: dict[str, tuple[str, str]] = {
    "TaskStarted": ("The task with Id '%1' has started.", "OK"),
    "TaskCompletedOK": ("The task with Id '%1' has completed.", "OK"),
    "TaskAborted": ("The task with Id '%1' has completed with errors.", "Critical"),
    "TaskCancelled": (
        "Work on the task with Id '%1' was halted prior to completion due to an explicit request.",
        "Warning",
    ),
}

log = logging.getLogger(__name__)


class TaskFailed(Exception):
    """Raised by work that failed in an expected way, after adding its own messages."""


class TaskLimitReached(Exception):
    """Every task slot holds a task that is still running."""


@dataclass
class Task:
    id: str
    name: str
    state: str = "New"
    status: str = "OK"
    percent: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.end_time is not None

    def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def _event(self, key: str) -> None:
        self.add_message(registry_message(TASK_EVENT, TASK_EVENTS, key, self.id))

    def _finish(self, state: str, status: str, event: str) -> None:
        self.state, self.status = state, status
        self.end_time = datetime.now(UTC)
        self._event(event)


Work = Callable[[Task], Awaitable[None]]


class TaskManager:
    def __init__(self, limit: int = MAX_TASKS) -> None:
        self.limit = limit
        self._tasks: dict[str, Task] = {}
        self._next_id = 1
        # asyncio keeps only a weak reference to running tasks; without this set a
        # task could be garbage-collected halfway through its work.
        self._running: set[asyncio.Task[None]] = set()

    def start(self, name: str, work: Work) -> Task:
        """Starts `work` in the background and returns its Task at once.
        Must be called from inside the running event loop (i.e. from an async route)."""
        self._evict()
        task = Task(str(self._next_id), name)
        self._next_id += 1
        self._tasks[task.id] = task
        runner = asyncio.create_task(self._run(task, work))
        self._running.add(runner)
        runner.add_done_callback(self._running.discard)
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    async def shutdown(self) -> None:
        """Cancels whatever is still running; called when the service stops."""
        for runner in self._running:
            runner.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)

    async def _run(self, task: Task, work: Work) -> None:
        task.state = "Running"
        task._event("TaskStarted")
        try:
            await work(task)
        except asyncio.CancelledError:
            task._finish("Cancelled", "Warning", "TaskCancelled")
            raise
        except TaskFailed:
            task._finish("Exception", "Critical", "TaskAborted")
        except Exception:
            log.exception("Task %s (%s) crashed", task.id, task.name)
            task._finish("Exception", "Critical", "TaskAborted")
        else:
            task.percent = 100
            task._finish("Completed", "OK", "TaskCompletedOK")

    def _evict(self) -> None:
        # CompletedTaskOverWritePolicy "Oldest": dicts keep insertion order.
        for task_id in [t.id for t in self._tasks.values() if t.done]:
            if len(self._tasks) < self.limit:
                break
            del self._tasks[task_id]
        if len(self._tasks) >= self.limit:
            raise TaskLimitReached(f"{self.limit} tasks are still running")
