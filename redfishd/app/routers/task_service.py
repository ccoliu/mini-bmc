"""TaskService: the Tasks behind long-running operations, and their task monitors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import require_auth
from ..errors import RedfishError
from ..odata import ROOT, collection, link, resource
from ..tasks import Task, TaskManager

SERVICE = f"{ROOT}/TaskService"
TASKS = f"{SERVICE}/Tasks"

router = APIRouter(dependencies=[Depends(require_auth)])


def manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def monitor_uri(task: Task) -> str:
    return f"{TASKS}/{task.id}/Monitor"


def task_body(task: Task) -> dict[str, Any]:
    body = resource(
        "Task",
        f"{TASKS}/{task.id}",
        task.id,
        task.name,
        TaskState=task.state,
        TaskStatus=task.status,
        PercentComplete=task.percent,
        StartTime=task.start_time.isoformat(timespec="seconds"),
        TaskMonitor=monitor_uri(task),
        Messages=task.messages,
    )
    if task.end_time is not None:  # the schema says EndTime only appears once complete
        body["EndTime"] = task.end_time.isoformat(timespec="seconds")
    return body


def find(request: Request, task_id: str) -> Task:
    task = manager(request).get(task_id)
    if task is None:
        raise RedfishError(404, "ResourceNotFound", "Task", task_id)
    return task


@router.get(SERVICE)
async def task_service(request: Request) -> dict[str, Any]:
    return resource(
        "TaskService",
        SERVICE,
        "TaskService",
        "Task Service",
        ServiceEnabled=True,
        DateTime=datetime.now(UTC).isoformat(timespec="seconds"),
        CompletedTaskOverWritePolicy="Oldest",
        LifeCycleEventOnTaskStateChange=False,
        Tasks=link(TASKS),
    )


@router.get(TASKS)
async def task_collection(request: Request) -> dict[str, Any]:
    member = [f"{TASKS}/{t.id}" for t in manager(request).all()]
    return collection("TaskCollection", TASKS, "Task Collection", member)


@router.get(f"{TASKS}/{{task_id}}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    return task_body(find(request, task_id))


@router.get(f"{TASKS}/{{task_id}}/Monitor")
async def task_monitor(task_id: str, request: Request) -> JSONResponse:
    """DSP0266 task monitor: 202 while the task runs, then its final state."""
    task = find(request, task_id)
    if not task.done:
        return JSONResponse(
            task_body(task),
            status_code=202,
            headers={"Location": monitor_uri(task), "Retry-After": "1"},
        )
    return JSONResponse(task_body(task))
