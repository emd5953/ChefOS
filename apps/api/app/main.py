"""FastAPI app factory.

To add a module: create its package with `api.py` exposing `router`, then add one
`include_router` line in `create_app` (see apps/api/README.md).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import health
from app.commerce import api as commerce_api
from app.config import get_settings
from app.context import request_id_var
from app.errors import install_error_handlers
from app.identity import api as identity_api
from app.integrations import api as integrations_api
from app.inventory import api as inventory_api
from app.sous import api as sous_api
from app.telemetry import instrument_app, setup_telemetry
from app.workflows import api as workflows_api


class RequestIdMiddleware:
    """Sets the request id contextvar and echoes it in X-Request-ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = (
            incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        )
        request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "x-request-id" not in headers:
                    headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    get_settings()  # exits with a clear message if configuration (e.g. the master key) is invalid
    setup_telemetry("chefos-api")
    app = FastAPI(title="ChefOS API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    # --- router registration point: one line per module ---
    app.include_router(health.router)
    app.include_router(identity_api.router)
    app.include_router(integrations_api.router)
    app.include_router(commerce_api.router)
    app.include_router(inventory_api.router)
    app.include_router(sous_api.router)
    app.include_router(workflows_api.router)
    # -------------------------------------------------------

    app.add_middleware(RequestIdMiddleware)
    instrument_app(app)
    return app


app = create_app()
