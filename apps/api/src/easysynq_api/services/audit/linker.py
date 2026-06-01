"""The decoupled audit chain-linker (slice S6, R12, doc 12 §4.4).

Walks the as-yet-unchained ``audit_event`` rows in ``id`` order (per org, one chain spanning all
monthly partitions), computes ``prev_hash``/``row_hash`` over :func:`canonical_serialize`, and
stamps ``chained_at`` — the ONLY post-insert mutation the schema permits. This is decoupled from the
write path so per-org throughput is never gated by chain-tail contention; tamper-evidence is fully
preserved (any gap/edit/reorder still breaks the chain once linked).

Runs single-threaded under a PG advisory lock as a dedicated ``easysynq_linker`` role — the ONLY
role granted ``UPDATE(prev_hash, row_hash, chained_at)`` on ``audit_event`` (the app role has no
UPDATE there, which is what makes the trail append-only — AC#6a). A bounded-lag alarm fires if the
oldest unchained row is older than ``system_config.audit_chain_lag_alarm_seconds`` (target ≤5 s).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.audit_event import AuditEvent
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from .canonical import GENESIS_HASH, audit_row_from_orm, compute_row_hash

logger = logging.getLogger("easysynq.audit.linker")

_BATCH = 500


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclasses.dataclass(frozen=True, slots=True)
class LinkResult:
    linked: int
    lag_seconds: float
    alarm: bool


async def _seed_prev_hash(session: AsyncSession, org_id: object) -> bytes:
    """The prev_hash to start from: the last already-linked row's row_hash, else genesis."""
    row_hash = (
        await session.execute(
            select(AuditEvent.row_hash)
            .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_not(None))
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row_hash if row_hash is not None else GENESIS_HASH


async def link_org_chain(session: AsyncSession, org_id: object) -> int:
    """Link all unchained rows for one org in ``id`` order. Commits per batch (the advisory lock is
    session-level, so it persists across the commits). Returns the number of rows linked."""
    prev = await _seed_prev_hash(session, org_id)
    linked = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_(None))
                    .order_by(AuditEvent.id.asc())
                    .limit(_BATCH)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        now = _now()
        for event in rows:
            digest = compute_row_hash(audit_row_from_orm(event), prev)
            event.prev_hash = prev
            event.row_hash = digest
            event.chained_at = now
            prev = digest
            linked += 1
        await session.commit()
        if len(rows) < _BATCH:
            break
    return linked


async def _lag_seconds(session: AsyncSession) -> float:
    """now minus the oldest unchained row's occurred_at (0 if the tail is fully linked). A
    persistent, growing value means the linker has stalled (doc 12 §4.4)."""
    oldest = (
        await session.execute(
            select(func.min(AuditEvent.occurred_at)).where(AuditEvent.chained_at.is_(None))
        )
    ).scalar_one_or_none()
    if oldest is None:
        return 0.0
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=datetime.UTC)
    return max(0.0, (_now() - oldest).total_seconds())


async def _lag_threshold(session: AsyncSession) -> int:
    threshold = (
        await session.execute(select(func.min(SystemConfig.audit_chain_lag_alarm_seconds)))
    ).scalar_one_or_none()
    return int(threshold) if threshold is not None else 60


async def link_all(session: AsyncSession) -> LinkResult:
    """Link every org's chain, then evaluate the bounded-lag alarm (doc 12 §4.4)."""
    org_ids = (await session.execute(select(Organization.id))).scalars().all()
    linked = 0
    for org_id in org_ids:
        linked += await link_org_chain(session, org_id)
    lag = await _lag_seconds(session)
    threshold = await _lag_threshold(session)
    alarm = lag > threshold
    if alarm:
        logger.error(
            "audit.chain_linker.lag_alarm",
            extra={"extra_fields": {"lag_seconds": lag, "threshold_seconds": threshold}},
        )
    return LinkResult(linked=linked, lag_seconds=lag, alarm=alarm)
