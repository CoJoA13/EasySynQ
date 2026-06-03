"""Audit-chain verification (slice S6, AC#6b, doc 12 §4.4).

Re-walks one org's linked rows (``chained_at IS NOT NULL``) in ``id`` order, recomputes each
``row_hash`` over :func:`canonical_serialize`, and confirms ``prev_hash[n] == row_hash[n-1]``. A
mutated row changes its recomputed ``row_hash`` (≠ the stored one) and is reported as the **first
broken link**; a deletion/reorder breaks a ``prev_hash`` link. The unchained tail
(``chained_at IS NULL``) is reported as ``pending``, never a break. Backs the on-demand
``GET /audit-events/verify-chain`` and the nightly Beat job.

The walk is **per-org** (``Each org maintains an ordered chain`` — doc 12 §4.3): a global walk
would both leak cross-org rows and falsely break at every org boundary (the linker resets prev_hash
per org). The endpoint passes ``caller.org_id``; the Beat job + CLI iterate every org.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.audit_event import AuditEvent
from .canonical import GENESIS_HASH, audit_row_from_orm, compute_row_hash


@dataclasses.dataclass(frozen=True, slots=True)
class ChainBreak:
    at_id: int
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyResult:
    verified: bool
    checked: int
    pending: int
    breaks: list[ChainBreak]


async def verify_chain(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    from_id: int | None = None,
    to_id: int | None = None,
    version: int = 1,
) -> VerifyResult:
    """Verify ``org_id``'s linked chain (optionally bounded to ``[from_id, to_id]``). Reports every
    broken link found, the first being the root cause (a mutated/deleted/reordered row).

    ``version`` selects the canonical_serialize spec version to recompute against; the S11 restore
    re-verify reads it from the RESTORED ``system_config.canonical_serialize_version`` rather than
    hardcoding 1, so a future v2 chain verifies under its own spec (R12/D-4)."""
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_not(None))
        .order_by(AuditEvent.id.asc())
    )
    if from_id is not None:
        stmt = stmt.where(AuditEvent.id >= from_id)
    if to_id is not None:
        stmt = stmt.where(AuditEvent.id <= to_id)
    rows = (await session.execute(stmt)).scalars().all()

    # Seed the walking prev_hash. For a full walk it is genesis; for a bounded walk that does not
    # start at the chain head, seed from the row immediately before ``from_id`` so a legitimate
    # window is not mis-flagged as a break at its first row.
    prev = GENESIS_HASH
    if from_id is not None and rows:
        before = (
            await session.execute(
                select(AuditEvent.row_hash)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.chained_at.is_not(None),
                    AuditEvent.id < rows[0].id,
                )
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if before is not None:
            prev = before

    breaks: list[ChainBreak] = []
    for event in rows:
        stored = event.row_hash or b""
        recomputed = compute_row_hash(
            audit_row_from_orm(event), event.prev_hash or GENESIS_HASH, version=version
        )
        if recomputed != stored:
            breaks.append(ChainBreak(at_id=event.id, reason="row_hash mismatch (row mutated)"))
        elif (event.prev_hash or GENESIS_HASH) != prev:
            breaks.append(
                ChainBreak(at_id=event.id, reason="prev_hash mismatch (chain reorder/deletion)")
            )
        prev = stored

    pending = (
        await session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_(None))
        )
    ).scalar_one()

    return VerifyResult(verified=not breaks, checked=len(rows), pending=int(pending), breaks=breaks)
