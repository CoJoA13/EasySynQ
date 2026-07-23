"""Batch 2 (2026-07-22 review) — deny-wins scope-tuple completeness on the write/dispose surfaces.

A scope-scoped DENY override must BEAT a broad SYSTEM ALLOW (deny-always-wins / R3). Before the fix,
POST /documents and the record.dispose gate built PARTIAL ResourceContext tuples, so a FRAMEWORK /
kind / PROCESS-scoped DENY didn't match, so the SYSTEM ALLOW won. Each test mutation-verifies that
against the pre-fix builder the op would SUCCEED (DENY dropped); post-fix it 403s.

Uses the default org (no second Organization); leftover records / scopes are per-id / per-user.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._retention_enums import DispositionAction
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.evidence_for_link import EvidenceForLink
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.vault import repository as vault_repo

from . import s5_helpers as s5
from .test_processes import _create_process
from .test_records import _capture, _grant, _subject
from .test_records_disposition import _DISPOSITION_PERMS, _cleanup, _org_id, _seed_policy, _to_due
from .test_records_process_scope import _link_process
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


async def _add_override(
    subject: str,
    permission_key: str,
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict[str, object],
) -> None:
    """Add a scoped PermissionOverride for ``subject`` (the report_document_control pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=level, selector=selector)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=effect,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def _framework_id(org_id: uuid.UUID) -> str:
    """The org's iso9001 framework id (every OPERATIONAL org has one seeded — D3)."""
    async with get_sessionmaker()() as s:
        fw = await vault_repo.get_framework(s, org_id)
        assert fw is not None
        return str(fw.id)


# --- POST /documents (document.create) ---------------------------------------------------


async def test_document_create_framework_deny_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A FRAMEWORK-scoped document.create DENY beats the SYSTEM ALLOW. Pre-fix the create scope had
    no framework_id, so the DENY was dropped and the doc was created (201)."""
    subject = _subject("doc-fw-deny")
    user_id = await _grant(subject, ("document.create",))
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    type_id = await s5.type_id("SOP")
    await _add_override(
        subject,
        "document.create",
        Effect.DENY,
        ScopeLevel.FRAMEWORK,
        selector={"framework_id": await _framework_id(org_id)},
    )
    r = await app_client.post(
        "/api/v1/documents",
        headers=h,
        json={"title": "fw-deny", "document_type_id": type_id, "area_code": "PUR"},
    )
    assert r.status_code == 403, r.text


async def test_document_create_doc_class_kind_deny_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A DOC_CLASS DENY requiring kind="DOCUMENT" beats the SYSTEM ALLOW. Pre-fix the scope set
    document_level but NOT kind, so the kind check failed and the DENY was dropped (201)."""
    subject = _subject("doc-kind-deny")
    await _grant(subject, ("document.create",))
    h = _auth(token_factory, subject)
    type_id = await s5.type_id("SOP")  # SOP → document_level L2_PROCEDURE
    await _add_override(
        subject,
        "document.create",
        Effect.DENY,
        ScopeLevel.DOC_CLASS,
        selector={"document_level": "L2_PROCEDURE", "kind": "DOCUMENT"},
    )
    r = await app_client.post(
        "/api/v1/documents",
        headers=h,
        json={"title": "kind-deny", "document_type_id": type_id, "area_code": "PUR"},
    )
    assert r.status_code == 403, r.text


# --- record.dispose ----------------------------------------------------------------------


async def test_record_dispose_framework_deny_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A FRAMEWORK-scoped record.dispose DENY beats the SYSTEM ALLOW. Pre-fix the dispose gate used
    a partial scope (artifact_id + folder_path), so the DENY was dropped and a distinct disposer
    disposed it (200). A distinct disposer (not the capturer) keeps SoD-6 from being the blocker."""
    capturer = _subject("disp-cap")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    disposer = _subject("disp-fw-deny")
    await _grant(disposer, _DISPOSITION_PERMS)  # SYSTEM record.dispose ALLOW
    hb = _auth(token_factory, disposer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _to_due(app_client, h, rid)
        await _add_override(
            disposer,
            "record.dispose",
            Effect.DENY,
            ScopeLevel.FRAMEWORK,
            selector={"framework_id": await _framework_id(org_id)},
        )
        denied = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=hb, json={"to_state": "DISPOSED"}
        )
        assert denied.status_code == 403, denied.text
    finally:
        await _cleanup(policy_id)


async def test_record_dispose_process_deny_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A PROCESS-scoped record.dispose DENY beats the SYSTEM ALLOW once the record is bound to that
    process — validates the dispose scope now carries process_ids (Option B). Pre-fix the partial
    scope had no process_ids, so the DENY was dropped and the distinct disposer disposed (200)."""
    capturer = _subject("disp-cap")
    user_id = await _grant(capturer, (*_DISPOSITION_PERMS, "process.create"))
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    disposer = _subject("disp-proc-deny")
    await _grant(disposer, _DISPOSITION_PERMS)
    hb = _auth(token_factory, disposer)
    process_id = (await _create_process(app_client, h))["id"]
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    rid = ""
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _link_process(app_client, h, rid, process_id)  # leg-A process binding
        await _to_due(app_client, h, rid)
        await _add_override(
            disposer,
            "record.dispose",
            Effect.DENY,
            ScopeLevel.PROCESS,
            selector={"process_ids": [process_id]},
        )
        denied = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=hb, json={"to_state": "DISPOSED"}
        )
        assert denied.status_code == 403, denied.text
    finally:
        # Drop the process evidence-link before _cleanup deletes the record — the leg-A link's
        # record_id FK (fk_evidence_for_link_record_id_record) would otherwise block the delete.
        if rid:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(EvidenceForLink).where(EvidenceForLink.record_id == uuid.UUID(rid))
                )
                await s.commit()
        await _cleanup(policy_id)
