"""RFC 9457 application/problem+json error model and FastAPI handlers.

HTTP status is authoritative; ``code`` is the stable machine string clients branch
on. The canonical code set mirrors ``packages/contracts/openapi.yaml`` and doc 15 §4.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import request_id_var

PROBLEM_MEDIA_TYPE = "application/problem+json"
_TYPE_BASE = "https://errors.easysynq.local/"


class ProblemException(Exception):
    """Raise to return a problem+json response with a canonical ``code``."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors
        super().__init__(title)


def _body(
    *, status: int, code: str, title: str, instance: str,
    detail: str | None = None, errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"{_TYPE_BASE}{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": instance,
        "request_id": request_id_var.get(),
    }
    if detail is not None:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def _problem(request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=exc.status, code=exc.code, title=exc.title,
                instance=str(request.url.path), detail=exc.detail, errors=exc.errors,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", [])), "code": e.get("type", ""),
             "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=422, code="validation_error", title="Request failed validation",
                instance=str(request.url.path), detail=f"{len(errors)} field(s) invalid.",
                errors=errors,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "internal_error"
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=exc.status_code, code=code, title=str(exc.detail),
                instance=str(request.url.path),
            ),
        )
