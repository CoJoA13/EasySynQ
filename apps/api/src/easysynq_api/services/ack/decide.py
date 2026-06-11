"""The DOC_ACK decide leg (slice S-ack-1; doc 04 §8.2, spec §5).

The fourth ``POST /tasks/{id}/decision`` dispatch branch. Authz = candidate-membership
(404-collapse, the sibling posture) AND ``document.acknowledge`` enforced at the document's scope
for FRESH decisions only — a replay bypasses the mutable key check, since membership (a 1-member
pool) proves the caller IS the original decider (its first consumer; key failure is a calm 403 —
the task is honestly yours, the capability is missing). One transaction: engine.decide
(_commit=False) + the immutable acknowledgement INSERT + DOCUMENT_ACKNOWLEDGED — NEVER a
signature_event (R2/R43)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._ack_enums import AckCreatedReason
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._vault_enums import DocumentKind
from ...db.models.acknowledgement import Acknowledgement
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.process_link import ProcessLink
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.authz import ResourceContext
from ...logging import request_id_var
from ...problems import ProblemException
from ..authz import AuthzAuditSink, enforce
from ..workflow import engine as wf_engine
from . import queries

_ALLOWED_OUTCOMES = {"acknowledge"}


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


def _client_ip(request: Request) -> str | None:
    # The pack_share XFF-aware shape (Caddy fronts the API, so the socket peer is the proxy).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _not_found() -> ProblemException:
    return ProblemException(status=404, code="not_found", title="Task not found")


async def decide_doc_ack(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    request: Request,
    authz_sink: AuthzAuditSink,
) -> dict[str, Any]:
    # Fresh locked load with populate_existing (the sweep's _cancel_instance shape, NOT
    # lock_instance_for_update): decide_endpoint has ALREADY wf_repo.get_instance-loaded this
    # instance into the request session's identity map to dispatch on subject_type, so a bare
    # FOR UPDATE select would take the lock but return the PRE-LOCK attribute snapshot (the
    # S-drift-1 trap) — a sweep-cancel that committed CANCELLED while we waited for the lock
    # would be invisible here.
    instance = (
        await session.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.id == task.instance_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found()
    pool = [str(u) for u in (task.candidate_pool or [])]
    if task.assignee_user_id != actor.id and str(actor.id) not in pool:
        raise _not_found()

    # Live doc re-check under FOR UPDATE — populate_existing because the route's task lookup /
    # any prior session.get has identity-mapped the row (the S-drift-1 trap; spec §5).
    doc = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == instance.subject_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None or doc.org_id != actor.org_id or doc.kind != DocumentKind.DOCUMENT:
        raise _not_found()

    if outcome not in _ALLOWED_OUTCOMES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="A DOC_ACK task accepts outcome acknowledge",
        )

    result = await wf_engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    # A replay is not an act: the decision already committed, and membership (a 1-member pool)
    # proves the caller IS its decider — re-running the mutable key check would 403 a legitimate
    # retry after a grant lapse (Codex P2). The fresh path enforces below.
    if result.get("replayed"):
        # Response-parity on replay: re-derive the ack row's id (no rows are added). The pinned
        # version parses LENIENTLY — an unreadable context just leaves the enrichment fields
        # None; it never 409s a replay (the decision already happened).
        replay_pinned: uuid.UUID | None
        try:
            replay_pinned = uuid.UUID(str((instance.context or {}).get("document_version_id")))
        except (ValueError, TypeError):
            replay_pinned = None
        ack: Acknowledgement | None = None
        if replay_pinned is not None:
            ack = (
                await session.execute(
                    select(Acknowledgement).where(
                        Acknowledgement.user_id == actor.id,
                        Acknowledgement.document_version_id == replay_pinned,
                    )
                )
            ).scalar_one_or_none()
        result["document_id"] = str(doc.id)
        result["document_version_id"] = str(replay_pinned) if replay_pinned else None
        result["acknowledgement_id"] = str(ack.id) if ack is not None else None
        await session.commit()
        return result

    # Fresh decisions only. The 403 raise rolls the engine's uncommitted rows back (the same
    # _commit=False trick as the 409 ladder); the deny is still audited (the authz sink commits
    # in its OWN session). The key's first consumer: document.acknowledge at the document's
    # scope — membership failures 404-collapsed above; a key failure is a calm 403 (doc 10 §8.3).
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    process_ids = (
        await session.execute(
            select(ProcessLink.process_id).where(ProcessLink.documented_information_id == doc.id)
        )
    ).scalars()
    # R28: the FULL context — the seeded Employee grant is PROCESS-scoped; without process_ids
    # the PDP can never match it (Codex P1).
    resource = ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        process_ids=frozenset(str(p) for p in process_ids),
        lifecycle_state=doc.current_state.value,
    )
    await enforce(session, authz_sink, request, actor, "document.acknowledge", resource)

    # The obligation must still stand (the sweep may not have caught up) — raising a 409 rolls
    # the engine's uncommitted rows back; the task stays PENDING (the decide_periodic_review
    # trick).
    # NOTE the in-force predicate matches the sweep's: a governing Effective version exists —
    # NOT current_state == Effective (an UnderRevision doc still governs; R1/T7).
    ctx = instance.context or {}
    try:
        pinned_version_id = uuid.UUID(str(ctx.get("document_version_id")))
    except (ValueError, TypeError):
        raise ProblemException(
            status=409, code="ack_obligation_lapsed", title="Obligation context unreadable"
        ) from None
    pinned = await session.get(DocumentVersion, pinned_version_id)
    if (
        doc.current_effective_version_id is None
        or not doc.acknowledgement_required
        or pinned is None
        or pinned.document_id != doc.id
    ):
        raise ProblemException(
            status=409,
            code="ack_obligation_lapsed",
            title="The acknowledgement obligation no longer stands",
        )
    entries = await queries.list_entries(session, doc.id)
    audience = await queries.resolve_audience(session, doc.org_id, entries)
    if actor.id not in audience:
        raise ProblemException(
            status=409,
            code="ack_obligation_lapsed",
            title="The acknowledgement obligation no longer stands",
        )
    boundary = await queries.boundary_seq(session, doc)
    if boundary is None or pinned.version_seq < boundary:
        raise ProblemException(
            status=409,
            code="ack_superseded",
            title="A newer MAJOR revision superseded this acknowledgement task",
        )

    reason_raw = str(ctx.get("created_reason", AckCreatedReason.target_entry.value))
    try:
        reason = AckCreatedReason(reason_raw)
    except ValueError:
        reason = AckCreatedReason.target_entry
    ack_row = Acknowledgement(
        org_id=actor.org_id,
        document_id=doc.id,
        document_version_id=pinned_version_id,
        user_id=actor.id,
        acknowledged_at=_now(),
        client_ip=_client_ip(request),
        created_reason=reason,
    )
    session.add(ack_row)
    try:
        await session.flush()  # UNIQUE(user_id, document_version_id) backstop
    except IntegrityError:
        # Raising rolls the WHOLE txn back (engine rows included — _commit=False), so the task
        # stays PENDING; the duplicate means evidence already exists (a cancelled-task remnant
        # race) — the sweep reconciles the open task next pass.
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Acknowledgement already recorded"
        ) from None
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.DOCUMENT_ACKNOWLEDGED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={
                "acknowledgement_id": str(ack_row.id),
                "revision_label": pinned.revision_label,
                "created_reason": reason.value,
            },
            request_id=_rid(),
        )
    )
    result["document_id"] = str(doc.id)
    result["document_version_id"] = str(pinned_version_id)
    result["acknowledgement_id"] = str(ack_row.id)
    await session.commit()
    return result
