"""Effective-permissions computation — the shared body behind the admin
``GET /users/{id}/effective-permissions`` and the self-scoped ``GET /me/permissions`` (S-web-3).

The caller's *candidate* key set (every key they hold any grant for) resolved one key at a time
against a single ``ResourceContext`` via the pure PDP — deny-by-default, deny-wins. This is the
NON-auditing decision path (the affordance view, not an enforcement point), so it calls
``authorize`` directly rather than the PEP's ``evaluate`` (which emits an audit row per probe).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.authz import RequestContext, ResourceContext, authorize
from .repository import gather_grants, granted_permission_keys


def resource_for_scope(level: str | None, scope_id: str | None) -> ResourceContext:
    """The ``ResourceContext`` a ``(scope_level, scope_id)`` query asks about. ``None``/``SYSTEM``
    yields the system resource; a narrower level binds its single selector."""
    if level is None or level == "SYSTEM":
        return ResourceContext.system()
    if level == "ARTIFACT":
        return ResourceContext(artifact_id=scope_id)
    if level == "PROCESS":
        return ResourceContext(process_ids=frozenset({scope_id}) if scope_id else frozenset())
    if level == "FOLDER":
        return ResourceContext(folder_path=scope_id)
    if level == "DOC_CLASS":
        return ResourceContext(document_level=scope_id)
    if level == "FRAMEWORK":
        return ResourceContext(framework_id=scope_id)
    return ResourceContext.system()


async def compute_effective_permissions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    scope_level: str | None,
    scope_id: str | None,
) -> dict[str, Any]:
    """The effective permission set for ``user_id`` at one scope: ``{scope, permissions:[…]}``.

    Each entry the PDP resolves to a concrete ALLOW or explicit-DENY is reported (a
    ``deny_by_default`` — held nowhere at this scope — is simply omitted)."""
    resource = resource_for_scope(scope_level, scope_id)
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    keys = await granted_permission_keys(session, user_id, org_id)

    permissions: list[dict[str, Any]] = []
    for key in sorted(keys):
        grants = await gather_grants(session, user_id, org_id, key)
        decision = authorize(grants, key, resource, ctx)
        if decision.reason in ("allow", "explicit_deny"):
            permissions.append(
                {
                    "key": key,
                    "effect": "ALLOW" if decision.allow else "DENY",
                    "source": decision.source,
                }
            )
    return {
        "scope": {
            "level": scope_level or "SYSTEM",
            "selector": {"scope_id": scope_id} if scope_id else None,
        },
        "permissions": permissions,
    }
