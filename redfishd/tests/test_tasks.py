from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.odata import registry_message
from app.tasks import Task, TaskFailed, TaskLimitReached, TaskManager

TASKS = "/redfish/v1/TaskService/Tasks"


def message_ids(task: Task) -> list[str]:
    return [m["MessageId"] for m in task.messages]


async def wait_done(task: Task) -> None:
    async with asyncio.timeout(2):
        while not task.done:
            await asyncio.sleep(0.01)


async def finished(manager: TaskManager, name: str = "job") -> Task:
    async def work(task: Task) -> None:
        pass

    task = manager.start(name, work)
    await wait_done(task)
    return task


# --- TaskManager -----------------------------------------------------------------


def test_successful_work_completes() -> None:
    seen: list[str] = []

    async def work(task: Task) -> None:
        seen.append(task.state)
        task.percent = 50

    async def scenario() -> Task:
        task = TaskManager().start("job", work)
        assert task.state == "New"  # returned before the work has run
        await wait_done(task)
        return task

    task = asyncio.run(scenario())
    assert seen == ["Running"]
    assert (task.state, task.status, task.percent) == ("Completed", "OK", 100)
    assert task.end_time is not None
    assert task.end_time >= task.start_time
    assert message_ids(task) == ["TaskEvent.1.1.TaskStarted", "TaskEvent.1.1.TaskCompletedOK"]
    assert task.messages[0]["MessageArgs"] == [task.id]


def test_expected_failure_keeps_its_own_messages() -> None:
    async def work(task: Task) -> None:
        task.add_message(
            registry_message("Test.1.0", {"Boom": ("Boom %1.", "Critical")}, "Boom", "x")
        )
        raise TaskFailed

    async def scenario() -> Task:
        task = TaskManager().start("job", work)
        await wait_done(task)
        return task

    task = asyncio.run(scenario())
    assert (task.state, task.status) == ("Exception", "Critical")
    assert message_ids(task) == [
        "TaskEvent.1.1.TaskStarted",
        "Test.1.0.Boom",
        "TaskEvent.1.1.TaskAborted",
    ]
    assert task.percent < 100


def test_crash_is_logged_and_aborts_the_task(caplog: pytest.LogCaptureFixture) -> None:
    async def work(task: Task) -> None:
        raise ValueError("bug")

    async def scenario() -> Task:
        task = TaskManager().start("job", work)
        await wait_done(task)
        return task

    task = asyncio.run(scenario())
    assert (task.state, task.status) == ("Exception", "Critical")
    assert "crashed" in caplog.text
    assert "ValueError: bug" in caplog.text


def test_oldest_completed_task_is_evicted() -> None:
    async def scenario() -> tuple[TaskManager, list[Task]]:
        manager = TaskManager(limit=3)
        tasks = [await finished(manager, f"job{i}") for i in range(4)]
        return manager, tasks

    manager, tasks = asyncio.run(scenario())
    assert [t.id for t in manager.all()] == ["2", "3", "4"]  # ids are never reused
    assert manager.get(tasks[0].id) is None


def test_running_tasks_are_never_evicted() -> None:
    async def scenario() -> None:
        manager = TaskManager(limit=2)
        release = asyncio.Event()

        async def blocked(task: Task) -> None:
            await release.wait()

        manager.start("a", blocked)
        manager.start("b", blocked)
        with pytest.raises(TaskLimitReached):
            manager.start("c", blocked)
        release.set()
        await manager.shutdown()

    asyncio.run(scenario())


def test_shutdown_cancels_running_work() -> None:
    async def scenario() -> Task:
        manager = TaskManager()

        async def forever(task: Task) -> None:
            await asyncio.Event().wait()

        task = manager.start("job", forever)
        await asyncio.sleep(0)  # let it start
        await manager.shutdown()
        return task

    task = asyncio.run(scenario())
    assert (task.state, task.status) == ("Cancelled", "Warning")
    assert message_ids(task)[-1] == "TaskEvent.1.1.TaskCancelled"


# --- HTTP ------------------------------------------------------------------------


def start_task(client: TestClient, app: FastAPI, release: threading.Event) -> Task:
    async def work(task: Task) -> None:
        while not release.is_set():
            await asyncio.sleep(0.01)

    async def start() -> Task:
        return app.state.tasks.start("Test job", work)

    assert client.portal is not None  # set while the client is open
    return client.portal.call(start)  # run it on the server's event loop


def test_task_service_and_empty_collection(live: TestClient) -> None:
    service = live.get("/redfish/v1/TaskService").json()
    assert service["Tasks"] == {"@odata.id": TASKS}
    assert service["ServiceEnabled"] is True
    tasks = live.get(TASKS).json()
    assert (tasks["Members"], tasks["Members@odata.count"]) == ([], 0)


def test_monitor_reports_202_until_the_task_is_done(live: TestClient, app: FastAPI) -> None:
    release = threading.Event()
    task = start_task(live, app, release)
    monitor = f"{TASKS}/{task.id}/Monitor"

    r = live.get(monitor)
    assert r.status_code == 202
    assert r.headers["Location"] == monitor
    assert r.json()["TaskState"] == "Running"
    assert "EndTime" not in r.json()

    body = live.get(f"{TASKS}/{task.id}").json()
    assert body["TaskMonitor"] == monitor
    assert live.get(TASKS).json()["Members"] == [{"@odata.id": f"{TASKS}/{task.id}"}]

    release.set()
    deadline = time.monotonic() + 2
    while (r := live.get(monitor)).status_code == 202:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert r.status_code == 200
    body = r.json()
    assert (body["TaskState"], body["TaskStatus"], body["PercentComplete"]) == (
        "Completed",
        "OK",
        100,
    )
    assert "EndTime" in body


@pytest.mark.parametrize("path", [f"{TASKS}/99", f"{TASKS}/99/Monitor"])
def test_unknown_task_is_404(live: TestClient, path: str) -> None:
    r = live.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "Base.1.24.ResourceNotFound"
