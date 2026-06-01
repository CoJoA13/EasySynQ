"""The Policy Enforcement Point — the FastAPI seam in front of every gated route.

``require(permission_key, scope_resolver)`` returns a dependency that gathers the caller's
grants, runs the pure PDP, **emits an audit hook for the decision (allow or deny)**, and
raises ``403 permission_denied`` on deny. ``assert_can_grant`` is the two-tier guard (R35):
a content-tier ``permission.grant`` holder cannot grant a system-domain permission (422).
``invalidate_user_permissions`` is the revoke-next-request epoch seam.
"""

from __future__ import annotations

import contextlib
import datetime
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user
from ...config import get_settings
from ...db.models.app_user import AppUser
from ...db.session import get_session
from ...domain.authz import RequestContext, ResourceContext, authorize
from ...domain.authz.types import Decision, Effect
from ...logging import request_id_var
from ...problems import ProblemException
from .audit import AuthzAuditEvent, AuthzAuditSink, LoggingAuthzAuditSink
from .repository import gather_grants, get_permission, role_system_domain_keys

ScopeResolver = Callable[[Request], ResourceContext]

_default_sink: AuthzAuditSink = LoggingAuthzAuditSink()


def get_authz_audit_sink() -> AuthzAuditSink:
    """FastAPI dependency for the audit sink — overridden in tests with a capturing sink."""
    return _default_sink


def _system_scope(_request: Request) -> ResourceContext:
    return ResourceContext.system()


def _scope_ref(resource: ResourceContext) -> str:
    if resource.artifact_id:
        return f"artifact:{resource.artifact_id}"
    if resource.folder_path:
        return f"folder:{resource.folder_path}"
    if resource.process_ids:
        return f"process:{','.join(sorted(resource.process_ids))}"
    if resource.document_level:
        return f"doc_class:{resource.document_level}"
    return "SYSTEM"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def evaluate(
    session: AsyncSession,
    sink: AuthzAuditSink,
    request: Request,
    user: AppUser,
    permission_key: str,
    resource: ResourceContext,
    *,
    sig_hook: bool = False,
) -> Decision:
    """Resolve grants, run the PDP, and emit the audit hook (allow AND deny)."""
    grants = await gather_grants(session, user.id, user.org_id, permission_key)
    ctx = RequestContext(now=_now(), source_ip=request.client.host if request.client else None)
    decision = authorize(grants, permission_key, resource, ctx, sig_hook=sig_hook)
    sink.record(
        AuthzAuditEvent(
            occurred_at=ctx.now,
            actor_id=str(user.id),
            org_id=str(user.org_id),
            permission_key=permission_key,
            decision="allow" if decision.allow else "deny",
            reason=decision.reason,
            scope_ref=_scope_ref(resource),
            source=decision.source,
            request_id=request_id_var.get(),
        )
    )
    return decision


def require(
    permission_key: str,
    scope_resolver: ScopeResolver = _system_scope,
    *,
    sig_hook: bool = False,
) -> Callable[..., Awaitable[AppUser]]:
    """Build a dependency that enforces ``permission_key`` and returns the caller on allow."""

    async def _dependency(
        request: Request,
        user: AppUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
        sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    ) -> AppUser:
        resource = scope_resolver(request)
        decision = await evaluate(
            session, sink, request, user, permission_key, resource, sig_hook=sig_hook
        )
        if not decision.allow:
            raise ProblemException(
                status=403,
                code="permission_denied",
                title="Permission denied",
                detail=f"{permission_key} on {_scope_ref(resource)}",
            )
        return user

    return _dependency


async def _is_system_tier(session: AsyncSession, granter: AppUser) -> bool:
    """True if the grantor holds a system-tier ``permission.grant`` (an Admin) rather than a
    content-only one (the QMS Owner, whose grant carries the ``content_only`` marker)."""
    grants = await gather_grants(session, granter.id, granter.org_id, "permission.grant")
    return any(
        g.effect is Effect.ALLOW and not (g.predicates or {}).get("content_only") for g in grants
    )


def _two_tier_deny(sink: AuthzAuditSink, granter: AppUser, scope_ref: str, detail: str) -> None:
    sink.record(
        AuthzAuditEvent(
            occurred_at=_now(),
            actor_id=str(granter.id),
            org_id=str(granter.org_id),
            permission_key="permission.grant",
            decision="deny",
            reason="two_tier_violation",
            scope_ref=scope_ref,
            source=None,
            request_id=request_id_var.get(),
        )
    )
    raise ProblemException(
        status=422,
        code="two_tier_violation",
        title="System-domain permission requires system-tier authority",
        detail=detail,
    )


async def assert_can_grant(
    session: AsyncSession,
    sink: AuthzAuditSink,
    granter: AppUser,
    target_permission_key: str,
) -> None:
    """Two-tier grant guard (R35) for a single permission override. A content-tier
    ``permission.grant`` holder (e.g. the QMS Owner) may grant CONTENT permissions but not
    system-domain ones — the latter requires a system-tier ``permission.grant`` (an Admin).
    Assumes the caller already passed ``require("permission.grant")``."""
    target = await get_permission(session, target_permission_key)
    if target is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unknown permission",
            detail=f"No such permission: {target_permission_key}",
        )
    if not target.is_system_domain:
        return  # CONTENT target — any permission.grant holder may grant it.
    if not await _is_system_tier(session, granter):
        _two_tier_deny(
            sink,
            granter,
            f"target:{target_permission_key}",
            f"{target_permission_key} is a system-administration permission and "
            "cannot be granted by a content-tier grantor",
        )


async def assert_can_assign_role(
    session: AsyncSession, sink: AuthzAuditSink, granter: AppUser, role_id: uuid.UUID
) -> None:
    """Two-tier guard (R35) for role assignment: a content-tier grantor cannot assign a role
    that bundles any system-domain permission (e.g. System Administrator)."""
    system_keys = await role_system_domain_keys(session, role_id)
    if not system_keys:
        return
    if not await _is_system_tier(session, granter):
        _two_tier_deny(
            sink,
            granter,
            f"role:{role_id}",
            "role bundles system-administration permissions "
            f"({', '.join(sorted(system_keys))}) and requires a system-tier grantor",
        )


async def invalidate_user_permissions(user_id: uuid.UUID) -> None:
    """Best-effort permissions-epoch bump — the revoke-takes-effect-next-request seam.

    S2 resolves grants from the DB on every request, so revocation is already immediate;
    nothing reads this epoch yet. The Redis-backed effective-permission cache that *does*
    read it lands with later perf/audit work (doc 18 §5.2). Never blocks a grant mutation.
    """
    # The cache is an optimization; a Redis hiccup must never fail a grant mutation.
    with contextlib.suppress(Exception):
        import redis.asyncio as aioredis

        client = aioredis.from_url(get_settings().redis_url)  # type: ignore[no-untyped-call]
        try:
            await client.incr(f"perm_epoch:{user_id}")
        finally:
            await client.aclose()
