"""The resilient runtime single-org lookup (the 0038/0043/0045 lesson, at runtime).

An OPERATIONAL install renames ``short_code`` away from ``'DEFAULT'`` at setup G-E (this live
install: ``AHT``), so a bare ``short_code='DEFAULT'`` lookup aborts. D1 = single-org: fall back to
the only row. Returns ``None`` when the org is unresolvable — pre-setup (zero orgs) AND the
can't-happen-under-D1 multi-org case with no DEFAULT (deliberately fail-soft, never a guess) —
callers skip persistence rather than crash (the mirror sync/scan must work on an empty install).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.organization import Organization


async def get_single_org_id(session: AsyncSession) -> uuid.UUID | None:
    org_id = (
        await session.execute(select(Organization.id).where(Organization.short_code == "DEFAULT"))
    ).scalar_one_or_none()
    if org_id is not None:
        return org_id
    rows = (await session.execute(select(Organization.id))).scalars().all()
    return rows[0] if len(rows) == 1 else None
