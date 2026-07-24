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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.audit_checkpoint import AuditCheckpoint
from ...db.models.audit_event import AuditEvent
from .canonical import GENESIS_HASH, audit_row_from_orm, compute_row_hash
from .checkpoint import verify_checkpoint_signature


@dataclasses.dataclass(frozen=True, slots=True)
class ChainBreak:
    at_id: int
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointStatus:
    """The signed-checkpoint attestation outcome for a full-chain verify (doc 12 §4.4). ``present``
    is False when nothing is anchored yet (not a tamper — the off-host read catches a wholesale
    deletion). When present, ``signature_ok`` (Ed25519 over the payload) and ``hash_match`` (signed
    ``latest_row_hash`` == the chain's stored hash at ``latest_id``) must both hold; either being
    False is a tamper the self-consistent chain walk alone cannot see."""

    present: bool
    signature_ok: bool | None
    hash_match: bool | None
    latest_id: int | None
    reason: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyResult:
    verified: bool
    checked: int
    pending: int
    breaks: list[ChainBreak]
    checkpoint: CheckpointStatus | None = None


async def verify_chain(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    from_id: int | None = None,
    to_id: int | None = None,
    version: int = 1,
    verify_key: Ed25519PublicKey | None = None,
) -> VerifyResult:
    """Verify ``org_id``'s linked chain (optionally bounded to ``[from_id, to_id]``). Reports every
    broken link found, the first being the root cause (a mutated/deleted/reordered row).

    ``version`` selects the canonical_serialize spec version to recompute against; the S11 restore
    re-verify reads it from the RESTORED ``system_config.canonical_serialize_version`` rather than
    hardcoding 1, so a future v2 chain verifies under its own spec (R12/D-4).

    ``verify_key`` enables the signed-checkpoint attestation (doc 12 §4.4) on a FULL walk: the walk
    alone is self-consistent, so a privileged DB owner who rewrites the payloads AND recomputes the
    hashes passes clean — only the Ed25519 signature (which the attacker cannot forge) on the latest
    checkpoint exposes the rewrite. When a key is supplied (``load_verify_key``) and the walk is
    unbounded, a bad signature or a checkpoint↔chain hash mismatch is appended as a break."""
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

    # Signed-checkpoint attestation (doc 12 §4.4) — only on a full walk (a bounded window has no
    # meaningful global-checkpoint compare). A bad signature or a checkpoint↔chain hash mismatch is
    # a break the self-consistent walk above cannot surface.
    checkpoint_status: CheckpointStatus | None = None
    if verify_key is not None and from_id is None and to_id is None:
        checkpoint_status, cp_breaks = await _check_checkpoint(session, org_id, verify_key)
        breaks.extend(cp_breaks)

    pending = (
        await session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_(None))
        )
    ).scalar_one()

    return VerifyResult(
        verified=not breaks,
        checked=len(rows),
        pending=int(pending),
        breaks=breaks,
        checkpoint=checkpoint_status,
    )


async def _check_checkpoint(
    session: AsyncSession, org_id: uuid.UUID, verify_key: Ed25519PublicKey
) -> tuple[CheckpointStatus, list[ChainBreak]]:
    """Attest the newest signed ``audit_checkpoint`` against ``verify_key`` + the live chain.
    Returns the status plus any breaks (empty when it attests)."""
    cp = (
        (
            await session.execute(
                select(AuditCheckpoint)
                .where(AuditCheckpoint.org_id == org_id)
                .order_by(AuditCheckpoint.latest_id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if cp is None:
        # Nothing anchored yet — NOT a tamper (a wholesale in-DB checkpoint deletion is caught by
        # the INDEPENDENT off-host read-back, which the DB owner cannot reach). R13 soft-gate warns.
        return (
            CheckpointStatus(False, None, None, None, "no checkpoint anchored yet"),
            [],
        )
    sig_ok = verify_checkpoint_signature(
        verify_key,
        org_id=cp.org_id,
        latest_id=cp.latest_id,
        latest_row_hash=bytes(cp.latest_row_hash),
        timestamp=cp.timestamp,
        signature=None if cp.app_signature is None else bytes(cp.app_signature),
    )
    if not sig_ok:
        reason = "checkpoint signature invalid (forged/rewritten checkpoint)"
        return (
            CheckpointStatus(True, False, None, cp.latest_id, reason),
            [ChainBreak(at_id=cp.latest_id, reason=reason)],
        )
    stored = (
        await session.execute(
            select(AuditEvent.row_hash).where(
                AuditEvent.org_id == org_id,
                AuditEvent.id == cp.latest_id,
                AuditEvent.chained_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if stored is None:
        reason = "checkpoint references a missing/unchained row (deletion)"
        return (
            CheckpointStatus(True, True, False, cp.latest_id, reason),
            [ChainBreak(at_id=cp.latest_id, reason=reason)],
        )
    if bytes(stored) != bytes(cp.latest_row_hash):
        reason = "checkpoint latest_row_hash mismatch (chain rewritten since last authentic anchor)"
        return (
            CheckpointStatus(True, True, False, cp.latest_id, reason),
            [ChainBreak(at_id=cp.latest_id, reason=reason)],
        )
    return CheckpointStatus(True, True, True, cp.latest_id, None), []
