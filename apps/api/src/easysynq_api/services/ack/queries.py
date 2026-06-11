"""S-ack-1 data access: audience resolution, satisfaction, coverage (doc 04 §8.1/§8.2, R43).

Audience resolution is LIVE (doc 04 §8.1 "resolved dynamically"): user targets + org_role members
(RoleAssignment by role_id — NOT wf_repo.users_with_roles, which matches Role NAMES), restricted
to ACTIVE non-guest users (doc 07 §5.4: a read_only/guest principal can never acknowledge).
``process``/``folder`` targets are refused at create (R43) so the resolver never sees them.
Coverage truth = distribution x acknowledgement under the R43 boundary; tasks are only the to-do
surface (and the source of due_at for the overdue count)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._ack_enums import DistributionTargetType
from ...db.models._vault_enums import ChangeSignificance, VersionState
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.acknowledgement import Acknowledgement
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.distribution_entry import DistributionEntry
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.role import RoleAssignment
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.ack.rules import last_major_seq


async def list_entries(session: AsyncSession, document_id: uuid.UUID) -> list[DistributionEntry]:
    return list(
        (
            await session.execute(
                select(DistributionEntry)
                .where(DistributionEntry.document_id == document_id)
                .order_by(DistributionEntry.created_at)
            )
        )
        .scalars()
        .all()
    )


async def resolve_audience(
    session: AsyncSession, org_id: uuid.UUID, entries: list[DistributionEntry]
) -> set[uuid.UUID]:
    """The deduplicated, ACTIVE, non-guest user set across this doc's ack-required entries."""
    direct = {
        e.target_id
        for e in entries
        if e.ack_required and e.target_type is DistributionTargetType.user
    }
    role_ids = {
        e.target_id
        for e in entries
        if e.ack_required and e.target_type is DistributionTargetType.org_role
    }
    candidates: set[uuid.UUID] = set(direct)
    if role_ids:
        rows = (
            await session.execute(
                select(RoleAssignment.user_id).where(
                    RoleAssignment.org_id == org_id, RoleAssignment.role_id.in_(role_ids)
                )
            )
        ).scalars()
        candidates.update(rows)
    if not candidates:
        return set()
    active = (
        await session.execute(
            select(AppUser.id).where(
                AppUser.id.in_(candidates),
                AppUser.org_id == org_id,
                AppUser.status == UserStatus.ACTIVE,
                AppUser.is_guest.is_(False),
            )
        )
    ).scalars()
    return set(active)


async def boundary_seq(session: AsyncSession, doc: DocumentedInformation) -> int | None:
    """The R43 last-MAJOR boundary for the doc's current Effective version (None when no
    Effective version exists). The pair query pre-filters to ever-governed versions, so
    ``last_major_seq``'s no-MAJOR min-fallback means "the lowest version that ever took
    effect" — never an abandoned draft's seq."""
    if doc.current_effective_version_id is None:
        return None
    current = await session.get(DocumentVersion, doc.current_effective_version_id)
    if current is None:
        return None
    pairs = (
        await session.execute(
            select(
                DocumentVersion.version_seq,
                DocumentVersion.change_significance == ChangeSignificance.MAJOR,
            ).where(
                DocumentVersion.document_id == doc.id,
                # R43: the boundary walks versions that EVER GOVERNED (Effective now, Superseded,
                # or Obsolete via T11/T12 — all previously in force). A never-Effective draft
                # (abandoned / changes_requested) must not move the boundary: a phantom MAJOR at
                # a seq below a later MINOR release would otherwise collapse coverage and
                # mass-re-mint the audience (the diff-critic MAJOR).
                DocumentVersion.version_state.in_(
                    (VersionState.Effective, VersionState.Superseded, VersionState.Obsolete)
                ),
            )
        )
    ).all()
    return last_major_seq(
        [(seq, bool(major)) for seq, major in pairs], current_seq=current.version_seq
    )


async def satisfied_users(
    session: AsyncSession, document_id: uuid.UUID, boundary: int
) -> set[uuid.UUID]:
    """Users holding an acknowledgement at or above the boundary (the carry-forward rule)."""
    rows = (
        await session.execute(
            select(Acknowledgement.user_id)
            .join(DocumentVersion, DocumentVersion.id == Acknowledgement.document_version_id)
            .where(
                Acknowledgement.document_id == document_id,
                DocumentVersion.version_seq >= boundary,
            )
            .distinct()
        )
    ).scalars()
    return set(rows)


async def open_ack_tasks(
    session: AsyncSession, document_id: uuid.UUID
) -> list[tuple[Task, WorkflowInstance]]:
    """Open (PENDING) DOC_ACK tasks for this document, with their instances."""
    return [
        (task, instance)
        for task, instance in (
            await session.execute(
                select(Task, WorkflowInstance)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                    WorkflowInstance.subject_id == document_id,
                    Task.state == TaskState.PENDING,
                )
            )
        ).all()
    ]


def pinned_seq_map(
    pairs: list[tuple[Task, WorkflowInstance]], seq_by_version: dict[str, int]
) -> dict[uuid.UUID, int]:
    """user → the open task's pinned version_seq (context document_version_id → seq). A task
    whose context is unreadable maps to seq 0 — always stale, so the sweep cancels it."""
    out: dict[uuid.UUID, int] = {}
    for task, instance in pairs:
        if task.assignee_user_id is None:
            continue
        vid = str((instance.context or {}).get("document_version_id", ""))
        out[task.assignee_user_id] = seq_by_version.get(vid, 0)
    return out


async def version_seqs(session: AsyncSession, version_ids: set[str]) -> dict[str, int]:
    valid = []
    for v in version_ids:
        try:
            valid.append(uuid.UUID(v))
        except (ValueError, AttributeError, TypeError):
            continue
    if not valid:
        return {}
    rows = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.version_seq).where(
                DocumentVersion.id.in_(valid)
            )
        )
    ).all()
    return {str(vid): seq for vid, seq in rows}


async def coverage_counts(
    session: AsyncSession, doc: DocumentedInformation
) -> dict[str, Any] | None:
    """{required, acknowledged, pending, overdue} for the current Effective version; None when
    the doc has no Effective version (an honest absence, not a 0/0)."""
    if not doc.acknowledgement_required:
        if doc.current_effective_version_id is None:
            return None
        return {"required": 0, "acknowledged": 0, "pending": 0, "overdue": 0}
    boundary = await boundary_seq(session, doc)
    if boundary is None:
        return None
    entries = await list_entries(session, doc.id)
    audience = await resolve_audience(session, doc.org_id, entries)
    satisfied = await satisfied_users(session, doc.id, boundary)
    now = datetime.datetime.now(datetime.UTC)
    open_pairs = await open_ack_tasks(session, doc.id)
    overdue = {
        t.assignee_user_id
        for t, _ in open_pairs
        if t.due_at is not None and t.due_at < now and t.assignee_user_id in audience
    }
    done = len(audience & satisfied)
    return {
        "required": len(audience),
        "acknowledged": done,
        "pending": len(audience) - done,
        "overdue": len(overdue - satisfied),
    }


async def coverage_matrix(
    session: AsyncSession, doc: DocumentedInformation
) -> list[dict[str, Any]]:
    """The named per-user status list (the QM chase view, gate document.distribute)."""
    # Flag off ⇒ no obligations exist — an entries-only chase list would contradict
    # coverage_counts' zeros (Codex P2).
    if not doc.acknowledgement_required:
        return []
    boundary = await boundary_seq(session, doc)
    if boundary is None:
        return []
    entries = await list_entries(session, doc.id)
    audience = await resolve_audience(session, doc.org_id, entries)
    if not audience:
        return []
    satisfied = await satisfied_users(session, doc.id, boundary)
    open_pairs = await open_ack_tasks(session, doc.id)
    due_by_user = {t.assignee_user_id: t.due_at for t, _ in open_pairs}
    # Ascending so the dict's last-wins pick is the NEWEST qualifying ack (a user may hold
    # several ≥-boundary acks; the displayed label/timestamp must be deterministic and freshest).
    acks = (
        await session.execute(
            select(Acknowledgement, DocumentVersion.revision_label)
            .join(DocumentVersion, DocumentVersion.id == Acknowledgement.document_version_id)
            .where(
                Acknowledgement.document_id == doc.id,
                Acknowledgement.user_id.in_(audience),
                DocumentVersion.version_seq >= boundary,
            )
            .order_by(DocumentVersion.version_seq)
        )
    ).all()
    ack_by_user = {a.user_id: (a, label) for a, label in acks}
    users = (
        await session.execute(
            select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(audience))
        )
    ).all()
    now = datetime.datetime.now(datetime.UTC)
    out: list[dict[str, Any]] = []
    for uid, name in sorted(users, key=lambda r: r[1] or ""):
        ack_pair = ack_by_user.get(uid)
        due = due_by_user.get(uid)
        if uid in satisfied and ack_pair is not None:
            ack, label = ack_pair
            out.append(
                {
                    "user_id": str(uid),
                    "display_name": name,
                    "status": "acknowledged",
                    "acknowledged_at": ack.acknowledged_at.isoformat(),
                    "acknowledged_revision_label": label,
                    "due_at": None,
                }
            )
        else:
            status = "overdue" if (due is not None and due < now) else "pending"
            out.append(
                {
                    "user_id": str(uid),
                    "display_name": name,
                    "status": status,
                    "acknowledged_at": None,
                    "acknowledged_revision_label": None,
                    "due_at": due.isoformat() if due else None,
                }
            )
    return out
