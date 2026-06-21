"""Server-computed register-head capability booleans (S-context-fe).

The faithful gate behind the register-steward console's Publish / Start-revision / Release
affordances: a single-axis FE ``/me/permissions`` probe CANNOT replicate the multi-axis release
scope (``_register_release_scope`` = artifact + folder_path + document_level + lifecycle_state +
SoD-2), so ``can_release`` is computed server-side via the pure PDP (``gather_grants`` +
``authorize(...).allow`` — NEVER ``enforce``, so a capability probe writes no authz-audit row; the
``_document_capabilities`` / ``_can_request_leadership_authorization`` pattern). Shared by
``api/context.py`` + ``api/risk.py`` so the two byte-identical register-status twins stay aligned
(closes the S-context-1 docs-P2 / S-risk-5 Codex-r2 residual for BOTH registers)."""

from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...domain.authz import RequestContext, ResourceContext, authorize
from .repository import gather_grants, gather_sod_constraints, get_allow_approver_release


async def register_capabilities(
    session: AsyncSession,
    caller: AppUser,
    *,
    release_scope: ResourceContext | None,
    source_ip: str | None,
) -> dict[str, bool]:
    """``{can_release, can_manage}`` for the caller over a register head.

    ``can_manage`` = ``register.manage`` @ SYSTEM (the steward probe; gates start-revision / publish
    + the FE "New" affordance) — independent of the head. ``can_release`` = ``document.release``
    over the head's multi-axis ``release_scope`` (artifact + SoD-2, the SAME scope the release
    endpoint enforces); ``None`` (no head) is ``False``. The SoD-2 ``RequestContext`` carries
    ``allow_approver_release`` (else the approver block evaluates wrong) and ``source_ip`` so an
    ``ip_allow`` grant evaluates as the action endpoint's enforce does (CX-1; else over-strict)."""
    now = datetime.datetime.now(datetime.UTC)

    manage_ctx = RequestContext(now=now, source_ip=source_ip, actor_user_id=str(caller.id))
    manage_grants = await gather_grants(session, caller.id, caller.org_id, "register.manage")
    can_manage = authorize(
        manage_grants, "register.manage", ResourceContext.system(), manage_ctx
    ).allow

    can_release = False
    if release_scope is not None:
        sod = await gather_sod_constraints(session, caller.org_id)
        allow_approver_release = await get_allow_approver_release(session, caller.org_id)
        release_ctx = RequestContext(
            now=now,
            source_ip=source_ip,
            actor_user_id=str(caller.id),
            allow_approver_release=allow_approver_release,
        )
        release_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
        can_release = authorize(
            release_grants,
            "document.release",
            release_scope,
            release_ctx,
            sig_hook=True,
            sod=sod,
        ).allow

    return {"can_release": can_release, "can_manage": can_manage}
