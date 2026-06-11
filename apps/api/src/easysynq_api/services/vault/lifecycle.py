"""The document-lifecycle use-case layer (slice S4): the FSM transitions + the atomic
single-Effective release cutover + the future-dated Beat sweep.

Each transition validates against the pure FSM (``domain.vault.lifecycle``), mutates the version's
``version_state`` and the document's derived ``current_state``, and emits a vault audit event.
**No ``signature_event`` is written in S4** — the FSM records the transition + audit hook only; S5
(Approval + SoD) wires signature emission via the ``SignatureEventSink`` seam (left available here).

The release cutover (T6 + T10) is the one transaction that must be *atomic and serializable*: it
runs in its own ``SERIALIZABLE`` session (separate from the request session, so the per-request
authz read stays READ COMMITTED and the cutover's footprint is just the doc + its versions — no
false conflicts
on shared permission-catalog rows), takes a ``SELECT … FOR UPDATE`` row lock on the document (a
single, consistently-ordered lock → deadlock-free), and relies on the INV-1 partial unique index as
the structural backstop. A concurrent loser surfaces as a serialization failure (40001) or an INV-1
unique violation (23505) → rolled back → ``409`` (register R8 / doc 18 §5.2; AC#1).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ...config import get_settings
from ...db.models._vault_enums import VersionState
from ...db.models.app_user import AppUser
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.working_draft import WorkingDraft
from ...db.session import get_sessionmaker
from ...domain.vault import Action, IllegalTransition, allowed_actions, apply_transition
from ...logging import request_id_var
from ...problems import ProblemException
from ..ack.sink import get_ack_enqueue_sink
from . import locks, repository
from .audit import VaultAuditEvent, VaultAuditSink, get_vault_audit_sink
from .mirror_sink import get_mirror_enqueue_sink
from .obsoletion import assert_obsoletion_allowed
from .review import REVIEW_PERIOD_DEFAULT_MONTHS, compute_next_review_due
from .signature import SignatureEvent, SignatureEventSink, get_vault_signature_sink

logger = logging.getLogger("easysynq.vault")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _audit(
    session: AsyncSession,
    sink: VaultAuditSink,
    event_type: str,
    actor: AppUser | None,
    org_id: uuid.UUID,
    obj_type: str,
    obj_id: uuid.UUID,
    *,
    identifier: str | None = None,
    reason: str | None = None,
) -> None:
    """Append the lifecycle ``audit_event`` row to ``session`` BEFORE its commit (doc 12 §4.4 /
    AC#6). ``actor=None`` is a system/Beat actor (release sweep) → ``actor_type='system'``."""
    sink.record(
        session,
        VaultAuditEvent(
            occurred_at=_now(),
            event_type=event_type,
            actor_id=str(actor.id) if actor else "system",
            org_id=str(org_id),
            object_type=obj_type,
            object_id=str(obj_id),
            identifier=identifier,
            reason=reason,
            request_id=request_id_var.get(),
        ),
    )


def _is_race_loss(exc: DBAPIError) -> bool:
    """True if a cutover commit failed because a concurrent release won — a serialization failure
    (SQLSTATE 40001), a deadlock (40P01), or a unique violation (23505). The cutover performs **only
    UPDATEs**, so the only unique constraints it can ever violate are the two partial indexes it
    targets — INV-1 (single Effective version) and R25 (single Effective singleton per org/type) —
    both of which mean another release / an already-Effective singleton took the slot. Treating any
    23505 here as a loss is therefore correct and robust to a missing psycopg ``constraint_name``.
    SQLSTATE is read defensively. Anything else propagates as a real error."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return sqlstate in ("40001", "40P01", "23505")


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionResult:
    """A completed FSM mutation that has NOT been committed or audited yet. The caller (the decision
    handler / the submit-review endpoint) calls :func:`audit_transition` to append the audit row to
    the session and then commits the unit of work, so the FSM mutation + its audit row commit (or
    roll back) atomically — no action without its audit row, no phantom (doc 12 §4.4 / AC#6)."""

    doc: DocumentedInformation
    version: DocumentVersion
    event_type: str
    reason: str | None = None


def audit_transition(
    session: AsyncSession, sink: VaultAuditSink, result: TransitionResult, actor: AppUser
) -> None:
    """Append the vault audit event for a :class:`TransitionResult` to ``session`` BEFORE its
    commit, so it commits atomically with the FSM mutation it records (doc 12 §4.4 / AC#6)."""
    _audit(
        session,
        sink,
        result.event_type,
        actor,
        result.doc.org_id,
        "document_version",
        result.version.id,
        identifier=result.doc.identifier,
        reason=result.reason,
    )


def _emit_signature(
    sig_sink: SignatureEventSink,
    session: AsyncSession,
    version: DocumentVersion,
    meaning: str,
    actor: AppUser | None,
    *,
    intent: str | None = None,
) -> None:
    """Append a ``signature_event`` for a version-level decision to the active session (no commit;
    flushed atomically with the surrounding txn). ``actor=None`` is a system/Beat release."""
    sig_sink.record(
        session,
        SignatureEvent(
            org_id=version.org_id,
            signer_user_id=actor.id if actor else None,
            signed_object_id=version.id,
            meaning=meaning,
            content_digest=version.source_blob_sha256,
            intent=intent,
            auth_context={"acr": "SESSION"} if actor else {"acr": "SESSION", "system": True},
        ),
    )


async def _advance_active_version(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    action: Action,
    event_type: str,
    *,
    reason: str | None = None,
) -> TransitionResult:
    """Advance the document's active (latest) version through a single-version transition
    (submit-review / request-changes). The FSM validates against the document's ``current_state``;
    a state/version mismatch is a defensive illegal transition. **Mutate-only** — the caller commits
    and audits."""
    transition = apply_transition(doc.current_state, action)
    version = await repository.latest_version(session, doc.id)
    new_state = transition.to_version_state
    if (
        version is None
        or new_state is None
        or version.version_state is not transition.from_version_state
    ):
        raise IllegalTransition(action, doc.current_state, allowed_actions(doc.current_state))
    version.version_state = new_state
    doc.current_state = transition.to_doc_state
    doc.updated_by = actor.id
    return TransitionResult(doc=doc, version=version, event_type=event_type, reason=reason)


async def submit_review(
    session: AsyncSession, actor: AppUser, doc: DocumentedInformation
) -> TransitionResult:
    """T2 (Draft → InReview) / T9 (UnderRevision → InReview). Acts on the latest checked-in Draft
    version (the check-in already released the edit lock, so this is lock-free). **Mutate-only** —
    the submit-review endpoint commits, instantiates the approval workflow, and audits."""
    # S9: a document must address >=1 ISO clause before it enters review (doc 15 §8.5 / doc 04
    # §6.1 / doc 14 §4). Counted on the DOCUMENT (clause_mapping is keyed to documented_information,
    # not the version), so a revision (T9) keeps its mappings; the gate covers both T2 and T9.
    if await repository.count_clause_mappings(session, doc.id) == 0:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Submit-review requires at least one clause mapping",
            errors=[
                {
                    "field": "clause_mappings",
                    "code": "required",
                    "message": "map the document to ≥1 ISO clause before submitting for review",
                }
            ],
        )
    if doc.review_period_months is None:
        # T2 auto-default (spec §3 amendment): the create-default applied late, so a legacy doc
        # is never stranded at submit while the SPA lacks the field (pre-S-web-8).
        doc.review_period_months = REVIEW_PERIOD_DEFAULT_MONTHS
    return await _advance_active_version(
        session, actor, doc, Action.submit_review, "SUBMITTED_FOR_REVIEW"
    )


async def approve(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    effective_from: datetime.datetime | None = None,
) -> TransitionResult:
    """T4 (InReview → Approved). Records an optional *scheduled* ``effective_from`` (R8: stored
    ``timestamptz`` in UTC); left NULL for an immediate approval (release is then a separate
    SoD-2-gated act, not a Beat auto-release). **Mutate-only** — the decision handler commits, emits
    ``signature_event(meaning=approval)``, writes the ``task_outcome``, and audits."""
    transition = apply_transition(doc.current_state, Action.approve)
    version = await repository.latest_version(session, doc.id)
    new_state = transition.to_version_state
    if (
        version is None
        or new_state is None
        or version.version_state is not transition.from_version_state
    ):
        raise IllegalTransition(
            Action.approve, doc.current_state, allowed_actions(doc.current_state)
        )
    version.version_state = new_state
    # Only a *scheduled* (explicit future) effective_from makes a version Beat-eligible. Immediate
    # approval leaves it NULL, so the Beat sweep (which filters effective_from IS NOT NULL) never
    # auto-releases it — release stays a separate SoD-2-gated act (else allow_approver_release=False
    # is defeated by the sweep). The cutover sets effective_from at release.
    version.effective_from = effective_from
    doc.current_state = transition.to_doc_state
    doc.updated_by = actor.id
    return TransitionResult(doc=doc, version=version, event_type="APPROVED")


async def request_changes(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    comment: str,
) -> TransitionResult:
    """T3 (InReview → Draft). Returns the document to Draft with a required reviewer comment; the
    version's ``version_state`` reverts to Draft (a fresh check-out creates the next version).
    **Mutate-only** — the decision handler commits + audits (no signature on request-changes)."""
    if not comment or not comment.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Request-changes requires a comment",
            errors=[{"field": "comment", "code": "required", "message": "must be non-empty"}],
        )
    return await _advance_active_version(
        session, actor, doc, Action.request_changes, "CHANGES_REQUESTED", reason=comment.strip()
    )


async def start_revision(
    session: AsyncSession, sink: VaultAuditSink, actor: AppUser, doc: DocumentedInformation
) -> DocumentedInformation:
    """T7 (Effective → UnderRevision). Acquires the edit lock and opens a working draft seeded from
    the Effective version, which keeps governing; the new draft version is produced by the existing
    S3 check-in path, then T9 submit-review advances it."""
    transition = apply_transition(doc.current_state, Action.start_revision)  # requires Effective
    source_id = doc.current_effective_version_id
    if source_id is None:
        # Defensive: an Effective document must always have a governing version. (Can only arise
        # from out-of-band corruption — the cutover always sets the pointer atomically.)
        raise ProblemException(
            status=409,
            code="conflict",
            title="Document has no governing version",
            detail="current_state is Effective but current_effective_version_id is null",
        )
    token = await locks.acquire(doc.id)
    if token is None:
        raise ProblemException(
            status=409,
            code="lock_conflict",
            title="Document is checked out",
            detail="a revision or edit is already in progress",
        )
    wd = await repository.get_working_draft(session, doc.id)
    if wd is None:
        wd = WorkingDraft(
            org_id=actor.org_id,
            document_id=doc.id,
            checked_out_by=actor.id,
            source_version_id=source_id,
            lock_token=token,
        )
        session.add(wd)
    else:  # stale row preserved by a prior break-lock — this acquirer takes over (R9)
        wd.checked_out_by = actor.id
        wd.source_version_id = source_id
        wd.lock_token = token
        wd.checked_out_at = _now()
    doc.current_state = transition.to_doc_state
    doc.updated_by = actor.id
    _audit(
        session,
        sink,
        "REVISION_STARTED",
        actor,
        doc.org_id,
        "document",
        doc.id,
        identifier=doc.identifier,
    )
    await session.commit()
    await session.refresh(doc)
    return doc


async def obsolete(
    session: AsyncSession,
    sink: VaultAuditSink,
    sig_sink: SignatureEventSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    reason: str,
    version_id: uuid.UUID | None = None,
    force_retire: bool = False,
    override_justification: str | None = None,
) -> DocumentedInformation:
    """T11 (Effective → Obsolete; clears the effective pointer) or, when a Superseded ``version_id``
    is given, the version-level T12 (Superseded version → Obsolete; document state unchanged).
    Emits ``signature_event(meaning=obsolete)`` in the same transaction (S5).

    The doc 05 §7.3 obsoletion-safety gate (S-dcr-5) fires on the **T11 document-level** path only
    (a T12 Superseded-version archive removes no Effective coverage): a coverage/dependency gap is a
    409 ``obsoletion_blocked`` unless ``force_retire`` + a non-empty ``override_justification``
    (recorded on the signature intent + audit). Both the direct ``document.obsolete`` endpoint and
    the DCR RETIRE-implement reach this gate — one check covers both paths."""
    if not reason or not reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Obsolete requires a reason",
            errors=[{"field": "reason", "code": "required", "message": "must be non-empty"}],
        )
    reason = reason.strip()

    if version_id is not None:
        version = await session.get(DocumentVersion, version_id)
        if version is None or version.document_id != doc.id:
            raise ProblemException(status=404, code="not_found", title="Version not found")
        if version.version_state is VersionState.Superseded:  # T12 — version-level archive
            version.version_state = VersionState.Obsolete
            doc.updated_by = actor.id
            _emit_signature(sig_sink, session, version, "obsolete", actor, intent=reason)
            _audit(
                session,
                sink,
                "MADE_OBSOLETE",
                actor,
                doc.org_id,
                "document_version",
                version.id,
                identifier=doc.identifier,
                reason=reason,
            )
            await session.commit()
            await session.refresh(doc)
            return doc
        if version.version_state is not VersionState.Effective:
            # A version-targeted obsolete only makes sense for a Superseded (T12) or the Effective
            # (T11) version — be explicit about the version state rather than the document state.
            raise ProblemException(
                status=409,
                code="invalid_state_transition",
                title="Version cannot be obsoleted",
                detail=(
                    "version_id must reference an Effective or Superseded version "
                    f"(is {version.version_state.value})"
                ),
            )
        # else: it is the Effective version — fall through to the T11 document obsolete.

    transition = apply_transition(doc.current_state, Action.obsolete)  # requires Effective
    effective = None
    if doc.current_effective_version_id is not None:
        effective = await session.get(DocumentVersion, doc.current_effective_version_id)
    if effective is None:
        raise IllegalTransition(
            Action.obsolete, doc.current_state, allowed_actions(doc.current_state)
        )
    # The doc 05 §7.3 gate (S-dcr-5): block a coverage-gap obsoletion unless force_retire +
    # justification. Runs ONLY here (T11 document-level) — the T12 archive returned early above.
    await assert_obsoletion_allowed(
        session,
        doc.org_id,
        doc.id,
        force_retire=force_retire,
        override_justification=override_justification,
    )
    # A forced retire records its justification on the e-signature intent + the audit reason (§7.3).
    retire_reason = f"{reason} | force_retire: {override_justification}" if force_retire else reason
    effective.version_state = VersionState.Obsolete
    doc.current_effective_version_id = None
    doc.current_state = transition.to_doc_state
    doc.updated_by = actor.id
    _emit_signature(sig_sink, session, effective, "obsolete", actor, intent=retire_reason)
    _audit(
        session,
        sink,
        "MADE_OBSOLETE",
        actor,
        doc.org_id,
        "document_version",
        effective.id,
        identifier=doc.identifier,
        reason=retire_reason,
    )
    await session.commit()
    await session.refresh(doc)
    # S7: T11 pulled the Effective version from the mirror — rebuild post-commit. (The T12 path
    # above obsoletes an already-Superseded version, never in the mirror, so it does not enqueue.)
    get_mirror_enqueue_sink().enqueue("obsolete")
    # S-ack-1: an Obsoleted doc's open obligations lapse — the doc-scoped sweep cancels them.
    get_ack_enqueue_sink().enqueue(str(doc.id), trigger="obsolete")
    return doc


# --- the atomic single-Effective cutover (T6 + T10) -------------------------------------


async def _cutover(
    session: AsyncSession,
    sink: VaultAuditSink,
    sig_sink: SignatureEventSink,
    doc_id: uuid.UUID,
    version_id: uuid.UUID | None,
    actor: AppUser | None,
    now: datetime.datetime,
) -> DocumentedInformation:
    """The atomic supersession, assuming ``session`` is already SERIALIZABLE. Locks the document
    row, validates the FSM, supersedes the prior Effective, makes the target Effective, and repoints
    ``current_effective_version_id`` — all in one transaction."""
    doc = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == doc_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if doc is None or (actor is not None and doc.org_id != actor.org_id):
        raise ProblemException(status=404, code="not_found", title="Document not found")

    transition = apply_transition(
        doc.current_state, Action.release
    )  # Approved → Effective else 409

    if version_id is not None:
        version = await session.get(DocumentVersion, version_id)
        if version is None or version.document_id != doc.id:
            raise ProblemException(status=404, code="not_found", title="Version not found")
    else:
        version = (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_state == VersionState.Approved,
                )
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version is None or version.version_state is not transition.from_version_state:
        raise IllegalTransition(
            Action.release, doc.current_state, allowed_actions(doc.current_state)
        )

    eff_from = version.effective_from or now
    if eff_from > now:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="effective_from is in the future",
            detail=f"scheduled for {eff_from.isoformat()}; the release sweep will activate it",
        )

    prior = (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_state == VersionState.Effective,
            )
        )
    ).scalar_one_or_none()
    if prior is not None and prior.id != version.id:
        prior.version_state = VersionState.Superseded
        prior.effective_to = now
        prior.superseded_by_version_id = version.id
        # Demote the prior Effective BEFORE promoting the new one: INV-1 is an immediate
        # (non-deferrable) partial unique index, so the unit-of-work must not emit the new
        # version's →Effective update while the prior is still Effective (transient duplicate).
        await session.flush()

    version.version_state = VersionState.Effective
    version.effective_from = eff_from
    doc.current_effective_version_id = version.id
    doc.current_state = transition.to_doc_state  # Effective
    if actor is not None:
        doc.updated_by = actor.id
    doc.next_review_due = compute_next_review_due(
        doc.review_period_months, doc.last_reviewed_at, eff_from
    )

    # T6: append the release signature + the RELEASED/SUPERSEDED audit rows INSIDE the cutover txn
    # (before commit) so they are atomic with the promotion and roll back with a race loser — no
    # phantom RELEASED row for the loser of a concurrent release (AC#6 / AC#1b). ``actor=None`` is
    # the system/Beat release.
    _emit_signature(sig_sink, session, version, "release", actor)
    _audit(
        session,
        sink,
        "RELEASED",
        actor,
        doc.org_id,
        "document_version",
        version.id,
        identifier=doc.identifier,
    )
    if prior is not None and prior.id != version.id:
        _audit(
            session,
            sink,
            "SUPERSEDED",
            actor,
            doc.org_id,
            "document_version",
            prior.id,
            identifier=doc.identifier,
        )

    await session.commit()  # INV-1 + SERIALIZABLE adjudicate the race here
    await session.refresh(doc)
    return doc


async def release(
    actor: AppUser,
    doc_id: uuid.UUID,
    sink: VaultAuditSink,
    sig_sink: SignatureEventSink,
    *,
    version_id: uuid.UUID | None = None,
) -> DocumentedInformation:
    """T6 (Approved → Effective). Runs :func:`_cutover` in a dedicated SERIALIZABLE session; a
    concurrent-release loser (40001/40P01/INV-1 23505) is rolled back and surfaced as 409. The
    ``signature_event(meaning=release)`` is emitted inside the cutover txn (S5)."""
    now = _now()
    async with get_sessionmaker()() as session:
        # Raise isolation before any statement opens the transaction.
        await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
        try:
            doc = await _cutover(session, sink, sig_sink, doc_id, version_id, actor, now)
        except DBAPIError as exc:
            await session.rollback()
            if _is_race_loss(exc):
                raise ProblemException(
                    status=409,
                    code="conflict",
                    title="Concurrent release conflict",
                    detail="another release set the Effective version concurrently; reload + retry",
                ) from exc
            raise
    # S7: enqueue the mirror rewrite AFTER the cutover commits — never inside the SERIALIZABLE txn,
    # so a concurrent-release loser (rolled back above) does not enqueue (doc 15 §8.5).
    get_mirror_enqueue_sink().enqueue("release")
    # S-ack-1: a fresh Effective version may re-arm acknowledgements (MAJOR) — doc-scoped sweep.
    get_ack_enqueue_sink().enqueue(str(doc_id), trigger="release")
    return doc


async def release_due(now: datetime.datetime | None = None) -> list[uuid.UUID]:
    """The Beat cutover sweep: release every Approved version whose ``effective_from <= now``
    (future-dated go-live). Each cutover runs in its own SERIALIZABLE transaction, reusing
    :func:`_cutover`; a version another sweep/manual release already took is skipped. Uses a
    dedicated, disposed engine so it is safe to call inside a Celery task's ``asyncio.run``."""
    now = now or _now()
    sink = get_vault_audit_sink()
    sig_sink = get_vault_signature_sink()
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    released: list[uuid.UUID] = []
    released_docs: list[uuid.UUID] = []
    try:
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    select(DocumentVersion.id, DocumentVersion.document_id).where(
                        DocumentVersion.version_state == VersionState.Approved,
                        DocumentVersion.effective_from.is_not(None),
                        DocumentVersion.effective_from <= now,
                    )
                )
            ).all()
        for version_id, doc_id in rows:
            async with sessionmaker() as session:
                await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
                try:
                    await _cutover(session, sink, sig_sink, doc_id, version_id, None, now)
                    released.append(version_id)
                    released_docs.append(doc_id)
                except DBAPIError as exc:
                    await session.rollback()
                    if not _is_race_loss(exc):
                        logger.warning("release_due: cutover failed for %s: %s", version_id, exc)
                except (ProblemException, IllegalTransition):
                    # The document moved past Approved between the scan and the cutover (a
                    # concurrent release won, or it was handled manually) — skip it.
                    await session.rollback()
        # S7: one idempotent full-rebuild enqueue covers every version this sweep activated.
        if released:
            get_mirror_enqueue_sink().enqueue("release_due")
            # S-ack-1: per released doc — the scoped sweep re-arms/mints against the fresh version.
            for sweep_doc_id in released_docs:
                get_ack_enqueue_sink().enqueue(str(sweep_doc_id), trigger="release_due")
    finally:
        await engine.dispose()
    return released
