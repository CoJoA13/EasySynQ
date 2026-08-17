"""RFC 9457 application/problem+json error model and FastAPI handlers.

HTTP status is authoritative; ``code`` is the stable machine string clients branch
on. The canonical code set mirrors ``packages/contracts/openapi.yaml`` and doc 15 §4.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import request_id_var

PROBLEM_MEDIA_TYPE = "application/problem+json"
_TYPE_BASE = "https://errors.easysynq.local/"

# The stable top-level problem vocabulary. Keep nested ``errors[].code`` values unconstrained:
# those include field-specific and Pydantic validation codes, not client routing keys.
type ProblemCode = Literal[
    "ack_obligation_lapsed",
    "ack_superseded",
    "already_archived",
    "already_authorized",
    "already_disposed",
    "already_on_hold",
    "audit_close_blocked",
    "audit_finding_audit_closed",
    "auth_unavailable",
    "authorization_in_progress",
    "backup_destination_invalid",
    "backup_destination_unreachable",
    "backup_not_configured",
    "bootstrap_administrator_exists",
    "bootstrap_already_consumed",
    "bootstrap_credential_superseded",
    "bootstrap_expired",
    "bootstrap_identity_bound",
    "bootstrap_invalid",
    "bootstrap_not_ready",
    "capa_already_spawned",
    "capa_approval_in_progress",
    "capa_close_incomplete",
    "capa_not_verified",
    "capa_terminal",
    "commit_blocked",
    "compliance_mode_denies_destroy",
    "conflict",
    "create_target_managed_subtype",
    "create_target_not_new",
    "dcr_approval_in_progress",
    "dcr_approver_conflict",
    "dcr_effectivity_pending",
    "dcr_impact_not_editable",
    "dcr_no_approvers",
    "dcr_not_assessable",
    "dcr_not_cancellable",
    "dcr_not_closable",
    "dcr_not_editable",
    "dcr_not_implementable",
    "dcr_not_in_approval",
    "dcr_not_routable",
    "dependency_unavailable",
    "document_not_approved",
    "dual_control_same_actor",
    "evidence_frozen",
    "finding_already_corrected",
    "finding_not_improvable",
    "finding_superseded",
    "impact_not_assessed",
    "improvement_not_editable",
    "improvement_transition_invalid",
    "initiative_not_authorizable",
    "internal_error",
    "invalid_audit_transition",
    "invalid_capa_transition",
    "invalid_class",
    "invalid_hour",
    "invalid_mode",
    "invalid_quiet_hours",
    "invalid_state_transition",
    "invalid_time",
    "invalid_timezone",
    "invalid_transition",
    "keycloak_email_exists",
    "keycloak_not_configured",
    "keycloak_unavailable",
    "keycloak_username_exists_unlinked",
    "last_admin",
    "leadership_authorization_required",
    "legal_hold_active",
    "lock_conflict",
    "name_taken",
    "ncr_already_dispositioned",
    "no_approved_draft",
    "no_bootstrap_secret",
    "no_controlled_rendition",
    "not_a_leadership_artifact",
    "not_archived",
    "not_editable",
    "not_found",
    "not_on_hold",
    "not_open",
    "obsoletion_blocked",
    "on_legal_hold",
    "output_not_actionable",
    "output_not_improvable",
    "pack_evidence_destroyed",
    "pack_unavailable",
    "permission_denied",
    "program_archived",
    "rate_limited",
    "rendition_pending",
    "retain_permanent",
    "review_close_blocked",
    "review_not_open_to_close",
    "review_not_tracking",
    "revision_chain_reconstruction_unsupported",
    "role_missing",
    "role_not_seeded",
    "setup_already_complete",
    "setup_gates_unsatisfied",
    "setup_incomplete",
    "setup_not_initialized",
    "signing_key_unavailable",
    "sod_self_disposition",
    "sod_self_verify",
    "sod_violation",
    "source_bytes_in_foreign_bucket",
    "staged_source_unavailable",
    "staging_version_required",
    "step_up_required",
    "storage_unavailable",
    "system_default_protected",
    "system_policy_protected",
    "target_kind_deferred",
    "token_expired",
    "token_invalid",
    "two_tier_violation",
    "unauthenticated",
    "unknown_filter",
    "upload_identity_mismatch",
    "use_legal_hold_endpoint",
    "user_exists",
    "user_not_linked",
    "validation_error",
    "version_already_linked",
    "version_not_approved",
    "wal_pitr_unavailable",
    "worm_destroy_request_open",
    "worm_lock_unexpired",
    "worm_not_enforced",
    "worm_required",
]


class ProblemException(Exception):
    """Raise to return a problem+json response with a canonical ``code``."""

    def __init__(
        self,
        *,
        status: int,
        code: ProblemCode,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        members: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors
        # Extra top-level problem members (RFC 9457 §3.2) — e.g. ``conflicting_duty`` on a
        # ``sod_violation`` so the client can correct without guessing (doc 15 §9.5).
        self.members = members
        super().__init__(title)


def _body(
    *,
    status: int,
    code: ProblemCode,
    title: str,
    instance: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    members: dict[str, Any] | None = None,
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
    if members:
        body.update(members)
    return body


def problem_response(
    request: Request, *, status: int, code: ProblemCode, title: str, detail: str | None = None
) -> JSONResponse:
    """Build a problem+json ``JSONResponse`` directly — for code paths OUTSIDE the exception
    handlers (e.g. the setup-latch middleware, which runs before routing/handlers)."""
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        content=_body(
            status=status, code=code, title=title, instance=str(request.url.path), detail=detail
        ),
    )


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
                members=problem.members,
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
        code: ProblemCode = "not_found" if http.status_code == 404 else "internal_error"
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
