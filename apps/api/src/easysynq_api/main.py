"""FastAPI application factory.

Wires structured logging, the request-id middleware, the RFC 9457 problem
handlers, and the routers. Later slices mount the auth, document, version, lock,
task, audit, and search routers under /api/v1.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from .api.health import router as health_router
from .config import get_settings
from .db.session import dispose_engine
from .logging import configure_logging, request_id_var
from .problems import register_exception_handlers


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="EasySynQ API",
        version=settings.version,
        lifespan=lifespan,
        # The published contract is packages/contracts/openapi.yaml (spec-first);
        # the FastAPI-emitted schema is for interactive docs only.
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

    register_exception_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
