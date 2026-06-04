"""The records disposition use-case layer (slice S-rec-2, doc 06 §5, doc 14 §10).

Drives the disposition state machine, legal-hold, the Beat retention sweep, and the R27 dual-control
WORM-destroy-under-legal-order escape hatch. The load-bearing correctness rules:

* **Fail-closed, purge-FIRST** — a DESTROY physically removes the MinIO bytes *before* the DB flips
  to DISPOSED + writes the tombstone, and only on success. So a storage failure rolls the txn
  back (record stays ``DUE_FOR_REVIEW``/``ACTIVE``; ``purge_object`` is idempotent so a retry
  self-heals) — never a DISPOSED tombstone over still-present bytes, never deleted bytes without a
  tombstone.
* **Pre-purge refusal (GDPR, R27)** — a DESTROY blocked by an unexpired WORM lock, an active
  legal_hold, or COMPLIANCE mode is *logged-as-refused-with-reason* (``RECORD_ERASURE_REFUSED``,
  committed) then 409 — never silently swallowed.
* **Dual-control** — the WORM-destroy hatch needs two *distinct* authorizers (requester ≠ approver,
  enforced in-service with a 409 + a DB CHECK backstop); only this path may pass
  ``BypassGovernanceRetention``.

Every transition writes its ``audit_event`` (object_type=record) in the same transaction as the
mutation (``emit_record_event`` for a user actor, ``emit_record_event_system`` for the Beat sweep).
Disposition is the *only* post-capture write to a record besides the S-rec-1 correction pointer-flip
— records stay otherwise immutable.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import EventType
from ...db.models._record_enums import RecordDispositionState
from ...db.models._retention_enums import DispositionAction
from ...db.models.app_user import AppUser
from ...db.models.disposition_event import DispositionEvent
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.worm_destroy_request import WormDestroyRequest
from ...domain.records.disposition import legal_disposition_transition
from ...domain.records.retention import retention_until
from ...problems import ProblemException
from ..vault import storage
from . import repository as repo
from .service import _load_record, _now, emit_record_event, emit_record_event_system

logger = logging.getLogger("easysynq.records.disposition")

_COMPLIANCE = "COMPLIANCE"


# --- shared helpers ----------------------------------------------------------------------


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


async def _max_worm_retain_until(
    session: AsyncSession, record_id: uuid.UUID
) -> datetime.datetime | None:
    """The latest WORM ``retain_until`` across a record's evidence blobs (None if none locked)."""
    latest: datetime.datetime | None = None
    for _eb, blob in await repo.list_evidence_blobs(session, record_id):
        if blob.worm_retain_until is not None and (
            latest is None or blob.worm_retain_until > latest
        ):
            latest = blob.worm_retain_until
    return latest


async def _purge_record_evidence(session: AsyncSession, record: Record, *, bypass: bool) -> int:
    """Physically destroy the record's evidence bytes (fail-closed; raises on any storage error). A
    blob still attached to another non-disposed record is left intact (its bytes survive for that
    live record); the disposed record keeps its ``evidence_blob`` rows as a tombstone regardless."""
    purged = 0
    seen: set[str] = set()
    for _eb, blob in await repo.list_evidence_blobs(session, record.id):
        if blob.sha256 in seen:
            continue
        seen.add(blob.sha256)
        if await repo.blob_needed_by_other_live_record(session, blob.sha256, record.id):
            continue
        await storage.purge_object(blob.object_key, bucket=blob.bucket, bypass_governance=bypass)
        # The object is gone → drop the now-false blob row + its evidence_blob links so the
        # invariant "a blob row exists iff its object exists" holds (backup won't copy a dead one).
        await repo.delete_blob_and_links(session, blob.sha256)
        purged += 1
    return purged


def _write_tombstone(
    session: AsyncSession,
    record: Record,
    *,
    action: DispositionAction,
    policy_id: uuid.UUID | None,
    approved_by: uuid.UUID | None,
    requested_by: uuid.UUID | None = None,
    is_worm_destroy: bool = False,
    legal_basis: str | None = None,
) -> None:
    """Flip the record to DISPOSED + append the immutable ``disposition_event`` tombstone."""
    record.disposition_state = RecordDispositionState.DISPOSED
    session.add(
        DispositionEvent(
            org_id=record.org_id,
            record_id=record.id,
            action=action,
            tombstone=True,
            policy_id=policy_id,
            approved_by=approved_by,
            requested_by=requested_by,
            is_worm_destroy=is_worm_destroy,
            legal_basis=legal_basis,
        )
    )


# --- PATCH /disposition (advance the state machine) --------------------------------------


async def advance_disposition(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    *,
    to_state: RecordDispositionState,
    reason: str | None = None,
) -> Record:
    """Advance a record's ``disposition_state`` (doc 06 §5.3 / doc 15 §8.9). Handles the
    ACTIVE↔DUE_FOR_REVIEW↔DISPOSED edges; ON_HOLD is driven by the legal-hold endpoints."""
    record = await _load_record(session, actor, record_id, for_update=True)
    frm = record.disposition_state

    if to_state is RecordDispositionState.ON_HOLD:
        raise _conflict(
            "use_legal_hold_endpoint", "Place a legal hold via POST /records/{id}/legal-hold"
        )
    if frm is RecordDispositionState.ON_HOLD:
        raise _conflict(
            "on_legal_hold", "Record is on legal hold; release it via the legal-hold endpoint"
        )
    if not legal_disposition_transition(frm, to_state):
        raise _conflict(
            "invalid_transition",
            f"Disposition transition {frm.value} → {to_state.value} is not allowed",
        )

    if to_state is RecordDispositionState.DUE_FOR_REVIEW:  # ACTIVE → DUE (manual early review)
        record.disposition_state = to_state
        emit_record_event(
            session,
            actor,
            EventType.RECORD_DISPOSITION_DUE,
            record.id,
            before={"disposition_state": frm.value},
            after={"disposition_state": to_state.value, "reason": reason, "trigger": "manual"},
        )
        await session.commit()
        await session.refresh(record)
        return record

    if to_state is RecordDispositionState.ACTIVE:  # DUE → ACTIVE (retention extended / re-anchor)
        record.disposition_state = to_state
        emit_record_event(
            session,
            actor,
            EventType.RECORD_RETENTION_EXTENDED,
            record.id,
            before={"disposition_state": frm.value},
            after={"disposition_state": to_state.value, "reason": reason},
        )
        await session.commit()
        await session.refresh(record)
        return record

    # to_state is DISPOSED — execute the disposition per the record's snapshotted policy.
    await _dispose_now(session, actor, record, reason=reason)
    await session.commit()
    await session.refresh(record)
    return record


async def _dispose_now(
    session: AsyncSession, actor: AppUser, record: Record, *, reason: str | None
) -> None:
    """The human-approved DUE_FOR_REVIEW → DISPOSED execution (no commit — the caller commits)."""
    policy = await repo.get_policy(session, record.retention_policy_id, record.org_id)
    if policy is None:  # pragma: no cover — NOT-NULL FK guarantees it
        raise ProblemException(
            status=422, code="validation_error", title="Retention policy missing"
        )
    action = policy.disposition_action

    if action is DispositionAction.RETAIN_PERMANENT:
        raise _conflict(
            "retain_permanent",
            "A RETAIN_PERMANENT record is never disposed on schedule; use the destroy hatch",
        )

    if action is DispositionAction.DESTROY:
        await _guard_or_refuse_destroy(session, actor, record, bypass=False)
        await _purge_record_evidence(session, record, bypass=False)

    _write_tombstone(session, record, action=action, policy_id=policy.id, approved_by=actor.id)
    emit_record_event(
        session,
        actor,
        EventType.RECORD_DISPOSED,
        record.id,
        before={"disposition_state": RecordDispositionState.DUE_FOR_REVIEW.value},
        after={
            "disposition_state": RecordDispositionState.DISPOSED.value,
            "action": action.value,
            "policy_id": str(policy.id),
            "reason": reason,
        },
    )


async def _guard_or_refuse_destroy(
    session: AsyncSession, actor: AppUser, record: Record, *, bypass: bool
) -> None:
    """Pre-purge fail-closed guard for a DESTROY. On a deliberate refusal (legal hold, unexpired
    WORM without bypass, or COMPLIANCE mode with bypass) logs ``RECORD_ERASURE_REFUSED`` (committed)
    then raises 409 — the GDPR refused-with-reason (R27). Returns when destruction may proceed."""
    refusal: tuple[str, str] | None = None  # (code, reason)
    if record.legal_hold:
        refusal = ("legal_hold_active", "legal_hold")
    elif not bypass:
        retain_until = await _max_worm_retain_until(session, record.id)
        if retain_until is not None and retain_until > _now():
            refusal = ("worm_lock_unexpired", "worm_lock_unexpired")
    if refusal is None and bypass:
        mode = await repo.org_object_lock_mode(session, record.org_id)
        if mode == _COMPLIANCE:
            refusal = ("compliance_mode_denies_destroy", "compliance_mode")
    if refusal is None:
        return
    code, why = refusal
    emit_record_event(
        session,
        actor,
        EventType.RECORD_ERASURE_REFUSED,
        record.id,
        after={"reason": why, "disposition_state": record.disposition_state.value},
    )
    await session.commit()
    raise _conflict(code, f"Destruction refused: {why}")


# --- legal hold --------------------------------------------------------------------------


async def place_legal_hold(
    session: AsyncSession, actor: AppUser, record_id: uuid.UUID, *, reason: str
) -> Record:
    record = await _load_record(session, actor, record_id, for_update=True)
    if record.disposition_state is RecordDispositionState.DISPOSED:
        raise _conflict("already_disposed", "Record is already disposed")
    if record.legal_hold:
        raise _conflict("already_on_hold", "Record is already on legal hold")
    before = {"legal_hold": False, "disposition_state": record.disposition_state.value}
    record.legal_hold = True
    record.disposition_state = RecordDispositionState.ON_HOLD
    emit_record_event(
        session,
        actor,
        EventType.RECORD_LEGAL_HOLD_PLACED,
        record.id,
        before=before,
        after={
            "legal_hold": True,
            "disposition_state": record.disposition_state.value,
            "reason": reason,
        },
    )
    await session.commit()
    await session.refresh(record)
    return record


async def release_legal_hold(
    session: AsyncSession, actor: AppUser, record_id: uuid.UUID, *, reason: str
) -> Record:
    record = await _load_record(session, actor, record_id, for_update=True)
    if not record.legal_hold:
        raise _conflict("not_on_hold", "Record is not on legal hold")
    before = {"legal_hold": True, "disposition_state": record.disposition_state.value}
    record.legal_hold = False
    # ON_HOLD → ACTIVE (the next sweep re-evaluates expiry; doc 06 §5.3).
    record.disposition_state = RecordDispositionState.ACTIVE
    emit_record_event(
        session,
        actor,
        EventType.RECORD_LEGAL_HOLD_RELEASED,
        record.id,
        before=before,
        after={
            "legal_hold": False,
            "disposition_state": record.disposition_state.value,
            "reason": reason,
        },
    )
    await session.commit()
    await session.refresh(record)
    return record


# --- R27 dual-control WORM-destroy-under-legal-order -------------------------------------


async def request_worm_destroy(
    session: AsyncSession, actor: AppUser, record_id: uuid.UUID, *, legal_basis: str
) -> WormDestroyRequest:
    """First control: a distinct second actor must approve before any bytes are destroyed."""
    record = await _load_record(session, actor, record_id, for_update=True)
    if record.disposition_state is RecordDispositionState.DISPOSED:
        raise _conflict("already_disposed", "Record is already disposed")
    if await repo.open_worm_destroy_request(session, record.id) is not None:
        raise _conflict("worm_destroy_request_open", "An open destroy request already exists")
    req = WormDestroyRequest(
        org_id=actor.org_id,
        record_id=record.id,
        legal_basis=legal_basis,
        requested_by=actor.id,
    )
    session.add(req)
    await session.flush()
    emit_record_event(
        session,
        actor,
        EventType.RECORD_WORM_DESTROY_REQUESTED,
        record.id,
        after={"request_id": str(req.id), "legal_basis": legal_basis},
    )
    await session.commit()
    await session.refresh(req)
    return req


async def approve_worm_destroy(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    req_id: uuid.UUID,
    *,
    reason: str | None = None,
) -> Record:
    """Second control: a *distinct* actor approves → governance-bypass purge (fail-closed) →
    DISPOSED tombstone (``is_worm_destroy=true``, both actors) → ``RECORD_WORM_DESTROYED``."""
    req = await repo.get_worm_destroy_request(session, req_id, for_update=True)
    if req is None or req.record_id != record_id or req.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Destroy request not found")
    if req.executed_at is not None or req.cancelled_at is not None:
        raise _conflict("not_open", "Destroy request is not open")
    if actor.id == req.requested_by:
        raise _conflict("dual_control_same_actor", "A second, distinct authorizer must approve")

    record = await _load_record(session, actor, record_id, for_update=True)
    if record.disposition_state is RecordDispositionState.DISPOSED:
        raise _conflict("already_disposed", "Record is already disposed")

    # Pre-purge guard: only COMPLIANCE mode is refused (bypass overrides an unexpired lock + hold).
    await _guard_or_refuse_destroy(session, actor, record, bypass=True)

    await _purge_record_evidence(session, record, bypass=True)

    req.approved_by = actor.id
    req.executed_at = _now()
    _write_tombstone(
        session,
        record,
        action=DispositionAction.DESTROY,
        policy_id=None,  # a legal-order destroy is not policy-driven
        approved_by=actor.id,
        requested_by=req.requested_by,
        is_worm_destroy=True,
        legal_basis=req.legal_basis,
    )
    emit_record_event(
        session,
        actor,
        EventType.RECORD_WORM_DESTROYED,
        record.id,
        after={
            "request_id": str(req.id),
            "requested_by": str(req.requested_by),
            "approved_by": str(actor.id),
            "legal_basis": req.legal_basis,
            "reason": reason,
        },
    )
    await session.commit()
    await session.refresh(record)
    return record


async def cancel_worm_destroy(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    req_id: uuid.UUID,
    *,
    reason: str | None = None,
) -> WormDestroyRequest:
    req = await repo.get_worm_destroy_request(session, req_id, for_update=True)
    if req is None or req.record_id != record_id or req.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Destroy request not found")
    if req.executed_at is not None or req.cancelled_at is not None:
        raise _conflict("not_open", "Destroy request is not open")
    req.cancelled_by = actor.id
    req.cancelled_at = _now()
    emit_record_event(
        session,
        actor,
        EventType.RECORD_WORM_DESTROY_CANCELLED,
        record_id,
        after={"request_id": str(req.id), "reason": reason},
    )
    await session.commit()
    await session.refresh(req)
    return req


# --- the Beat retention sweep ------------------------------------------------------------


async def sweep_due_records(
    session: AsyncSession, *, now: datetime.datetime | None = None
) -> dict[str, int]:
    """Flip due ``ACTIVE`` records to ``DUE_FOR_REVIEW`` (+ the ``RECORD_DISPOSITION_DUE`` system
    event — the v1 'notify owning org_role' surrogate) and auto-execute disposition for low-risk
    (``review_required=false``) policies once the WORM lock allows. ``review_required=true`` records
    stop at DUE_FOR_REVIEW for human approval. One commit at the end; per-record SAVEPOINTs isolate
    a transient storage failure (that record is left for the next sweep — ``purge_object`` is
    idempotent). Returns ``{flipped, disposed, skipped}``."""
    now = now or _now()
    today = now.date()
    summary = {"flipped": 0, "disposed": 0, "skipped": 0}

    for record, policy in await repo.due_active_records(session, for_update=True):
        try:
            until = retention_until(record.retention_basis_date, policy.duration)
        except ValueError:
            logger.warning(
                "records.sweep.bad_duration",
                extra={"extra_fields": {"record_id": str(record.id), "duration": policy.duration}},
            )
            summary["skipped"] += 1
            continue
        due = until is not None and until <= today

        if record.disposition_state is RecordDispositionState.ACTIVE:
            if not due:
                continue
            async with session.begin_nested():
                record.disposition_state = RecordDispositionState.DUE_FOR_REVIEW
                emit_record_event_system(
                    session,
                    record.org_id,
                    EventType.RECORD_DISPOSITION_DUE,
                    record.id,
                    before={"disposition_state": RecordDispositionState.ACTIVE.value},
                    after={
                        "disposition_state": RecordDispositionState.DUE_FOR_REVIEW.value,
                        "trigger": "sweep",
                        "retention_until": until.isoformat() if until else None,
                    },
                )
            summary["flipped"] += 1

        # Auto-dispose low-risk policies once due AND (for DESTROY) the WORM lock has expired.
        if (
            not policy.review_required
            and due
            and record.disposition_state is RecordDispositionState.DUE_FOR_REVIEW
        ):
            try:
                async with session.begin_nested():
                    did = await _auto_dispose(session, record, policy, now)
                if did:
                    summary["disposed"] += 1
            except (ClientError, BotoCoreError):
                logger.warning(
                    "records.sweep.purge_failed",
                    extra={"extra_fields": {"record_id": str(record.id)}},
                )

    await session.commit()
    return summary


async def _auto_dispose(
    session: AsyncSession, record: Record, policy: RetentionPolicy, now: datetime.datetime
) -> bool:
    """Execute a system (Beat) disposition. Returns ``False`` (no change) when a DESTROY's WORM lock
    is not yet expired (leave at DUE_FOR_REVIEW; retried next sweep). Raises on a storage failure
    (the caller's SAVEPOINT rolls back, the record stays DUE_FOR_REVIEW)."""
    action = policy.disposition_action
    if action is DispositionAction.DESTROY:
        retain_until = await _max_worm_retain_until(session, record.id)
        if retain_until is not None and retain_until > now:
            return False  # WORM lock not yet expired — no bypass in the sweep; wait
        await _purge_record_evidence(session, record, bypass=False)
    _write_tombstone(session, record, action=action, policy_id=policy.id, approved_by=None)
    emit_record_event_system(
        session,
        record.org_id,
        EventType.RECORD_DISPOSED,
        record.id,
        before={"disposition_state": RecordDispositionState.DUE_FOR_REVIEW.value},
        after={
            "disposition_state": RecordDispositionState.DISPOSED.value,
            "action": action.value,
            "policy_id": str(policy.id),
            "trigger": "sweep",
        },
    )
    return True
