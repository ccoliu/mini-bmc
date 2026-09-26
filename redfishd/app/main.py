"""FastAPI application factory.

Run with:  uvicorn app.main:app --app-dir redfishd
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from . import errors
from .config import Settings
from .power import PowerController
from .routers import chassis, service_root, session_service, systems
from .sensord_client import SensordClient
from .sessions import SessionStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    app = FastAPI(
        title="mini-bmc redfishd", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.sensord = SensordClient(settings.sensord_socket, settings.sensord_timeout)
    app.state.power = PowerController()
    app.state.sessions = SessionStore()

    @app.middleware("http")
    async def odata_version(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["OData-Version"] = "4.0"
        return response

    errors.install(app)
    app.include_router(service_root.router)
    app.include_router(chassis.router)
    app.include_router(systems.router)
    app.include_router(session_service.public)
    app.include_router(session_service.router)
    return app


app = create_app()
