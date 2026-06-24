"""Read-scope audience resolver (slice S-notify-5a, doc 10 §9.2).

The inverse of an access check: given a document, who may read it? Used by the awareness fan-out to
target doc.released at exactly the document.read holders. This is the ONLY ABAC-correct answer —
DENY overrides and time-windowed predicates are not join-expressible (spec §4) — so it is the
per-user PDP loop, reusing the exact path every request takes. Resolved at fan-out time (an
R32-bounded residual, spec §4/§15: not re-verified at later digest send).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser, UserStatus
from ...domain.authz import RequestContext, authorize
from .repository import gather_grants
from .resource import build_document_resource_context

# Mirror recipients.py / auth dependencies: a deactivated user is never an awareness recipient.
_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}
_READ = "document.read"


async def resolve_document_readers(
    session: AsyncSession,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    now: datetime.datetime,
) -> list[uuid.UUID]:
    """All ACTIVE users in ``org_id`` who can read ``doc_id`` per the real PDP (deny-wins,
    ABAC-complete), evaluated at ``now``. source_ip is None (worker has no request IP), so an
    ip_allow-gated grant fails to match → fail-safe under-inclusion (spec §4)."""
    resource = await build_document_resource_context(session, doc_id)
    user_ids = (
        (
            await session.execute(
                select(AppUser.id).where(
                    AppUser.org_id == org_id,
                    AppUser.status.notin_(_INACTIVE),
                )
            )
        )
        .scalars()
        .all()
    )
    ctx = RequestContext(now=now)  # source_ip=None, step_up_satisfied=True, actor_user_id=None
    readers: list[uuid.UUID] = []
    for uid in user_ids:
        grants = await gather_grants(session, uid, org_id, _READ)
        if authorize(grants, _READ, resource, ctx).allow:
            readers.append(uid)
    return readers
