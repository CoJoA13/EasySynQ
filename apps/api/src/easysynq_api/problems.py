"""RFC 9457 application/problem+json error model and FastAPI handlers.

HTTP status is authoritative; ``code`` is the stable machine string clients branch
on. The canonical code set mirrors ``packages/contracts/openapi.yaml`` and doc 15 §4.
"""

from __future__ import annotations

from typing import Any, cast

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
    *,
    status: int,
    code: str,
    title: str,
    instance: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
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
    from .domain.vault.lifecycle import IllegalTransition

    @app.exception_handler(IllegalTransition)
    async def _illegal_transition(request: Request, exc: Exception) -> JSONResponse:
        illegal = cast(IllegalTransition, exc)
        body = _body(
            status=409,
            code="invalid_state_transition",
            title=f"Illegal lifecycle transition: {illegal.action.value}",
            instance=str(request.url.path),
            detail=f"not legal from current_state={illegal.doc_state.value}",
        )
        # allowed_transitions lets the client correct without guessing (doc 15 §4).
        body["allowed_transitions"] = illegal.allowed
        return JSONResponse(status_code=409, media_type=PROBLEM_MEDIA_TYPE, content=body)

    @app.exception_handler(ProblemException)
    async def _problem(request: Request, exc: Exception) -> JSONResponse:
        problem = cast(ProblemException, exc)
        return JSONResponse(
            status_code=problem.status,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=problem.status,
                code=problem.code,
                title=problem.title,
                instance=str(request.url.path),
                detail=problem.detail,
                errors=problem.errors,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: Exception) -> JSONResponse:
        validation = cast(RequestValidationError, exc)
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", [])),
                "code": err.get("type", ""),
                "message": err.get("msg", ""),
            }
            for err in validation.errors()
        ]
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=422,
                code="validation_error",
                title="Request failed validation",
                instance=str(request.url.path),
                detail=f"{len(errors)} field(s) invalid.",
                errors=errors,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: Exception) -> JSONResponse:
        http = cast(StarletteHTTPException, exc)
        code = "not_found" if http.status_code == 404 else "internal_error"
        return JSONResponse(
            status_code=http.status_code,
            media_type=PROBLEM_MEDIA_TYPE,
            content=_body(
                status=http.status_code,
                code=code,
                title=str(http.detail),
                instance=str(request.url.path),
            ),
        )
