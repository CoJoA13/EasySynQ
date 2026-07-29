"""Organization-scoped build-versus-legal-erasure serialization for Evidence Packs.

Pack build stages take the shared side; an R27 approval takes the exclusive side. The advisory
lock is transaction-scoped, so PostgreSQL releases it on commit, rollback, or connection loss.
Callers must take this lock before any pack row lock:

* shared lock -> pack row for Stage 1/2;
* exclusive lock -> destroy request/source Record -> affected pack rows for R27.

That fixed order lets concurrent builds proceed while making the rare legal erasure a complete
cut through every pack copy in an organization.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# "ESPK" as a signed-int-safe namespace. PostgreSQL's two-int advisory-lock form keeps this
# exclusion independent from every other advisory-lock family in the application.
_PACK_ARTIFACT_LOCK_NAMESPACE = 0x4553504B


async def _org_lock_key(session: AsyncSession, org_id: uuid.UUID) -> int:
    key = await session.scalar(select(func.hashtext(str(org_id))))
    if key is None:  # PostgreSQL hashtext(non-NULL text) is total; fail closed on drift.
        raise RuntimeError("PostgreSQL returned no Evidence Pack advisory lock key")
    return int(key)


async def lock_pack_build_shared(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Hold the shared pack-artifact lock through the caller's transaction."""
    key = await _org_lock_key(session, org_id)
    await session.execute(
        select(func.pg_advisory_xact_lock_shared(_PACK_ARTIFACT_LOCK_NAMESPACE, key))
    )


async def lock_pack_erasure_exclusive(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Hold the exclusive pack-artifact lock through the caller's R27 transaction."""
    key = await _org_lock_key(session, org_id)
    await session.execute(select(func.pg_advisory_xact_lock(_PACK_ARTIFACT_LOCK_NAMESPACE, key)))
