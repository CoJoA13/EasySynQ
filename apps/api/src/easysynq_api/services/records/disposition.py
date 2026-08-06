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
from sqlalchemy import asc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._pack_enums import PackStatus
from ...db.models._record_enums import RecordDispositionState
from ...db.models._retention_enums import DispositionAction
from ...db.models.app_user import AppUser
from ...db.models.blob import Blob
from ...db.models.disposition_event import DispositionEvent
from ...db.models.evidence_pack import EvidencePack
from ...db.models.pack_share_link import PackShareLink
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.worm_destroy_request import WormDestroyRequest
from ...db.session import get_sessionmaker
from ...domain.records.disposition import legal_disposition_transition, self_disposition_blocked
from ...domain.records.retention import retention_until
from ...problems import ProblemCode, ProblemException
from ..vault import storage
from . import repository as repo
from .service import _load_record, _now, emit_record_event, emit_record_event_system

logger = logging.getLogger("easysynq.records.disposition")

_COMPLIANCE = "COMPLIANCE"


# --- shared helpers ----------------------------------------------------------------------


def _conflict(code: ProblemCode, title: str) -> ProblemException:
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


@dataclasses.dataclass(frozen=True, slots=True)
class _PendingPurgeSnapshot:
    """Commit-stable fields copied before the reaper commits any marker in a claimed batch."""

    purge_id: uuid.UUID
    org_id: uuid.UUID
    sha256: str
    bucket: str
    object_key: str
    requested_bypass: bool
    record_id: uuid.UUID | None
    disposition_event_id: uuid.UUID | None
    worm_destroy_request_id: uuid.UUID | None
    authority_bound: bool


def _r27_authority_matches(
    record: Record,
    event: DispositionEvent,
    request: WormDestroyRequest,
    *,
    source_event: DispositionEvent | None,
) -> bool:
    """Validate root or one-hop-derived R27 authority for a target Record.

    A root event is authorized by a request for that same Record. A derivative pack-copy event is
    authorized by the root event/request for the copied source Record, and must copy the exact
    organization, actors, and legal basis. Deeper chains are refused.
    """
    if (
        event.org_id != record.org_id
        or event.record_id != record.id
        or event.action is not DispositionAction.DESTROY
        or not event.tombstone
        or not event.is_worm_destroy
        or event.policy_id is not None
        or event.requested_by is None
        or event.approved_by is None
        or event.requested_by == event.approved_by
        or request.executed_at is None
        or request.cancelled_at is not None
        or request.approved_by is None
        or request.requested_by == request.approved_by
    ):
        return False

    if event.derived_from_disposition_event_id is None:
        if source_event is not None:
            return False
        root = event
    else:
        if (
            source_event is None
            or event.derived_from_disposition_event_id != source_event.id
            or source_event.derived_from_disposition_event_id is not None
            or source_event.org_id != record.org_id
            or not source_event.is_worm_destroy
            or source_event.action is not DispositionAction.DESTROY
            or not source_event.tombstone
            or source_event.policy_id is not None
            or event.requested_by != source_event.requested_by
            or event.approved_by != source_event.approved_by
            or event.legal_basis != source_event.legal_basis
        ):
            return False
        root = source_event

    return bool(
        request.id is not None
        and request.org_id == record.org_id
        and request.record_id == root.record_id
        and root.org_id == request.org_id
        and root.requested_by == request.requested_by
        and root.approved_by == request.approved_by
        and root.legal_basis == request.legal_basis
    )


async def _mark_record_evidence_for_purge(
    session: AsyncSession,
    record: Record,
    *,
    disposition_event: DispositionEvent,
    worm_destroy_request: WormDestroyRequest | None = None,
    source_disposition_event: DispositionEvent | None = None,
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

    Every marker is bound to the explicit immutable DESTROY event and, for an R27 legal-order
    destruction, its executed two-person request. This routine derives bypass from that authority;
    callers cannot set the marker boolean independently.

    Also handles the structured-PDF rendition (S-rec-3). Its pointer is cleared first, then the
    same cross-domain last-owner check protects any unrelated live Record, document version, or
    available pack that lawfully owns identical content."""
    if (
        disposition_event.record_id != record.id
        or disposition_event.org_id != record.org_id
        or disposition_event.action is not DispositionAction.DESTROY
        or not disposition_event.tombstone
    ):
        raise ValueError("purge marker authority must be this Record's DESTROY event")
    if disposition_event.is_worm_destroy:
        if (
            worm_destroy_request is None
            or worm_destroy_request.id is None
            or not _r27_authority_matches(
                record,
                disposition_event,
                worm_destroy_request,
                source_event=source_disposition_event,
            )
        ):
            raise ValueError(
                "R27 purge marker authority requires root or one-hop-derived two-person lineage"
            )
    elif (
        worm_destroy_request is not None
        or source_disposition_event is not None
        or disposition_event.derived_from_disposition_event_id is not None
        or disposition_event.policy_id != record.retention_policy_id
        or disposition_event.requested_by is not None
        or disposition_event.legal_basis is not None
    ):
        raise ValueError("ordinary purge marker authority requires this Record's retention policy")

    bypass = disposition_event.is_worm_destroy
    request_id = worm_destroy_request.id if worm_destroy_request is not None else None
    specs: list[_PurgeSpec] = []
    blobs = {b.sha256: b for _eb, b in await repo.list_evidence_blobs(session, record.id)}
    rendition_sha = record.structured_pdf_blob_sha256
    # Evidence and structured-rendition rows share one Blob lock namespace. Acquire the complete
    # Record set in global SHA order before any liveness decision so an evidence SHA cannot be
    # followed by a lexically-earlier rendition SHA in a concurrent destroy transaction.
    record_blob_shas = set(blobs)
    if rendition_sha is not None:
        record_blob_shas.add(rendition_sha)
    for sha in sorted(record_blob_shas):
        await repo.lock_blob_for_update(session, sha)
    for sha in sorted(blobs):  # consistent lock order across concurrent dispositions
        blob = blobs[sha]
        if await repo.blob_needed_by_other_live_record(session, sha, record.id):
            await repo.detach_record_evidence_blob(session, record.id, sha)
            continue  # another live Record, document version, rendition, or pack owns the bytes
        purge_id = await repo.insert_pending_purge(
            session,
            org_id=record.org_id,
            sha256=sha,
            bucket=blob.bucket,
            object_key=blob.object_key,
            bypass_governance=bypass,
            record_id=record.id,
            disposition_event_id=disposition_event.id,
            worm_destroy_request_id=request_id,
        )
        await repo.delete_blob_and_links(session, sha)
        specs.append(_PurgeSpec(purge_id, sha, blob.bucket, blob.object_key, bypass))
    if rendition_sha is not None:
        record.structured_pdf_blob_sha256 = None
        await session.flush()
        rendition_blob = (
            await session.execute(
                select(Blob)
                .where(Blob.sha256 == rendition_sha)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if rendition_blob is None or await repo.blob_needed_by_any_live_owner(
            session, rendition_sha
        ):
            return specs
        rendition_bypass = bool(bypass and rendition_blob.worm_locked)
        purge_id = await repo.insert_pending_purge(
            session,
            org_id=record.org_id,
            sha256=rendition_sha,
            bucket=rendition_blob.bucket,
            object_key=rendition_blob.object_key,
            bypass_governance=rendition_bypass,
            record_id=record.id,
            disposition_event_id=disposition_event.id,
            worm_destroy_request_id=request_id,
        )
        await repo.delete_blob_and_links(session, rendition_sha)
        specs.append(
            _PurgeSpec(
                purge_id,
                rendition_sha,
                rendition_blob.bucket,
                rendition_blob.object_key,
                rendition_bypass,
            )
        )
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
    now OWNS this exact physical object again (same bucket + object_key) — a re-capture into the
    SAME location re-owns the bytes, so a stale marker must not erase them; a matching SHA in a
    DIFFERENT bucket is a physically distinct object and does not cancel the purge."""
    for spec in specs:
        try:
            async with sessionmaker() as s:
                # Match the reaper's marker-row → physical-object lock order. If the reaper
                # already completed this marker, there is no stale work left to replay.
                if not await repo.lock_pending_purge_for_update(s, spec.purge_id):
                    await s.rollback()
                    continue
                await repo.lock_physical_object(
                    s,
                    bucket=spec.bucket,
                    object_key=spec.object_key,
                )
                if not await repo.object_is_owned(
                    s, bucket=spec.bucket, object_key=spec.object_key
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
    derived_from_disposition_event_id: uuid.UUID | None = None,
) -> DispositionEvent:
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
    event = DispositionEvent(
        id=uuid.uuid4(),
        org_id=record.org_id,
        record_id=record.id,
        action=action,
        tombstone=True,
        policy_id=policy_id,
        approved_by=approved_by,
        requested_by=requested_by,
        is_worm_destroy=is_worm_destroy,
        legal_basis=legal_basis,
        derived_from_disposition_event_id=derived_from_disposition_event_id,
    )
    session.add(event)
    return event


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

    event = _write_tombstone(
        session, record, action=action, policy_id=policy.id, approved_by=actor.id
    )
    if action is DispositionAction.DESTROY:
        specs = await _mark_record_evidence_for_purge(
            session,
            record,
            disposition_event=event,
        )
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
    refusal: tuple[ProblemCode, str] | None = None  # (code, reason)
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


_PACK_ERASURE_REASON = "R27 legal erasure invalidated the Evidence Pack"


async def _lock_pack_records(
    session: AsyncSession, packs: list[EvidencePack]
) -> dict[uuid.UUID, Record]:
    record_ids = sorted(
        {pack.pack_record_id for pack in packs if pack.pack_record_id is not None},
        key=str,
    )
    if len(record_ids) != len({pack.pack_record_id for pack in packs}):
        raise RuntimeError("SEALED Evidence Pack is missing its registered Record")
    records = list(
        (
            await session.execute(
                select(Record)
                .where(Record.id.in_(record_ids))
                .order_by(asc(Record.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    by_id = {record.id: record for record in records}
    if set(by_id) != set(record_ids):
        raise RuntimeError("SEALED Evidence Pack registered Record is missing")
    return by_id


async def _lock_r27_blob_rows(
    session: AsyncSession,
    records: list[Record],
    artifact_shas: set[str],
) -> None:
    """Acquire every source/derivative Blob row in one transaction-global SHA order.

    R27 can dispose several registered pack Records and detached portfolios at once. Per-record
    ordering is insufficient because UUID-ordered Records can expose their SHA sets in opposite
    order to another destroy transaction. The caller has already stabilized pack membership under
    the organization-exclusive artifact lock; Record evidence is immutable after capture.
    """
    shas = set(artifact_shas)
    for record in records:
        shas.update(
            blob.sha256 for _link, blob in await repo.list_evidence_blobs(session, record.id)
        )
        if record.structured_pdf_blob_sha256 is not None:
            shas.add(record.structured_pdf_blob_sha256)
    for sha in sorted(shas):
        await repo.lock_blob_for_update(session, sha)


async def _mark_detached_pack_artifacts(
    session: AsyncSession,
    artifacts: dict[str, tuple[Record, DispositionEvent, bool]],
    *,
    request: WormDestroyRequest,
    source_event: DispositionEvent,
) -> list[_PurgeSpec]:
    """Mark detached ZIP/portfolio pointers not already reached through pack Record evidence.

    The normal pack-Record purge handles canonical ZIPs. This second pass is the corruption-safe
    closer and the portfolio path: after every affected pointer has been cleared, it removes a Blob
    only when no live Record, DocumentVersion, structured rendition, or available pack still owns
    it. ``wants_bypass`` is true for ZIP identities; actual non-WORM objects never request bypass.
    """
    specs: list[_PurgeSpec] = []
    await session.flush()
    for sha in sorted(artifacts):
        record, event, wants_bypass = artifacts[sha]
        lineage_source = source_event if event.id != source_event.id else None
        if not _r27_authority_matches(
            record,
            event,
            request,
            source_event=lineage_source,
        ):
            raise ValueError("detached pack artifact has invalid R27 lineage")
        await repo.lock_blob_for_update(session, sha)
        blob = (
            await session.execute(
                select(Blob)
                .where(Blob.sha256 == sha)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if blob is None or await repo.blob_needed_by_any_live_owner(session, sha):
            continue
        bypass = bool(wants_bypass and blob.worm_locked)
        purge_id = await repo.insert_pending_purge(
            session,
            org_id=record.org_id,
            sha256=sha,
            bucket=blob.bucket,
            object_key=blob.object_key,
            bypass_governance=bypass,
            record_id=record.id,
            disposition_event_id=event.id,
            worm_destroy_request_id=request.id,
        )
        await repo.delete_blob_and_links(session, sha)
        specs.append(_PurgeSpec(purge_id, sha, blob.bucket, blob.object_key, bypass))
    return specs


async def _invalidate_sealed_packs_for_r27(
    session: AsyncSession,
    actor: AppUser,
    source_record: Record,
    source_event: DispositionEvent,
    request: WormDestroyRequest,
) -> list[_PurgeSpec]:
    """Mark the source and invalidate every sealed derivative copy in one R27 transaction.

    The caller holds the organization-exclusive pack-artifact lock. Pack headers, links, derived
    pack-Record tombstones, source/derivative Blob deletes, and authority-bound markers are
    therefore one atomic DB state transition. Every involved Blob row is acquired in global SHA
    order before any per-Record processing. Physical S3 removal remains the shared post-commit
    phase.
    """
    # Lazy imports avoid the records.__init__ -> disposition -> packs.__init__ -> build -> records
    # cycle. At use time the records package (including capture_record) is fully initialized.
    from ..packs import repository as packs_repo
    from ..packs import service as packs_service

    if not _r27_authority_matches(source_record, source_event, request, source_event=None):
        raise ValueError("source R27 event/request lineage is invalid")

    packs = await packs_repo.affected_sealed_packs_for_r27(
        session, source_record.org_id, source_record.id
    )

    now = _now()
    pack_records = await _lock_pack_records(session, packs)
    events_by_record: dict[uuid.UUID, DispositionEvent] = {}
    for record_id in sorted(pack_records, key=str):
        record = pack_records[record_id]
        if record.id == source_record.id:
            events_by_record[record.id] = source_event
            continue
        events_by_record[record.id] = _write_tombstone(
            session,
            record,
            action=DispositionAction.DESTROY,
            policy_id=None,
            approved_by=source_event.approved_by,
            requested_by=source_event.requested_by,
            is_worm_destroy=True,
            legal_basis=source_event.legal_basis,
            derived_from_disposition_event_id=source_event.id,
        )

    artifacts: dict[str, tuple[Record, DispositionEvent, bool]] = {}
    for pack in packs:
        if pack.pack_record_id is None:  # _lock_pack_records fails first; type narrowing.
            raise RuntimeError("SEALED Evidence Pack is missing its registered Record")
        record = pack_records[pack.pack_record_id]
        event = events_by_record[record.id]
        old_zip = pack.zip_blob_sha256
        old_portfolio = pack.portfolio_blob_sha256
        pack.status = PackStatus.UNAVAILABLE
        pack.invalidated_at = now
        pack.invalidated_by_disposition_event_id = source_event.id
        pack.zip_blob_sha256 = None
        pack.portfolio_blob_sha256 = None

        if old_zip is not None:
            prior = artifacts.get(old_zip)
            artifacts[old_zip] = (
                prior[0] if prior else record,
                prior[1] if prior else event,
                True,
            )
        if old_portfolio is not None:
            prior = artifacts.get(old_portfolio)
            artifacts[old_portfolio] = (
                prior[0] if prior else record,
                prior[1] if prior else event,
                prior[2] if prior else False,
            )

        packs_service.emit_pack_event(
            session,
            actor,
            EventType.PACK_INVALIDATED,
            pack.id,
            before={
                "status": PackStatus.SEALED.value,
                "zip_blob_sha256": old_zip,
                "portfolio_blob_sha256": old_portfolio,
            },
            after={
                "status": PackStatus.UNAVAILABLE.value,
                "source_record_id": str(source_record.id),
                "source_disposition_event_id": str(source_event.id),
                "worm_destroy_request_id": str(request.id),
                "pack_record_id": str(record.id),
                "pack_record_disposition_event_id": str(event.id),
                "zip_blob_sha256": None,
                "portfolio_blob_sha256": None,
            },
        )

    pack_ids = [pack.id for pack in packs]
    links = list(
        (
            await session.execute(
                select(PackShareLink)
                .where(
                    PackShareLink.pack_id.in_(pack_ids),
                    PackShareLink.revoked_at.is_(None),
                )
                .order_by(asc(PackShareLink.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for link in links:
        link.revoked_at = now
        link.revoked_by = actor.id
        link.revoke_reason = _PACK_ERASURE_REASON
        packs_service.emit_pack_event(
            session,
            actor,
            EventType.PACK_SHARE_REVOKED,
            link.pack_id,
            after={
                "share_link_id": str(link.id),
                "reason": _PACK_ERASURE_REASON,
                "source_disposition_event_id": str(source_event.id),
            },
        )

    records_by_id = {source_record.id: source_record, **pack_records}
    await _lock_r27_blob_rows(
        session,
        list(records_by_id.values()),
        set(artifacts),
    )

    specs = await _mark_record_evidence_for_purge(
        session,
        source_record,
        disposition_event=source_event,
        worm_destroy_request=request,
    )
    for record_id in sorted(events_by_record, key=str):
        if record_id == source_record.id:
            # The source Record was marked immediately above with its root event.
            continue
        specs.extend(
            await _mark_record_evidence_for_purge(
                session,
                pack_records[record_id],
                disposition_event=events_by_record[record_id],
                worm_destroy_request=request,
                source_disposition_event=source_event,
            )
        )
    specs.extend(
        await _mark_detached_pack_artifacts(
            session,
            artifacts,
            request=request,
            source_event=source_event,
        )
    )
    return specs


async def approve_worm_destroy(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    req_id: uuid.UUID,
    *,
    reason: str | None = None,
) -> Record:
    """Second control: a *distinct* actor approves → governance-bypass purge (fail-closed) →
    DISPOSED tombstone (``is_worm_destroy=true``, both actors) → invalidate every sealed pack copy
    in the same DB transaction → ``RECORD_WORM_DESTROYED``."""
    # Lazy for the same package-cycle reason documented in _invalidate_sealed_packs_for_r27.
    from ..packs.locks import lock_pack_erasure_exclusive

    # Fixed lock order: exclusive org-artifact lock before request/source Record and pack rows.
    # Stage 1/2 take the shared side before their pack row, closing the last-copy race.
    await lock_pack_erasure_exclusive(session, actor.org_id)
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

    req.approved_by = actor.id
    req.executed_at = _now()
    event = _write_tombstone(
        session,
        record,
        action=DispositionAction.DESTROY,
        policy_id=None,  # a legal-order destroy is not policy-driven
        approved_by=actor.id,
        requested_by=req.requested_by,
        is_worm_destroy=True,
        legal_basis=req.legal_basis,
    )
    # Resolve every derivative before deleting any source Blob row, then acquire the source +
    # cascade Blob lock set globally by SHA. The request, event, pack invalidations, and purge
    # markers commit atomically; a rollback leaves neither erasure state nor authority.
    specs = await _invalidate_sealed_packs_for_r27(session, actor, record, event, req)
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
    event = _write_tombstone(session, record, action=action, policy_id=policy.id, approved_by=None)
    if action is DispositionAction.DESTROY:
        specs = await _mark_record_evidence_for_purge(
            session,
            record,
            disposition_event=event,
        )
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


async def _authorized_reaper_bypass(
    session: AsyncSession, marker: _PendingPurgeSnapshot
) -> bool | None:
    """Derive a marker's bypass decision from durable disposition authority.

    ``None`` means the bound authority is invalid and the marker must be refused without touching
    S3. Legacy rows predate the authority columns and are intentionally recoverable only through
    the ordinary non-bypass delete path.
    """
    settings = get_settings()
    if marker.bucket not in {
        settings.s3_bucket_records,
        settings.s3_bucket_renditions,
    }:
        return None
    if not marker.authority_bound:
        return False
    if marker.record_id is None or marker.disposition_event_id is None:
        return None

    authority = await repo.get_pending_purge_authority(
        session,
        record_id=marker.record_id,
        disposition_event_id=marker.disposition_event_id,
        worm_destroy_request_id=marker.worm_destroy_request_id,
    )
    if authority is None:
        return None
    record, event, request = authority

    if (
        record.org_id != marker.org_id
        or record.disposition_state is not RecordDispositionState.DISPOSED
        or event.org_id != marker.org_id
        or event.record_id != record.id
        or event.action is not DispositionAction.DESTROY
        or not event.tombstone
    ):
        return None

    if marker.worm_destroy_request_id is None:
        if (
            marker.requested_bypass
            or event.is_worm_destroy
            or event.derived_from_disposition_event_id is not None
            or event.policy_id is None
            or event.policy_id != record.retention_policy_id
            or event.requested_by is not None
            or event.legal_basis is not None
        ):
            return None
        return False

    if request is None or request.id != marker.worm_destroy_request_id:
        return None
    source_event = (
        await session.get(DispositionEvent, event.derived_from_disposition_event_id)
        if event.derived_from_disposition_event_id is not None
        else None
    )
    if not _r27_authority_matches(
        record,
        event,
        request,
        source_event=source_event,
    ):
        return None
    if source_event is not None:
        invalidated_pack_id = await session.scalar(
            select(EvidencePack.id)
            .where(
                EvidencePack.org_id == record.org_id,
                EvidencePack.pack_record_id == record.id,
                EvidencePack.status == PackStatus.UNAVAILABLE,
                EvidencePack.invalidated_by_disposition_event_id == source_event.id,
            )
            .limit(1)
        )
        if invalidated_pack_id is None:
            return None
    return marker.requested_bypass


async def reap_pending_blob_purges(session: AsyncSession) -> dict[str, int]:
    """Crash-recovery for the post-commit purge. A ``pending_blob_purge`` marker survives only when
    the immediate ``_purge_marked`` didn't finish (a crash or storage outage between the disposition
    commit and the physical erase); this backstop completes it — idempotently purge the bytes then
    drop the marker, committing per marker so a mid-run crash re-does at most one. Fields are
    snapshotted per batch so a per-marker commit can't expire an ORM row we read. LOOPS in
    ``exclude_ids`` batches so a persistent-failure cohort in the oldest rows can't starve newer
    purgeable markers within a run. SKIPS the purge (and drops the marker) when a ``blob`` row now
    owns the exact physical location (bucket + object_key), independently of the untrusted marker
    SHA. Bound markers are purged only after their Record/event/request authority validates; legacy
    markers are forced to non-bypass. Returns processed ``reaped`` and authority ``refused`` counts.
    """
    reaped = 0
    refused = 0
    handled: set[uuid.UUID] = set()
    while True:
        markers = await repo.list_pending_purges(session, exclude_ids=handled)
        if not markers:
            break
        todo = [
            _PendingPurgeSnapshot(
                purge_id=m.id,
                org_id=m.org_id,
                sha256=m.sha256,
                bucket=m.bucket,
                object_key=m.object_key,
                requested_bypass=m.bypass_governance,
                record_id=m.record_id,
                disposition_event_id=m.disposition_event_id,
                worm_destroy_request_id=m.worm_destroy_request_id,
                authority_bound=m.authority_bound,
            )
            for m in markers
        ]
        handled.update(marker.purge_id for marker in todo)
        for marker in todo:
            # A prior per-marker commit/rollback released every row claim from the original batch.
            # Reclaim this snapshot before the object lock so immediate purge and every reaper
            # iteration preserve the same marker-row → physical-object order.
            if not await repo.lock_pending_purge_for_update(session, marker.purge_id):
                await session.rollback()
                continue
            await repo.lock_physical_object(
                session,
                bucket=marker.bucket,
                object_key=marker.object_key,
            )
            if await repo.object_is_owned(
                session,
                bucket=marker.bucket,
                object_key=marker.object_key,
            ):
                await repo.delete_pending_purge(session, marker.purge_id)
                await session.commit()
                reaped += 1
                continue

            bypass = await _authorized_reaper_bypass(session, marker)
            if bypass is None:
                logger.warning(
                    "records.reap_purge.authority_refused",
                    extra={
                        "extra_fields": {
                            "purge_id": str(marker.purge_id),
                            "record_id": str(marker.record_id) if marker.record_id else None,
                            "disposition_event_id": (
                                str(marker.disposition_event_id)
                                if marker.disposition_event_id
                                else None
                            ),
                        }
                    },
                )
                await repo.delete_pending_purge(session, marker.purge_id)
                await session.commit()
                reaped += 1
                refused += 1
                continue
            if not marker.authority_bound and marker.requested_bypass:
                logger.warning(
                    "records.reap_purge.legacy_bypass_downgraded",
                    extra={"extra_fields": {"purge_id": str(marker.purge_id)}},
                )
            try:
                await storage.purge_object(
                    marker.object_key,
                    bucket=marker.bucket,
                    bypass_governance=bypass,
                )
            except (ClientError, BotoCoreError):
                logger.warning(
                    "records.reap_purge.failed",
                    extra={
                        "extra_fields": {
                            "sha256": marker.sha256,
                            "bucket": marker.bucket,
                        }
                    },
                )
                # Release this marker's row claim and physical-object transaction lock before
                # moving on; ``handled`` keeps it deferred until the next reaper run.
                await session.rollback()
                continue  # leave the marker; retried on the NEXT run (skipped this run via handled)
            await repo.delete_pending_purge(session, marker.purge_id)
            await session.commit()
            reaped += 1
    return {"reaped": reaped, "refused": refused}
