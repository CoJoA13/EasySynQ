"""Engine-routed, signed Top-Management *release authorization* for leadership artifacts (slice
S-leadership-1; doc 02 Cl 5.2/6.2/9.3, doc 10 §2.5, decisions-register R45/R2).

The OPT-IN, additive Top-Management gate the welded single-stage ``document_approval`` path cannot
express. A leadership artifact (Quality Policy POL, Quality Objectives OBJ, Management Review MR —
all ``kind=DOCUMENT``) is approved as today; when the org flag
``leadership_release_requires_top_management_authorization`` is set, the Approved version may not be
RELEASED until a **"Top Management"** member signs ``meaning=verify`` on the ``document_version``.
The request routes through the generic multi-stage workflow engine to the reserved role; the
sign-off mints a single ``signature_event`` bound to the existing version row (no own-table stage
event, so no two-INSERT seam — the CAPA/initiative template, simplified). The document FSM is NOT
mutated here: release stays a separate act, and the cutover (services/vault/lifecycle.py::_cutover)
checks for this ``verify`` signature when the flag is on.

Authority is the role-resolved candidate pool (no permission key gates the SIGN — the self-scoped-
task doctrine, doc 07); the REQUEST reuses ``document.approve`` at the document's scope. The engine
fails CLOSED to ``NEEDS_ATTENTION`` when no Top-Management member is assigned, so release simply
stays blocked. The welded approve/release path is untouched (byte-identical; R45 opt-in).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._signature_enums import SignatureMeaning, SignedObjectType
from ...db.models._vault_enums import DocumentCurrentState, VersionState
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.signature_event import SignatureEvent as SignatureEventRow
from ...db.models.system_config import SystemConfig
from ...db.models.workflow import Task, WorkflowInstance
from ...logging import request_id_var
from ...problems import ProblemException
from ..workflow import engine
from ..workflow import repository as wf_repo
from .signature import SignatureEvent, SignatureEventSink

# The seeded (mig 0054) effective definition: a single Top-Management ANY stage that signs
# ``meaning=verify`` and advances to COMPLETED.
_AUTH_DEF_KEY = "leadership_release_authorization"
# The leadership artifact document types this gate applies to (doc 14 document_type.code): Quality
# Policy / Quality Objectives / Management Review. A config-driven set — adding a type later is
# data, not new code (the "general enough to add others later" promise).
LEADERSHIP_DOC_TYPES = frozenset({"POL", "OBJ", "MR"})
# The ONLY outcomes this leadership sign-off accepts: ``verify`` (the positive sign that authorizes
# release) and ``reject`` (decline). The generic engine treats EVERY positive TaskOutcomeKind
# (approve/complete/acknowledge/verify) as satisfying an ANY quorum, so without this allow-list a
# Top-Management candidate could POST ``approve`` and still mint a ``verify`` signature (the
# initiative/PERIODIC_REVIEW complete-only precedent).
_ALLOWED_OUTCOMES = frozenset({"verify", "reject"})
# The engine's terminal/sentinel instance states. NEEDS_ATTENTION is an abandoned fail-closed
# instance (empty pool) → a re-request is allowed once a Top-Management approver is assigned.
_TERMINAL_INSTANCE_STATES = (engine.COMPLETED, engine.REJECTED, engine.NEEDS_ATTENTION)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _content_digest(content_block: dict[str, Any]) -> str:
    """A deterministic ``sha256:`` digest of the sealed authorization block — binds the ``verify``
    signature to the exact bytes it signed (the ``_content_digest`` in capa/service.py)."""
    payload = json.dumps(content_block, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _not_found(what: str) -> ProblemException:
    return ProblemException(status=404, code="not_found", title=f"{what} not found")


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


async def _doc_type_code(session: AsyncSession, doc: DocumentedInformation) -> str | None:
    if doc.document_type_id is None:
        return None
    return (
        await session.execute(
            select(DocumentType.code).where(DocumentType.id == doc.document_type_id)
        )
    ).scalar_one_or_none()


async def _latest_approved_version(
    session: AsyncSession, doc_id: uuid.UUID
) -> DocumentVersion | None:
    """The version the cutover would promote — the latest ``Approved`` version (the
    enrich_release_sod_scope / _cutover resolution)."""
    return (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_state == VersionState.Approved,
            )
            .order_by(DocumentVersion.version_seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_release_authorization(session: AsyncSession, version_id: uuid.UUID) -> bool:
    """True iff a Top-Management leadership ``verify`` signature exists for this version — the
    durable WORM proof the release gate (lifecycle._cutover) checks. ``meaning=verify`` on a
    ``document_version`` is minted by NOTHING else (CAPA→capa_stage, initiative→its own stage
    event), so its presence on the version is an unambiguous leadership-authorization marker."""
    return (
        await session.execute(
            select(SignatureEventRow.id).where(
                SignatureEventRow.signed_object_id == version_id,
                SignatureEventRow.signed_object_type == SignedObjectType.document_version,
                SignatureEventRow.meaning == SignatureMeaning.verify,
            )
        )
    ).first() is not None


async def release_authorization_status(
    session: AsyncSession, doc: DocumentedInformation
) -> dict[str, Any]:
    """The leadership release-authorization status for a document (the GET endpoint payload):
    whether it is a leadership artifact (POL/OBJ/MR), whether authorization is REQUIRED (the org
    flag is on AND it is a leadership type → release is gated), the current Approved ``version_id``,
    and whether that version is already ``authorized`` (carries a Top-Management verify sig)."""
    code = await _doc_type_code(session, doc)
    is_leadership = code in LEADERSHIP_DOC_TYPES
    config = await session.get(SystemConfig, doc.org_id)
    flag_on = bool(config and config.leadership_release_requires_top_management_authorization)
    version = await _latest_approved_version(session, doc.id)
    authorized = version is not None and await has_release_authorization(session, version.id)
    return {
        "is_leadership_artifact": is_leadership,
        "required": is_leadership and flag_on,
        "version_id": str(version.id) if version is not None else None,
        "authorized": authorized,
    }


def _emit_authorized(
    session: AsyncSession,
    actor: AppUser,
    *,
    version_id: uuid.UUID,
    identifier: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """The first-class audit of the leadership release sign-off (object_type=version,
    scope_ref=identifier) — the precondition for release, distinct from the RELEASED audit the
    cutover later emits."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.LEADERSHIP_AUTHORIZED,
            # The vault audits a version as AuditObjectType.version (doc 12 §4.2 — "version", not
            # "document_version"; the VaultAuditSink _OBJECT_TYPE map). The signature itself binds
            # to signed_object_type=document_version.
            object_type=AuditObjectType.version,
            object_id=version_id,
            scope_ref=identifier,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


async def request_leadership_authorization(
    session: AsyncSession,
    actor: AppUser,
    doc_id: uuid.UUID,
    *,
    comment: str | None = None,
) -> WorkflowInstance:
    """Open a Top-Management release authorization for an Approved leadership artifact (POST
    /documents/{id}/request-leadership-authorization). FOR UPDATE → 404 → 409 unless a leadership
    type → 409 unless Approved with an Approved version → 409 if an authorization is already in
    flight → instantiate the engine definition (materializing the Top-Management task, or
    NEEDS_ATTENTION when the pool is empty), commit. The document stays Approved until release."""
    doc = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == doc_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if doc is None or doc.org_id != actor.org_id:
        raise _not_found("Document")
    code = await _doc_type_code(session, doc)
    if code not in LEADERSHIP_DOC_TYPES:
        raise _conflict(
            "not_a_leadership_artifact",
            "Top-Management authorization applies only to a Quality Policy, Quality Objective, "
            "or Management Review",
        )
    if doc.current_state is not DocumentCurrentState.Approved:
        raise _conflict(
            "document_not_approved",
            "A leadership artifact must be Approved before requesting Top-Management authorization",
        )
    version = await _latest_approved_version(session, doc.id)
    if version is None:
        raise _conflict(
            "document_not_approved",
            "No Approved version to authorize for release",
        )
    existing = await wf_repo.find_nonterminal_instance(
        session,
        actor.org_id,
        WorkflowSubjectType.LEADERSHIP_AUTHORIZATION,
        doc.id,
        _TERMINAL_INSTANCE_STATES,
    )
    if existing is not None:
        raise _conflict(
            "authorization_in_progress",
            "A Top-Management authorization is already in progress for this document",
        )
    instance = await engine.instantiate(
        session,
        org_id=actor.org_id,
        definition_key=_AUTH_DEF_KEY,
        subject_type=WorkflowSubjectType.LEADERSHIP_AUTHORIZATION,
        subject_id=doc.id,
        context={
            "document_id": str(doc.id),
            "version_id": str(version.id),
            "identifier": doc.identifier,
            "title": doc.title,
            "doc_type": code,
            "requested_by": str(actor.id),
            "request_comment": comment,
        },
        actor=actor,
    )
    await session.commit()
    await session.refresh(instance)
    return instance


async def _assert_leadership_authorizer(
    session: AsyncSession, actor: AppUser, task: Task, instance: WorkflowInstance
) -> None:
    """The SOLE authorization gate for a leadership-authorization decision (no catalog key — the
    role-resolved candidate pool IS the authority; the ``_assert_initiative_authorizer`` shape).
    Both collapse legs return 404 uniformly. MUST run under the instance lock the caller holds."""
    pool_frozen = task.candidate_pool or []
    if task.assignee_user_id != actor.id and str(actor.id) not in pool_frozen:
        raise _not_found("Task")
    stages = await wf_repo.all_stages(session, instance.definition_id)
    stage = stages.get(task.stage_key)
    if stage is None:
        raise _not_found("Task")
    roles = list((stage.assignees or {}).get("roles", []))
    pool = await wf_repo.users_with_roles(session, actor.org_id, roles)
    if actor.id not in pool:
        raise _not_found("Task")
    # Single-stage flow → the cross-stage guard is a no-op today, but kept for symmetry with the
    # CAPA/initiative precedent (so a future two-tier definition stays sound).
    if await wf_repo.actor_decided_in_instance(
        session, instance.id, actor.id, exclude_task_id=task.id
    ):
        raise ProblemException(
            status=409,
            code="conflict",
            title="Already decided this authorization",
            detail="an approver may not decide more than one stage of one authorization",
        )


async def _enrich_completed_replay(
    session: AsyncSession, result: dict[str, Any], instance: WorkflowInstance
) -> None:
    """Re-derive the leadership ``verify`` signature id for an idempotent replay whose original
    decision COMPLETED — so a retry's body matches. A version is authorized exactly once (the
    signature is version-scoped + minted once on COMPLETE)."""
    version_id_raw = (instance.context or {}).get("version_id")
    result["version_id"] = version_id_raw
    result["document_id"] = str(instance.subject_id)
    if version_id_raw is None:
        result["signature_event_id"] = None
        return
    sig_id = (
        await session.execute(
            select(SignatureEventRow.id).where(
                SignatureEventRow.signed_object_id == uuid.UUID(str(version_id_raw)),
                SignatureEventRow.signed_object_type == SignedObjectType.document_version,
                SignatureEventRow.meaning == SignatureMeaning.verify,
            )
        )
    ).scalar_one_or_none()
    result["signature_event_id"] = str(sig_id) if sig_id is not None else None


async def decide_leadership_authorization(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
) -> dict[str, Any]:
    """Decide a Top-Management release-authorization task (the ``POST /tasks/{id}/decision``
    LEADERSHIP_AUTHORIZATION dispatch). Runs the generic engine decision WITHOUT committing, then —
    on the COMPLETING ``verify`` sign — writes a single ``signature_event(meaning=verify,
    signed_object_type=document_version, signed_object_id=<version>)`` bound to the existing version
    row (no own-table stage event, so no two-INSERT seam) and emits the LEADERSHIP_AUTHORIZED audit,
    all in ONE transaction. The document FSM is NOT mutated — release is a separate act the cutover
    gates on this signature. A reject is DECISIVE — it ends the cycle (REJECTED) and leaves the
    document Approved (re-requestable); no signature."""
    # Lock the instance FIRST (the serialization point); engine.decide re-locks re-entrantly.
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found("Workflow instance")
    await _assert_leadership_authorizer(session, actor, task, instance)
    # Validate the outcome AFTER the authority check so a non-pool caller gets the 404-collapse, not
    # a 422 that would leak the task's existence (the S-improvement-4 Codex P2). Accept ONLY
    # verify/reject — never a generic positive (approve/complete/acknowledge) the ANY-quorum engine
    # would treat as completing → a spurious verify signature.
    if outcome not in _ALLOWED_OUTCOMES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title=f"Unsupported outcome for a leadership authorization: {outcome}",
        )

    result = await engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    if result.get("replayed"):
        if result.get("current_state") == engine.COMPLETED:
            await _enrich_completed_replay(session, result, instance)
        await session.commit()
        return result

    if result["current_state"] == engine.COMPLETED:
        ctx = instance.context or {}
        version_id = uuid.UUID(str(ctx["version_id"]))
        identifier = ctx.get("identifier")
        sealed: dict[str, Any] = {
            "document_id": str(instance.subject_id),
            "version_id": str(version_id),
            "identifier": identifier,
            "doc_type": ctx.get("doc_type"),
            "authorized_by": str(actor.id),
            "requested_by": ctx.get("requested_by"),
            "outcome": comment,
            "workflow_instance_id": str(instance.id),
        }
        sig = sig_sink.record(
            session,
            SignatureEvent(
                org_id=actor.org_id,
                signed_object_id=version_id,
                meaning="verify",
                signer_user_id=actor.id,
                signed_object_type="document_version",
                content_digest=_content_digest(sealed),
                auth_context={"acr": "SESSION"},
            ),
        )
        await session.flush()  # the sink adds but does NOT flush — populate sig.id for the result
        _emit_authorized(
            session,
            actor,
            version_id=version_id,
            identifier=identifier,
            before={"authorized": False},
            after={
                "authorized": True,
                "signed_event_id": str(sig.id) if sig is not None else None,
            },
        )
        result["document_id"] = str(instance.subject_id)
        result["version_id"] = str(version_id)
        result["signature_event_id"] = str(sig.id) if sig is not None else None
    elif outcome == "reject":
        # A decline is DECISIVE — one Top-Management member ends the authorization. The engine's ANY
        # quorum does NOT fail on a single negative when the pool has other live candidates, so we
        # force the instance terminal + skip its sibling PENDING tasks here (the decide_dcr_approval
        # / initiative precedent); else the lingering non-terminal instance would block a re-request
        # and let a different member later verify. The document stays Approved (untouched); no
        # signature.
        pending = (
            (
                await session.execute(
                    select(Task)
                    .where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for sibling in pending:
            sibling.state = TaskState.SKIPPED
        instance.current_state = engine.REJECTED
        result["current_state"] = engine.REJECTED

    await session.commit()
    return result
