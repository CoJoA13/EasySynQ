"""Shared S5 integration helpers (not a test module — no ``test_`` prefix, so pytest skips it).

The S5 grant model: both the author and the approver hold the full lifecycle permission set (via a
SYSTEM-scope override) — **SoD**, not the grant, enforces the separation (the author has
``document.approve`` but SoD-1 denies self-approval). ``grant_role`` additionally role-assigns the
approver so they populate a task's ``candidate_pool`` (the My-Tasks proof). Approval is driven
through ``POST /tasks/{id}/decision``; ``task_for_doc`` finds the open task for a document.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models._workflow_enums import TaskState
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.document_type import DocumentType
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.workflow import Task, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _checkin, _create, _ensure_user, _upload

# The full lifecycle permission set an actor needs end-to-end (S3 vault + S4/S5 keys, doc 07 §3.1).
LIFECYCLE_PERMS = (
    "document.read",
    "document.read_draft",
    "document.create",
    "document.checkout",
    "document.edit",
    "document.manage_metadata",
    "document.submit",
    "document.review",
    "document.approve",
    "document.release",
    "document.obsolete",
)


async def default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def grant_lifecycle(subject: str) -> uuid.UUID:
    """Grant every lifecycle permission at SYSTEM scope via override (SoD does the gating)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in LIFECYCLE_PERMS:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
            s.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=perm.id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
            )
        await s.commit()
        return user.id


async def grant_role(subject: str, role_name: str) -> uuid.UUID:
    """Assign a seeded role (SYSTEM-bound) to a user — grants the role's perms AND lands the user in
    the candidate_pool of tasks whose stage names that role (the My-Tasks path)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (
            await s.execute(select(Role).where(Role.org_id == user.org_id, Role.name == role_name))
        ).scalar_one()
        s.add(
            RoleAssignment(
                org_id=user.org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
        )
        await s.commit()
        return user.id


async def set_approver_release(org_id: uuid.UUID, value: bool) -> None:
    """Upsert the org's SoD-2 ``allow_approver_release`` flag (no system_config row exists yet)."""
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is None:
            s.add(SystemConfig(org_id=org_id, allow_approver_release=value))
        else:
            cfg.allow_approver_release = value
        await s.commit()


async def task_for_doc(document_id: str) -> str:
    """The latest open (PENDING) approval task for a document's most recent workflow instance."""
    async with get_sessionmaker()() as s:
        instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(WorkflowInstance.subject_id == uuid.UUID(document_id))
                .order_by(WorkflowInstance.started_at.desc())
                .limit(1)
            )
        ).scalar_one()
        task = (
            await s.execute(
                select(Task)
                .where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
                .limit(1)
            )
        ).scalar_one()
        return str(task.id)


async def effective_count(document_id: str) -> int:
    async with get_sessionmaker()() as s:
        from sqlalchemy import func

        return (
            await s.execute(
                select(func.count())
                .select_from(DocumentVersion)
                .where(
                    DocumentVersion.document_id == uuid.UUID(document_id),
                    DocumentVersion.version_state == VersionState.Effective,
                )
            )
        ).scalar_one()


async def get_version(version_id: str) -> DocumentVersion:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(version_id))
            )
        ).scalar_one()


async def type_id(code: str) -> str:
    async with get_sessionmaker()() as s:
        return str(
            (await s.execute(select(DocumentType).where(DocumentType.code == code))).scalar_one().id
        )


async def drive_to_approved(
    client: AsyncClient,
    h_author: dict[str, str],
    h_approver: dict[str, str],
    doc_type_id: str,
    content: bytes,
    *,
    effective_from: str | None = None,
) -> str:
    """author: create → checkout → upload → checkin → submit-review; approver: decide approve."""
    doc = await _create(client, h_author, doc_type_id)
    did = doc["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h_author)
    sha = await _upload(client, h_author, did, content)
    ci = await _checkin(client, h_author, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    sr = await client.post(f"/api/v1/documents/{did}/submit-review", headers=h_author)
    assert sr.status_code == 200, sr.text
    task_id = await task_for_doc(did)
    body: dict = {"outcome": "approve"}
    if effective_from is not None:
        body["effective_from"] = effective_from
    dec = await client.post(f"/api/v1/tasks/{task_id}/decision", headers=h_approver, json=body)
    assert dec.status_code == 200, dec.text
    return did


async def drive_to_effective(
    client: AsyncClient,
    h_author: dict[str, str],
    h_approver: dict[str, str],
    h_releaser: dict[str, str],
    doc_type_id: str,
    content: bytes,
) -> dict:
    """As :func:`drive_to_approved`, then the releaser releases (needs ``allow_approver_release``
    when the releaser is the approver)."""
    did = await drive_to_approved(client, h_author, h_approver, doc_type_id, content)
    rel = await client.post(f"/api/v1/documents/{did}/release", headers=h_releaser, json={})
    assert rel.status_code == 200, rel.text
    return rel.json()
