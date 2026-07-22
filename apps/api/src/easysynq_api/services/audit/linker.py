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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.audit_event import AuditEvent
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from .canonical import GENESIS_HASH, audit_row_from_orm, compute_row_hash
from .watermark import WatermarkState, advance_watermark

logger = logging.getLogger("easysynq.audit.linker")

_BATCH = 500
_ID_WINDOW = 5000  # visible ids fetched above the watermark per tick (bounds the per-tick scan)


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


async def link_org_chain(session: AsyncSession, org_id: object, max_id: int) -> int:
    """Link one org's unchained rows in ``id`` order, but ONLY those with ``id <= max_id`` (the
    proven safe watermark — CR-2, so a higher id is never linked ahead of a lower one still
    uncommitted). Commits per batch (the advisory lock is session-level, so it persists across the
    commits). Returns the number of rows linked."""
    prev = await _seed_prev_hash(session, org_id)
    linked = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.chained_at.is_(None),
                        AuditEvent.id <= max_id,
                    )
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
    persistent, growing value means the linker has stalled (doc 12 §4.4). ⚠ Under the CR-2 safe
    prefix a visible row held above an in-flight gap (a lower id still uncommitted in a long sweep)
    also counts here — the intended signal (the tail genuinely cannot advance), transient, and it
    clears when the blocking txn commits; do NOT narrow this to id <= W (that would hide real
    above-watermark stalls)."""
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


async def _load_cursor(session: AsyncSession) -> WatermarkState:
    """The linker's persisted safe-prefix cursor (the singleton ``audit_chain_cursor`` seeded by
    migration 0071). A missing row falls back to a genesis state (defensive)."""
    row = (
        await session.execute(
            text(
                "SELECT safe_watermark, stall_xmax, stall_ceiling"
                " FROM audit_chain_cursor WHERE id = 1"
            )
        )
    ).one_or_none()
    if row is None:  # pragma: no cover — migration 0071 seeds the singleton
        return WatermarkState(0, None, None)
    return WatermarkState(
        int(row.safe_watermark),
        int(row.stall_xmax) if row.stall_xmax is not None else None,
        int(row.stall_ceiling) if row.stall_ceiling is not None else None,
    )


async def _save_cursor(session: AsyncSession, state: WatermarkState) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_chain_cursor"
            " (id, safe_watermark, stall_xmax, stall_ceiling, updated_at)"
            " VALUES (1, :w, :x, :c, now())"
            " ON CONFLICT (id) DO UPDATE SET"
            " safe_watermark = EXCLUDED.safe_watermark,"
            " stall_xmax = EXCLUDED.stall_xmax,"
            " stall_ceiling = EXCLUDED.stall_ceiling,"
            " updated_at = now()"
        ),
        {"w": state.watermark, "x": state.stall_xmax, "c": state.stall_ceiling},
    )


async def link_all(session: AsyncSession) -> LinkResult:
    """Link every org's chain up to the PROVEN safe-prefix watermark, then evaluate the bounded-lag
    alarm (doc 12 §4.4). The watermark (services/audit/watermark) advances only over a contiguous
    id-prefix whose members are all decided, so a higher id is never linked ahead of a lower one
    still uncommitted in a long sweep — the reorder that permanently breaks verify_chain (CR-2)."""
    prior = await _load_cursor(session)
    # The id window above the watermark AND the snapshot bounds, read in ONE statement so the
    # ids' visibility and the xmin/xmax share ONE snapshot (the rollback proof depends on a
    # gap's absence and the snapshot xids being mutually consistent).
    rows = (
        await session.execute(
            text(
                "SELECT a.id AS id,"
                " pg_snapshot_xmin(pg_current_snapshot())::text AS xmin,"
                " pg_snapshot_xmax(pg_current_snapshot())::text AS xmax"
                " FROM audit_event a WHERE a.id > :w ORDER BY a.id ASC LIMIT :lim"
            ),
            {"w": prior.watermark, "lim": _ID_WINDOW},
        )
    ).all()
    if rows:
        ids_above = [int(r.id) for r in rows]
        snap_xmin, snap_xmax = int(rows[0].xmin), int(rows[0].xmax)
    else:
        ids_above, snap_xmin, snap_xmax = [], 0, 0
    step = advance_watermark(prior, ids_above=ids_above, snap_xmin=snap_xmin, snap_xmax=snap_xmax)

    org_ids = (await session.execute(select(Organization.id))).scalars().all()
    linked = 0
    for org_id in org_ids:
        linked += await link_org_chain(session, org_id, step.link_up_to)
    await _save_cursor(session, step.state)
    await session.commit()

    lag = await _lag_seconds(session)
    threshold = await _lag_threshold(session)
    alarm = lag > threshold
    if alarm:
        logger.error(
            "audit.chain_linker.lag_alarm",
            extra={"extra_fields": {"lag_seconds": lag, "threshold_seconds": threshold}},
        )
    return LinkResult(linked=linked, lag_seconds=lag, alarm=alarm)
