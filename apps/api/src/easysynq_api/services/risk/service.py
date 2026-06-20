"""Risk & Opportunity register service (clause 6.1, S-risk-1) — the txn owner for the risk rows.

The register is ONE ``kind=DOCUMENT`` ``RSK`` head (``is_singleton``) per org holding many
``risk_opportunity`` satellite rows. ``resolve_or_create_head`` is an advisory-lock-serialized
get-or-create (the only multi-head window is the concurrent first-create; ``RSK`` revisions in
place,
so the existing Effective-only ``uq_doc_info_singleton_effective`` covers the rest). Rows are
editable
only while the head is Draft/UnderRevision (the ``form_template``/objectives edit gate).
``risk_rating``
is re-derived from ``likelihood x severity`` on every write (never client-supplied); a re-score
emits
``RISK_RESCORED``. The controlled-document publish/freeze/release lifecycle (and the
governing-snapshot
band resolve) is the deferred **S-risk-1b** — the head stays Draft in S-risk-1 (a working register).
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaSource, NcSeverity
from ...db.models._risk_enums import RiskOpportunityType, ScoringMethod
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.capa import Capa
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.process import Process
from ...db.models.risk_opportunity import RiskOpportunity
from ...domain.authz import ResourceContext
from ...domain.risk.register_content import resolve_criteria
from ...domain.risk.rules import RiskBand, risk_band, risk_rating
from ...problems import ProblemException
from ..authz import AuthzAuditSink, enforce
from ..capa import repository as capa_repo
from ..capa.service import build_capa
from ..vault import VaultAuditSink, create_document
from ..vault import repository as vault_repo
from .queries import governing_register

# A per-org transaction advisory lock that serializes the concurrent *first* head-create. The
# two-arg form (ns, oid) is distinct from the single-arg global LOCK_* keys; it auto-releases at the
# txn boundary (incl. create_document's internal commit). Only taken when no head is found.
_RISK_HEAD_LOCK_NS = 7710100
_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


def _org_head_lock_oid(org_id: uuid.UUID) -> int:
    """A stable signed int32 from the org id (PostgreSQL advisory keys are int4 in the two-arg
    form)."""
    return int.from_bytes(hashlib.blake2b(org_id.bytes, digest_size=4).digest(), "big", signed=True)


async def _rsk_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "RSK")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422, code="validation_error", title="RSK document_type is not seeded"
        )
    return dt.id


async def find_head(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> DocumentedInformation | None:
    """The single non-Obsolete RSK head for the org (Effective OR its Draft/UnderRevision
    successor) — the one source of truth for "the org's register head" (used by the get-or-create,
    the publish/revision lifecycle, and the GET status read). ``for_update`` locks ONLY the head row
    + forces populate_existing (the S-drift-1 stale-identity-map trap: a locking SELECT returns the
    cached attributes unless populate_existing overrides them)."""
    stmt = (
        select(DocumentedInformation)
        .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
        .where(
            DocumentedInformation.org_id == org_id,
            DocumentType.code == "RSK",
            DocumentedInformation.current_state != DocumentCurrentState.Obsolete,
        )
        .order_by(DocumentedInformation.created_at)
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update(of=DocumentedInformation).execution_options(
            populate_existing=True
        )
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_or_create_head(
    session: AsyncSession, vault_sink: VaultAuditSink, actor: AppUser
) -> DocumentedInformation:
    """Get-or-create the org's single RSK register head. The head carries ZERO ProcessLinks
    (``create_document(processes=())``) — a row's process_id lives on the satellite, never the head,
    so a bound Process-Owner's PROCESS-scoped ``document.*`` grant cannot match the org head
    (L1-MAJOR). Auto-maps the head to clause 6.1. The advisory lock guards the concurrent
    first-create
    (double-checked under the lock)."""
    head = await find_head(session, actor.org_id)
    if head is not None:
        return head
    # No head — serialize the create so two concurrent first-risk POSTs cannot mint two heads.
    await session.execute(
        # Cast to int4 so PG resolves the two-arg pg_advisory_xact_lock(int, int) overload (psycopg
        # would otherwise bind the Python ints as bigint, for which no two-arg form exists).
        text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:oid AS integer))"),
        {"ns": _RISK_HEAD_LOCK_NS, "oid": _org_head_lock_oid(actor.org_id)},
    )
    head = await find_head(session, actor.org_id)  # re-check under the lock
    if head is not None:
        return head
    dt_id = await _rsk_document_type_id(session, actor.org_id)
    head = await create_document(  # commits the base doc (releases the xact lock)
        session,
        vault_sink,
        actor,
        title="Risk & Opportunity Register",
        document_type_id=dt_id,
    )
    clause_6_1 = (
        await session.execute(
            select(Clause).where(Clause.number == "6.1", Clause.framework_id == head.framework_id)
        )
    ).scalar_one_or_none()
    if clause_6_1 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=head.framework_id,
                clause_id=clause_6_1.id,
                documented_information_id=head.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
        await session.commit()
        await session.refresh(head)
    return head


async def _validate_process(session: AsyncSession, actor: AppUser, process_id: uuid.UUID) -> None:
    proc = await session.get(Process, process_id)
    if proc is None or proc.org_id != actor.org_id:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unknown process_id (must be a process in your organization)",
        )


def _risk_resource(process_id: uuid.UUID | None) -> ResourceContext:
    """The PROCESS scope from a row's own ``process_id`` (SYSTEM when none). Mirrors the route's
    ``_risk_scope`` — kept here to avoid the service↔api circular import for the under-lock
    re-auth."""
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _validate_clause(
    session: AsyncSession, framework_id: uuid.UUID, clause_id: uuid.UUID
) -> None:
    clause = await session.get(Clause, clause_id)
    if clause is None or clause.framework_id != framework_id:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unknown clause_id (must be a clause in your framework)",
        )


async def _org_framework_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    framework = await vault_repo.get_framework(session, org_id)
    if framework is None:
        raise ProblemException(status=422, code="validation_error", title="No framework configured")
    return framework.id


async def add_risk_row(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    *,
    type: RiskOpportunityType,
    description: str,
    likelihood: int,
    severity: int,
    scoring_method: ScoringMethod,
    process_id: uuid.UUID | None = None,
    clause_id: uuid.UUID | None = None,
    treatment: str | None = None,
) -> RiskOpportunity:
    """Author a risk row on the register's working satellite. ``risk_rating`` is server-derived. The
    head must be Draft/UnderRevision (the edit gate; in S-risk-1 the head stays Draft)."""
    # Validate FK inputs BEFORE resolve_or_create_head (which COMMITS the head on the first risk) so
    # a bad clause_id/process_id never orphans an empty register doc + consumed identifier.
    if process_id is not None:
        await _validate_process(session, actor, process_id)
    if clause_id is not None:
        await _validate_clause(session, await _org_framework_id(session, actor.org_id), clause_id)
    await resolve_or_create_head(session, vault_sink, actor)
    # Re-load the head FOR UPDATE so the editable-gate + the insert serialize against a concurrent
    # publish (which holds the head FOR UPDATE while it freezes the rows): without this lock a row
    # insert could commit AFTER the freeze + the head moves to InReview, leaving live register
    # content out of the version the approver signs and un-editable once Effective (Codex P1).
    # find_head excludes Obsolete → None only on a concurrent retire (RSK is reserved from it).
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None or head.current_state not in _EDITABLE:
        state = head.current_state.value if head is not None else "obsolete"
        raise ProblemException(
            status=409,
            code="conflict",
            title="Risk register is not editable",
            detail=f"current_state is {state}; start a revision to edit",
        )
    rating = risk_rating(likelihood, severity, scoring_method)
    row = RiskOpportunity(
        register_doc_id=head.id,
        org_id=actor.org_id,
        type=type,
        description=description,
        process_id=process_id,
        clause_id=clause_id,
        likelihood=likelihood,
        severity=severity,
        risk_rating=rating,
        scoring_method=scoring_method,
        treatment=treatment,
        row_version=1,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


_NON_NULL_FIELDS = frozenset({"type", "description", "likelihood", "severity", "scoring_method"})
_UPDATABLE_FIELDS = frozenset(
    {
        "type",
        "description",
        "likelihood",
        "severity",
        "scoring_method",
        "process_id",
        "clause_id",
        "treatment",
        "effectiveness",
    }
)


async def get_risk(
    session: AsyncSession, risk_id: uuid.UUID, *, for_update: bool = False
) -> RiskOpportunity | None:
    stmt = select(RiskOpportunity).where(RiskOpportunity.id == risk_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_risks(session: AsyncSession, org_id: uuid.UUID) -> list[RiskOpportunity]:
    return list(
        (
            await session.execute(
                select(RiskOpportunity)
                .where(RiskOpportunity.org_id == org_id)
                .order_by(RiskOpportunity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def update_risk_row(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    actor: AppUser,
    risk_id: uuid.UUID,
    *,
    updates: dict[str, Any],
) -> RiskOpportunity:
    """Apply a partial PATCH to a risk row. ``risk_rating`` is re-derived in the SAME txn when
    likelihood/severity change (a re-score → ``RISK_RESCORED`` audit); ``scoring_method`` is
    write-once (a change → 422). Reassigning ``process_id`` is re-authorized over the NEW target by
    the caller (the route) BEFORE this call; here we ALSO re-authorize ``register.manage`` over the
    row's CURRENT (freshly-locked) process — the path dependency authorized a PRE-lock read that a
    concurrent reassign may have invalidated (the S-records-W under-lock re-auth, a TOCTOU close).
    Rows are editable only while the head is Draft/UnderRevision."""
    row = await get_risk(session, risk_id, for_update=True)
    if row is None or row.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Risk not found")
    await enforce(
        session, authz_sink, request, actor, "register.manage", _risk_resource(row.process_id)
    )
    # Lock the head FOR UPDATE (after the row lock — a consistent row→head order; publish locks only
    # the head, so no cycle): the editable-gate + the update serialize against a concurrent publish
    # freeze, so a row edit cannot land after the version is frozen (Codex P1; the S-records-W
    # TOCTOU discipline). populate_existing overrides the authz-resolver's identity-mapped read.
    head = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == row.register_doc_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if head.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Risk register is not editable",
            detail=f"current_state is {head.current_state.value}; start a revision to edit",
        )
    unknown = set(updates) - _UPDATABLE_FIELDS
    if unknown:
        raise ProblemException(
            status=422, code="validation_error", title=f"Unknown field(s): {sorted(unknown)}"
        )
    for field in _NON_NULL_FIELDS & set(updates):
        if updates[field] is None:
            raise ProblemException(
                status=422, code="validation_error", title=f"{field} may not be null"
            )
    if "scoring_method" in updates and updates["scoring_method"] != row.scoring_method:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="scoring_method is write-once (mint a new method to change the scheme)",
        )
    if updates.get("process_id") is not None:
        await _validate_process(session, actor, updates["process_id"])
    if updates.get("clause_id") is not None:
        await _validate_clause(session, head.framework_id, updates["clause_id"])

    before = {
        "likelihood": row.likelihood,
        "severity": row.severity,
        "risk_rating": row.risk_rating,
        "scoring_method": row.scoring_method.value,
    }
    for field in _UPDATABLE_FIELDS & set(updates):
        setattr(row, field, updates[field])
    rescored = "likelihood" in updates or "severity" in updates
    if rescored:
        row.risk_rating = risk_rating(row.likelihood, row.severity, row.scoring_method)
    row.row_version += 1
    row.updated_by = actor.id

    if rescored:
        session.add(
            AuditEvent(
                org_id=actor.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.RISK_RESCORED,
                object_type=AuditObjectType.document,
                object_id=row.register_doc_id,
                scope_ref=head.identifier,
                before=before,
                after={
                    "risk_id": str(row.id),
                    "likelihood": row.likelihood,
                    "severity": row.severity,
                    "risk_rating": row.risk_rating,
                    "scoring_method": row.scoring_method.value,
                },
            )
        )
    await session.commit()
    await session.refresh(row)
    return row


# The risk band → CAPA severity map for the auto-derived spawn severity (S-risk-3): the 4-band RAG
# collapses onto the 3-value NcSeverity — Critical→Critical, High→Major, {Medium,Low}→Minor — so
# only the highest risks route the CAPA's two-tier (QMS-Owner → Top-Management) Critical approval.
# Total over RiskBand (``unscored`` is unreachable for a real row — likelihood/severity are NOT NULL
# so risk_rating ≥ 1 → a real band — but mapped defensively so the lookup never KeyErrors).
_BAND_TO_CAPA_SEVERITY: dict[RiskBand, NcSeverity] = {
    RiskBand.critical: NcSeverity.Critical,
    RiskBand.high: NcSeverity.Major,
    RiskBand.medium: NcSeverity.Minor,
    RiskBand.low: NcSeverity.Minor,
    RiskBand.unscored: NcSeverity.Minor,
}


async def spawn_capa_for_risk(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    actor: AppUser,
    risk_id: uuid.UUID,
) -> tuple[Capa, bool]:
    """Idempotently spawn a CAPA to TREAT a risk row (clause 6.1 §7; the complaint→CAPA shape).
    Returns ``(capa, created)`` — ``created`` is False on an idempotent replay (the risk already
    latched a CAPA).

    ``linked_capa_id`` is OPERATIONAL metadata (S-risk-3 — excluded from the frozen version
    content), so the spawn works at ANY register head state: there is NO editable gate and NO head
    lock — only the RISK row is held ``FOR UPDATE`` across check-then-spawn (R16's real idempotency
    guard is the parent-row lock, NOT a UNIQUE: two spawns would otherwise mint two distinct CAPA
    ids that never collide). ``get_risk(for_update=True)`` forces ``populate_existing`` so the
    locking load overrides the route's pre-lock identity-mapped read (the S-drift-1 trap: the
    ``_risk_path_scope`` resolver already ``session.get``-loaded the row during authz).

    Authz: ``capa.create`` is re-authorized over the row's CURRENT (freshly-locked) ``process_id``
    here — the route gated a PRE-lock read a concurrent PATCH-reassign may have invalidated, and the
    spawned CAPA inherits that process, so this re-auth is the real escalation boundary (the
    S-records-W TOCTOU close). The CAPA's ``process_id`` is the risk's OWN already-authorized
    column, so the spawn does not re-open the records/CAPA escalation surface (spec §7).
    ``severity`` auto-derives from the governing-graded band."""
    row = await get_risk(session, risk_id, for_update=True)
    if row is None or row.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Risk not found")
    # TOCTOU close: re-authorize capa.create over the row's freshly-locked process (SYSTEM when None
    # → an org-level risk's CAPA is org-level, SYSTEM-only). A null process_id reaches here only for
    # a SYSTEM grant; a bound owner whose row was reassigned out from under them is denied.
    await enforce(
        session, authz_sink, request, actor, "capa.create", _risk_resource(row.process_id)
    )
    if row.linked_capa_id is not None:
        existing = await capa_repo.get_capa(session, row.linked_capa_id)
        await session.commit()  # release the FOR UPDATE lock promptly (no mutation on the replay)
        # Org-check the loaded CAPA too (defense-in-depth) — a risk can only ever latch a same-org
        # CAPA the spawn itself created.
        if existing is None or existing.org_id != actor.org_id:
            raise ProblemException(status=404, code="not_found", title="CAPA not found")
        return existing, False

    # Auto-derive the CAPA severity from the live band, graded against the GOVERNING frozen criteria
    # (R49 L2 — the same basis the serializer + MR summary use; never a live module constant).
    governing = await governing_register(session, actor.org_id)
    band = risk_band(row.risk_rating, resolve_criteria(governing, row.scoring_method))
    severity = _BAND_TO_CAPA_SEVERITY[band]
    head = await session.get(DocumentedInformation, row.register_doc_id)
    head_identifier = head.identifier if head is not None else None
    capa = await build_capa(
        session,
        actor,
        title=f"CAPA — treat risk: {row.description}"[:200],
        severity=severity,
        source=CapaSource.risk,
        process_id=row.process_id,
        raised_block={
            "source": CapaSource.risk.value,
            "risk_id": str(row.id),
            "register_doc_id": str(row.register_doc_id),
            "risk_identifier": head_identifier,
            "risk_rating": row.risk_rating,
            "band": band.value,
            "risk_description": row.description,
            "severity": severity.value,
        },
        _commit=False,
    )
    row.linked_capa_id = capa.id
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.RISK_SPAWNED_CAPA,
            object_type=AuditObjectType.document,
            object_id=row.register_doc_id,
            scope_ref=head_identifier,
            after={
                "risk_id": str(row.id),
                "linked_capa_id": str(capa.id),
                "band": band.value,
                "severity": severity.value,
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return capa, True
