"""FastAPI application factory.

Run with:  uvicorn --factory app.main:create_app --app-dir redfishd
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from . import errors, protocol, query
from .config import Settings
from .firmware_store import FirmwareStore
from .firmware_update import FirmwareUpdater
from .power import PowerController
from .routers import (
    account_service,
    chassis,
    service_root,
    session_service,
    systems,
    task_service,
    update_service,
)
from .sensord_client import SensordClient
from .sessions import SessionStore
from .tasks import TaskManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.tasks.shutdown()  # cancel background work rather than orphan it


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    app = FastAPI(
        title="mini-bmc redfishd",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(query.check_query)],
        default_response_class=protocol.RedfishJSONResponse,
    )
    app.state.settings = settings
    app.state.sensord = SensordClient(settings.sensord_socket, settings.sensord_timeout)
    app.state.power = PowerController()
    app.state.sessions = SessionStore()
    app.state.tasks = TaskManager()
    # Refuses to start (StoreError) rather than guess which firmware is running.
    store = FirmwareStore(Path(settings.firmware_dir))
    app.state.updater = FirmwareUpdater(store, app.state.tasks, settings.reboot_seconds)

    errors.install(app)
    app.include_router(service_root.router)
    app.include_router(chassis.router)
    app.include_router(systems.router)
    app.include_router(session_service.public)
    app.include_router(session_service.router)
    app.include_router(task_service.router)
    app.include_router(update_service.router)
    app.include_router(account_service.router)
    protocol.install(app)  # last: it reads the finished route table
    return app
