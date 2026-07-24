"""The records disposition use-case layer (slice S-rec-2, doc 06 §5, doc 14 §10).

Drives the disposition state machine, legal-hold, the Beat retention sweep, and the R27 dual-control
WORM-destroy-under-legal-order escape hatch. The load-bearing correctness rules:

* **Purge-AFTER-commit (Batch 5)** — a DESTROY MARKS its evidence (deletes the ``blob`` row +
  ``evidence_blob`` links and records a ``pending_blob_purge`` marker), COMMITs the DISPOSED
  tombstone + those deletes FIRST, and only THEN physically removes the MinIO bytes (idempotent).
  A storage failure after the commit leaves the record DISPOSED + a committed marker that
  ``reap_pending_blob_purges`` completes — never *deleted bytes with a rolled-back DB* (which would
  strand a ``blob`` row over missing bytes and silently break backups). It trades toward a brief
  'tombstone before bytes gone' window (safe + reaper-recoverable: a backup iterates ``blob`` rows,
  so it never references the transiently-orphaned bytes — the S-rec-2 blob-row-iff-bytes invariant,
  in the safe direction).
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

import dataclasses
import datetime
import logging
import uuid

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._record_enums import RecordDispositionState
from ...db.models._retention_enums import DispositionAction
from ...db.models.app_user import AppUser
from ...db.models.disposition_event import DispositionEvent
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.worm_destroy_request import WormDestroyRequest
from ...db.session import get_sessionmaker
from ...domain.records.disposition import legal_disposition_transition, self_disposition_blocked
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


@dataclasses.dataclass(frozen=True, slots=True)
class _PurgeSpec:
    """A blob whose ``blob`` row + ``evidence_blob`` links are deleted this txn; its bytes are
    purged AFTER the commit (idempotent), then the ``pending_blob_purge`` marker (``purge_id``) is
    dropped on success."""

    purge_id: uuid.UUID
    sha256: str
    bucket: str
    object_key: str
    bypass: bool


async def _mark_record_evidence_for_purge(
    session: AsyncSession, record: Record, *, bypass: bool
) -> list[_PurgeSpec]:
    """The DB phase of a DESTROY erasure (NO S3 call, NO commit). For each evidence blob this record
    is the LAST live referencer of, drop the ``blob`` row + ``evidence_blob`` links and record a
    ``pending_blob_purge`` marker — so the caller COMMITs the DISPOSED tombstone + these deletes
    FIRST, then physically purges the bytes as a separate idempotent step (``_purge_marked``). That
    ordering is the fix: a crash leaves a committed, reaper-recoverable marker — never bytes-gone-
    with-the-DB-rolled-back (which would strand a ``blob`` row over dead bytes, breaking backups).

    Each shared blob is row-locked ``FOR UPDATE`` (in sha256 order — deadlock-safe) BEFORE the
    liveness re-check, so two concurrent shared-blob dispositions serialise and the last referencer
    purges, instead of both observing the peer live and orphaning the bytes.

    Also handles the structured-PDF rendition (S-rec-3) — a per-record non-evidence blob reachable
    only via ``record.structured_pdf_blob_sha256`` (no liveness guard; non-WORM, no bypass)."""
    specs: list[_PurgeSpec] = []
    blobs = {b.sha256: b for _eb, b in await repo.list_evidence_blobs(session, record.id)}
    for sha in sorted(blobs):  # consistent lock order across concurrent dispositions
        blob = blobs[sha]
        await repo.lock_blob_for_update(session, sha)
        if await repo.blob_needed_by_other_live_record(session, sha, record.id):
            continue  # another live record (or a document_version) still needs the bytes
        purge_id = await repo.insert_pending_purge(
            session,
            org_id=record.org_id,
            sha256=sha,
            bucket=blob.bucket,
            object_key=blob.object_key,
            bypass_governance=bypass,
        )
        await repo.delete_blob_and_links(session, sha)
        specs.append(_PurgeSpec(purge_id, sha, blob.bucket, blob.object_key, bypass))
    rendition_sha = record.structured_pdf_blob_sha256
    if rendition_sha is not None:
        bucket = get_settings().s3_bucket_renditions
        await repo.lock_blob_for_update(session, rendition_sha)
        purge_id = await repo.insert_pending_purge(
            session,
            org_id=record.org_id,
            sha256=rendition_sha,
            bucket=bucket,
            object_key=rendition_sha,
            bypass_governance=False,
        )
        await repo.delete_blob_and_links(session, rendition_sha)
        record.structured_pdf_blob_sha256 = None
        specs.append(_PurgeSpec(purge_id, rendition_sha, bucket, rendition_sha, False))
    return specs


async def _purge_marked(
    specs: list[_PurgeSpec], *, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The S3 phase — call AFTER the caller COMMITs the tombstone + blob-row deletes + markers.
    Physically purge each blob's bytes (idempotent) and drop its marker on success. Runs in a FRESH
    session PER marker, opened from the CALLER's ``sessionmaker`` (NOT the request session and NOT
    the process-global one): the disposition is committed, so a post-commit failure here — a storage
    outage OR a DB blip — must never disturb the request transaction or expire its ORM objects (a
    shared-session ``rollback`` would, then the handler's reads raise ``MissingGreenlet``).
    The sessionmaker must be bound to the CALLER's engine + event loop — the FastAPI request loop
    for the API paths (``get_sessionmaker()``), the Celery task's own ``asyncio.run`` loop + local
    engine for the sweep — since reusing the process-global pool across a task's per-invocation loop
    raises a cross-loop ``RuntimeError`` (not a ``SQLAlchemyError``, so it would escape the deferral
    below). Either failure just leaves the marker for ``reap_pending_blob_purges`` to finish (the
    record stays disposed either way). Skips the purge (and drops the marker) only if a ``blob`` row
    now OWNS this exact object again (same sha + bucket + object_key) — a re-capture into the SAME
    location re-owns the bytes, so a stale marker must not erase them; a matching sha in a DIFFERENT
    bucket is a physically distinct object and does NOT cancel the purge (``blob_owns_object``)."""
    for spec in specs:
        try:
            async with sessionmaker() as s:
                if not await repo.blob_owns_object(
                    s, sha256=spec.sha256, bucket=spec.bucket, object_key=spec.object_key
                ):
                    try:
                        await storage.purge_object(
                            spec.object_key, bucket=spec.bucket, bypass_governance=spec.bypass
                        )
                    except (ClientError, BotoCoreError):
                        logger.warning(
                            "records.purge.deferred_to_reaper",
                            extra={"extra_fields": {"sha256": spec.sha256, "bucket": spec.bucket}},
                        )
                        continue  # storage outage — leave the marker; try the next spec
                await repo.delete_pending_purge(s, spec.purge_id)
                await s.commit()
        except SQLAlchemyError:
            # A post-commit DB blip in the re-check / marker-delete / commit. The fresh session's
            # context manager already rolled it back and the request transaction is untouched, so
            # just defer this marker to reap_pending_blob_purges rather than fail a done operation.
            logger.warning(
                "records.purge.db_deferred_to_reaper",
                extra={"extra_fields": {"sha256": spec.sha256, "bucket": spec.bucket}},
            )
            continue


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
    """Flip the record to DISPOSED + append the immutable ``disposition_event`` tombstone.

    On a DESTROY — the schedule-driven ``DispositionAction.DESTROY`` sweep/human path AND the R27
    WORM-destroy hatch (which also passes ``action=DESTROY``) — also NULL the record's structured
    ``form_field_values`` in the same transaction as the tombstone. A Mode-B structured record's
    personal data (names, assessment comments) must NOT survive a 'physical destruction' / legal
    erasure order. The evidence bytes + the derived structured-PDF rendition are MARKED for purge by
    ``_mark_record_evidence_for_purge`` (the ``blob`` rows are deleted here; the bytes are erased by
    ``_purge_marked`` right after the commit), so nulling this JSONB content in the same txn as the
    tombstone completes the DB-side erasure. ``content_hash`` is deliberately preserved as the
    tombstone's verification anchor. ARCHIVE/TRANSFER dispositions keep their content — a change of
    custody, not an erasure."""
    record.disposition_state = RecordDispositionState.DISPOSED
    if action is DispositionAction.DESTROY:
        record.form_field_values = None
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

    # to_state is DISPOSED. SoD-6 (creator-not-disposer, doc 07 §7): the record's own capturer may
    # not execute its disposition unless the org relaxes it (allow_self_disposition). Checked HERE —
    # the DISPOSED edge only (never the DUE_FOR_REVIEW / ACTIVE branches above), and BEFORE
    # _dispose_now's irreversible purge — so it applies uniformly to every disposition action
    # (DESTROY and the ARCHIVE/TRANSFER actions alike). Audited-then-409, never silent.
    if self_disposition_blocked(
        actor.id,
        record.captured_by,
        allow_self_disposition=await repo.allow_self_disposition(session, record.org_id),
    ):
        await _refuse_self_disposition(session, actor, record)

    # execute the disposition per the record's snapshotted policy: MARK evidence for purge, COMMIT
    # the DISPOSED tombstone + blob-row deletes + purge markers, THEN physically purge the bytes (so
    # a crash can never leave bytes-gone-with-the-DB-rolled-back — the reaper finishes a stranded
    # mark).
    specs = await _dispose_now(session, actor, record, reason=reason)
    await session.commit()
    await _purge_marked(specs, sessionmaker=get_sessionmaker())
    await session.refresh(record)
    return record


async def _dispose_now(
    session: AsyncSession, actor: AppUser, record: Record, *, reason: str | None
) -> list[_PurgeSpec]:
    """The human-approved DUE_FOR_REVIEW → DISPOSED execution (no commit — the caller commits). For
    a DESTROY it MARKS evidence for purge and returns the specs; the caller commits, then calls
    ``_purge_marked`` to physically erase the bytes. Non-DESTROY actions return ``[]``."""
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

    specs: list[_PurgeSpec] = []
    if action is DispositionAction.DESTROY:
        await _guard_or_refuse_destroy(session, actor, record, bypass=False)
        specs = await _mark_record_evidence_for_purge(session, record, bypass=False)

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
    return specs


async def _refuse_self_disposition(session: AsyncSession, actor: AppUser, record: Record) -> None:
    """SoD-6 refusal (creator-not-disposer): the refuse-with-reason contract (the
    ``_guard_or_refuse_destroy`` precedent) — log ``DISPOSITION_REFUSED_SOD`` (committed) then raise
    409, never silent. Distinct from ``RECORD_ERASURE_REFUSED`` (a preservation refusal) — this is a
    duty-segregation refusal and fires for ALL disposition actions, not just DESTROY."""
    emit_record_event(
        session,
        actor,
        EventType.DISPOSITION_REFUSED_SOD,
        record.id,
        after={
            "reason": "sod_self_disposition",
            "constraint": "SoD-6",
            "captured_by": str(record.captured_by),
            "disposition_state": record.disposition_state.value,
        },
    )
    await session.commit()
    raise _conflict(
        "sod_self_disposition",
        "Disposition refused: the record's capturer may not dispose it (SoD-6)",
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
    # Dual-control (R27): a distinct second actor must approve. This subsumes SoD-6
    # (creator-not-disposer) by a STRONGER rule — two distinct humans are mandatory here — so SoD-6
    # is intentionally NOT re-checked on this legal-order hatch, and allow_self_disposition must
    # NEVER weaken it.
    if actor.id == req.requested_by:
        raise _conflict("dual_control_same_actor", "A second, distinct authorizer must approve")

    record = await _load_record(session, actor, record_id, for_update=True)
    if record.disposition_state is RecordDispositionState.DISPOSED:
        raise _conflict("already_disposed", "Record is already disposed")

    # Pre-purge guard: only COMPLIANCE mode is refused (bypass overrides an unexpired lock + hold).
    await _guard_or_refuse_destroy(session, actor, record, bypass=True)

    # MARK the evidence for purge (blob-row deletes + markers); the physical S3 purge is the
    # post-commit ``_purge_marked`` step below (reaper-backstopped).
    specs = await _mark_record_evidence_for_purge(session, record, bypass=True)

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
    await _purge_marked(specs, sessionmaker=get_sessionmaker())
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
    session: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    purge_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """Flip due ``ACTIVE`` records to ``DUE_FOR_REVIEW`` (+ the ``RECORD_DISPOSITION_DUE`` system
    event — the v1 'notify owning org_role' surrogate) and auto-execute disposition for low-risk
    (``review_required=false``) policies once the WORM lock allows. ``review_required=true`` records
    stop at DUE_FOR_REVIEW for human approval. Returns ``{flipped, disposed, skipped}``.

    Ordering (the Batch-5 fix): each record's disposition only MARKS its evidence for purge (DB-only
    — blob-row deletes + ``pending_blob_purge`` markers) inside a per-record SAVEPOINT; the sweep
    COMMITs ONCE, THEN physically purges the marked bytes (``_purge_marked``, idempotent, reaper-
    backstopped). Because the S3 purge is strictly AFTER the commit, a commit failure purges NOTHING
    — it can no longer strand deleted bytes over a rolled-back DB (the amplification is gone
    regardless of commit granularity). Per-record commits are NOT used here: the batch is
    reserved by one ``FOR UPDATE SKIP LOCKED`` for the whole sweep, so committing mid-loop would
    release the tail's row locks, letting a concurrent sweep / manual disposition double-process;
    the SAVEPOINTs already give per-record failure isolation."""
    now = now or _now()
    today = now.date()
    summary = {"flipped": 0, "disposed": 0, "skipped": 0}
    specs: list[_PurgeSpec] = []

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
                    marks = await _auto_dispose(session, record, policy, now)
                if marks is not None:
                    specs.extend(marks)
                    summary["disposed"] += 1
            except Exception:  # noqa: BLE001 — per-record isolation: one bad record must not sink
                # the whole sweep (the savepoint rolled back; the record is retried next sweep).
                logger.warning(
                    "records.sweep.dispose_failed",
                    extra={"extra_fields": {"record_id": str(record.id)}},
                )

    await session.commit()  # durable FIRST — a failure here purges nothing (specs untouched)
    # Purge from the CALLER's engine/loop: the sweep's task passes its loop-scoped sessionmaker
    # (the process-global pool would be cross-loop from the task's asyncio.run); tests/direct calls
    # fall back to the global one (single loop).
    await _purge_marked(specs, sessionmaker=purge_sessionmaker or get_sessionmaker())
    return summary


async def _auto_dispose(
    session: AsyncSession, record: Record, policy: RetentionPolicy, now: datetime.datetime
) -> list[_PurgeSpec] | None:
    """Execute a system (Beat) disposition — MARK evidence for purge (no S3), flip DISPOSED, and
    return the purge specs; the sweep COMMITs, then physically purges the marked bytes. Returns
    ``None`` (no change) when a DESTROY's WORM lock is not yet expired (leave at DUE_FOR_REVIEW;
    retried next sweep); ``[]`` when a non-DESTROY / evidence-free record is disposed."""
    action = policy.disposition_action
    specs: list[_PurgeSpec] = []
    if action is DispositionAction.DESTROY:
        retain_until = await _max_worm_retain_until(session, record.id)
        if retain_until is not None and retain_until > now:
            return None  # WORM lock not yet expired — no bypass in the sweep; wait
        specs = await _mark_record_evidence_for_purge(session, record, bypass=False)
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
    return specs


# --- the pending-purge reaper (Batch 5 crash-recovery) -----------------------------------


async def reap_pending_blob_purges(session: AsyncSession) -> dict[str, int]:
    """Crash-recovery for the post-commit purge. A ``pending_blob_purge`` marker survives only when
    the immediate ``_purge_marked`` didn't finish (a crash or storage outage between the disposition
    commit and the physical erase); this backstop completes it — idempotently purge the bytes then
    drop the marker, committing per marker so a mid-run crash re-does at most one. Fields are
    snapshotted per batch so a per-marker commit can't expire an ORM row we read. LOOPS in
    ``exclude_ids`` batches so a persistent-failure cohort in the oldest rows can't starve newer
    purgeable markers within a run. SKIPs the purge (and drops the marker) when a ``blob`` row now
    OWNS this exact object again (same sha + bucket + object_key) — a re-capture of the same content
    into the SAME location re-owns the bytes; a matching sha in a different bucket is a distinct
    object and does NOT cancel the purge. Returns ``{reaped}``."""
    reaped = 0
    handled: set[uuid.UUID] = set()
    while True:
        markers = await repo.list_pending_purges(session, exclude_ids=handled)
        if not markers:
            break
        todo = [(m.id, m.sha256, m.bucket, m.object_key, m.bypass_governance) for m in markers]
        handled.update(
            purge_id for purge_id, *_ in todo
        )  # skip this cohort on the next batch fetch
        for purge_id, sha256, bucket, object_key, bypass in todo:
            if await repo.blob_owns_object(
                session, sha256=sha256, bucket=bucket, object_key=object_key
            ):
                await repo.delete_pending_purge(session, purge_id)
                await session.commit()
                reaped += 1
                continue
            try:
                await storage.purge_object(object_key, bucket=bucket, bypass_governance=bypass)
            except (ClientError, BotoCoreError):
                logger.warning(
                    "records.reap_purge.failed",
                    extra={"extra_fields": {"sha256": sha256, "bucket": bucket}},
                )
                continue  # leave the marker; retried on the NEXT run (skipped this run via handled)
            await repo.delete_pending_purge(session, purge_id)
            await session.commit()
            reaped += 1
    return {"reaped": reaped}
