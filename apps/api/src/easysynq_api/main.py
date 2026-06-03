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

from .api.audit import router as audit_router
from .api.auth import router as auth_router
from .api.authz import router as authz_router
from .api.clauses import router as clauses_router
from .api.documents import router as documents_router
from .api.health import router as health_router
from .api.setup import router as setup_router
from .api.users import router as users_router
from .api.verify import router as verify_router
from .api.workflow import router as workflow_router
from .config import get_settings
from .db.models.system_config import SetupState
from .db.session import dispose_engine, get_sessionmaker
from .logging import configure_logging, request_id_var
from .problems import problem_response, register_exception_handlers
from .services.setup import get_setup_state

# Paths reachable while the setup latch is closed (setup_state != OPERATIONAL): the wizard itself,
# the auth config + identity it needs to load, and the public verify page + dev docs. Everything
# else under /api/v1/ is 423 until setup finalizes (doc 08 §2; 423 per doc 18 §7). Non-/api/v1 paths
# (/healthz, /readyz, the SPA, Keycloak) are never guarded here.
#
# Exemptions are BOUNDARY-ANCHORED (not bare prefixes): the single-endpoint exemptions match
# exactly, and only the /setup tree is a prefix — so a future sibling route (e.g. /api/v1/members,
# /api/v1/metrics) can never be silently un-latched by a startswith collision with /api/v1/me.
_LATCH_EXEMPT_EXACT: frozenset[str] = frozenset(
    {
        "/api/v1/auth/config",
        "/api/v1/me",
        "/api/v1/verify",
        "/api/v1/openapi.json",
        "/api/v1/docs",
    }
)
_LATCH_EXEMPT_SETUP = "/api/v1/setup"


def _latch_exempt(path: str) -> bool:
    return (
        path in _LATCH_EXEMPT_EXACT
        or path == _LATCH_EXEMPT_SETUP
        or path.startswith(_LATCH_EXEMPT_SETUP + "/")
    )


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

    @app.middleware("http")
    async def setup_latch_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Lock the QMS surface until first-run setup finalizes (doc 08 §2). Guards ``/api/v1/*``
        (minus the wizard/auth/verify exemptions) with **423 ``setup_incomplete``** while
        ``setup_state != OPERATIONAL``. Queries the singleton state per request — one indexed PK
        lookup; a one-way app-state cache is a safe later optimization."""
        path = request.url.path
        if path.startswith("/api/v1/") and not _latch_exempt(path):
            async with get_sessionmaker()() as session:
                state = await get_setup_state(session)
            if state is not SetupState.OPERATIONAL:
                return problem_response(
                    request,
                    status=423,
                    code="setup_incomplete",
                    title="First-run setup is not complete",
                    detail="The QMS is locked until setup finalizes. Open /setup to continue.",
                )
        return await call_next(request)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(setup_router)  # S8a: first-run wizard (latch-exempt)
    app.include_router(authz_router)
    app.include_router(users_router)  # S8d: user-lifecycle admin (roster / invite / enable-disable)
    app.include_router(documents_router)
    app.include_router(clauses_router)  # S9: read-only ISO clause spine (GET /clauses)
    app.include_router(workflow_router)
    app.include_router(audit_router)
    app.include_router(verify_router)  # S7c: public controlled-rendition verify page (no auth)
    return app


app = create_app()
