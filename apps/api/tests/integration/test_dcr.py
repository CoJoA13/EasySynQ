"""S-dcr-1 integration proofs — DCR core + intake (/dcrs) over HTTP against testcontainer Postgres.

The seeded ``changeRequest.*`` keys ride the Process-Owner / QMS-Owner / Approver / Author roles
(create/read already granted by 0004; assess/close backfilled by 0040 — PROCESS placeholders), but
the test actor has no role assignment, so each test grants the keys it needs via SYSTEM-scope
overrides (the ``test_capa`` precedent; a SYSTEM grant matches any resource context). Assertions are
scoped to **this run's own** dcr ids — the integration suite shares one session DB across files, so
absolute counts are never asserted.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._process_enums import ProcessState
from easysynq_api.db.models._vault_enums import DocumentKind
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.dcr_stage_event import DcrStageEvent
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.process_link import ProcessLink
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_DCR_KEYS = (
    "changeRequest.create",
    "changeRequest.read",
    "changeRequest.assess",
    "changeRequest.close",
)


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the test_capa pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
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


async def _seed_di(subject: str, kind: DocumentKind) -> str:
    """Insert a minimal DocumentedInformation of the given kind in the actor's org; return its id.
    For the DCR target-validation tests only (a bare row — no version/record subtype needed)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        framework_id = (await s.execute(select(Framework.id).limit(1))).scalar_one()
        di = DocumentedInformation(
            org_id=user.org_id,
            framework_id=framework_id,
            kind=kind,
            identifier=f"DCRTGT-{uuid.uuid4().hex[:10]}",
            title="target",
            owner_user_id=user.id,
            created_by=user.id,
        )
        s.add(di)
        await s.commit()
        return str(di.id)


async def _seed_process_and_linked_doc(subject: str) -> tuple[str, str]:
    """Insert a Process + a DocumentedInformation(DOCUMENT) + the process_link between them; return
    (process_id, doc_id). For the PROCESS-scope authz seam test."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        framework_id = (await s.execute(select(Framework.id).limit(1))).scalar_one()
        proc = Process(
            org_id=user.org_id,
            name=f"P-{uuid.uuid4().hex[:8]}",
            pdca_phase=PdcaPhase.DO,
            state=ProcessState.ACTIVE,
            created_by=user.id,
        )
        s.add(proc)
        di = DocumentedInformation(
            org_id=user.org_id,
            framework_id=framework_id,
            kind=DocumentKind.DOCUMENT,
            identifier=f"DCRTGT-{uuid.uuid4().hex[:10]}",
            title="target",
            owner_user_id=user.id,
            created_by=user.id,
        )
        s.add(di)
        await s.flush()
        s.add(
            ProcessLink(
                org_id=user.org_id,
                process_id=proc.id,
                documented_information_id=di.id,
                created_by=user.id,
            )
        )
        await s.commit()
        return str(proc.id), str(di.id)


async def _grant_process(subject: str, key: str, process_id: str) -> None:
    """Grant one key at PROCESS scope bound to a concrete process_id (NOT a SYSTEM override) — the
    PDP PROCESS branch matches only when selector.process_id ∈ resource.process_ids."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.PROCESS, selector={"process_id": process_id}
        )
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


async def _event_count(object_id: str, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(object_id),
                    AuditEvent.event_type == event_type,
                )
            )
        ).scalar_one()


# --- intake + lifecycle -----------------------------------------------------------------------


async def test_raise_create_dcr_then_cancel(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("dcr")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "CREATE",
            "change_significance": "MAJOR",
            "reason_class": "regulatory",
            "reason_text": "New EU supplier due-diligence procedure",
        },
    )
    assert r.status_code == 201, r.text
    dcr = r.json()
    dcr_id = dcr["id"]
    assert dcr["state"] == "Open"
    assert dcr["change_type"] == "CREATE"
    assert dcr["target_document_id"] is None
    assert dcr["resulting_version_id"] is None
    # Identifier DCR-{YYYY}-{SEQ} with 4-digit zero-padded SEQ (doc 14 §7).
    assert re.match(r"^DCR-\d{4}-\d{4,}$", dcr["identifier"]), dcr["identifier"]

    # The detail view carries the genesis stage event (from=None → Open).
    detail = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=h)).json()
    assert [(e["from_state"], e["to_state"]) for e in detail["stage_events"]] == [(None, "Open")]
    assert await _event_count(dcr_id, EventType.DCR_RAISED) == 1

    # Cancel while Open → Cancelled + one DCR_TRANSITIONED.
    r = await app_client.post(
        f"/api/v1/dcrs/{dcr_id}/cancel", headers=h, json={"comment": "superseded by DCR-…"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Cancelled"
    assert await _event_count(dcr_id, EventType.DCR_TRANSITIONED) == 1

    detail = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=h)).json()
    assert [e["to_state"] for e in detail["stage_events"]] == ["Open", "Cancelled"]

    # A second cancel on a terminal DCR is a 409 (not a no-op).
    r2 = await app_client.post(f"/api/v1/dcrs/{dcr_id}/cancel", headers=h, json={})
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "dcr_not_cancellable"


async def test_revise_target_validation(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("dcr-tgt")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)

    # CREATE must NOT carry a target.
    doc_id = await _seed_di(subject, DocumentKind.DOCUMENT)
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "CREATE",
            "change_significance": "MINOR",
            "reason_class": "other",
            "reason_text": "x",
            "target_document_id": doc_id,
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "validation_error", r.text

    # REVISE without a target.
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "REVISE",
            "change_significance": "MINOR",
            "reason_class": "error_correction",
            "reason_text": "fix typo",
        },
    )
    assert r.status_code == 422, r.text

    # REVISE against a real Document → 201.
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "REVISE",
            "change_significance": "MAJOR",
            "reason_class": "process_improvement",
            "reason_text": "tighten the approval step",
            "target_document_id": doc_id,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["target_document_id"] == doc_id

    # REVISE against a Record (kind=RECORD) → 422 not_a_document.
    rec_id = await _seed_di(subject, DocumentKind.RECORD)
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "RETIRE",
            "change_significance": "MAJOR",
            "reason_class": "other",
            "reason_text": "retire it",
            "target_document_id": rec_id,
        },
    )
    assert r.status_code == 422, r.text

    # REVISE against a non-existent document → 404.
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "REVISE",
            "change_significance": "MINOR",
            "reason_class": "other",
            "reason_text": "x",
            "target_document_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 404, r.text


async def test_patch_while_open_then_blocked_after_cancel(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("dcr-patch")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)
    dcr_id = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "CREATE",
                "change_significance": "MINOR",
                "reason_class": "other",
                "reason_text": "initial reason",
            },
        )
    ).json()["id"]

    r = await app_client.patch(
        f"/api/v1/dcrs/{dcr_id}",
        headers=h,
        json={"reason_text": "clarified reason", "change_significance": "MAJOR"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reason_text"] == "clarified reason"
    assert r.json()["change_significance"] == "MAJOR"
    assert await _event_count(dcr_id, EventType.DCR_UPDATED) == 1

    # Once cancelled (no longer Open), a PATCH is a 409.
    await app_client.post(f"/api/v1/dcrs/{dcr_id}/cancel", headers=h, json={})
    r = await app_client.patch(f"/api/v1/dcrs/{dcr_id}", headers=h, json={"reason_text": "nope"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "dcr_not_editable"


async def test_dcr_stage_event_is_append_only(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The running app connects as the NON-OWNER easysynq_app role → the dcr_stage_event REVOKE
    bites (SQLSTATE 42501). The transition trail is structurally immutable, not conventional."""
    subject = _subject("dcr-ao")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)
    dcr_id = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "CREATE",
                "change_significance": "MINOR",
                "reason_class": "other",
                "reason_text": "x",
            },
        )
    ).json()["id"]
    async with get_sessionmaker()() as s:
        ev_id = (
            await s.execute(
                select(DcrStageEvent.id).where(DcrStageEvent.dcr_id == uuid.UUID(dcr_id))
            )
        ).scalar_one()
    for stmt in (
        "UPDATE dcr_stage_event SET to_state = 'Closed' WHERE id = :id",
        "DELETE FROM dcr_stage_event WHERE id = :id",
    ):
        async with get_sessionmaker()() as s:
            with pytest.raises(DBAPIError) as exc:
                await s.execute(text(stmt), {"id": ev_id})
                await s.commit()
            assert getattr(exc.value.orig, "sqlstate", None) == "42501", stmt


async def test_assess_authorized_by_concrete_process_grant(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The seam (diff-critic MAJOR): _dcr_doc_scope must populate process_ids from the target doc's
    process-links, or a PROCESS-scoped changeRequest.assess grant can NEVER match (the PDP needs a
    non-empty resource.process_ids). A concrete PROCESS grant bound to the doc's process
    authorizes the PATCH; the same key bound to a DIFFERENT process does not."""
    subject = _subject("dcr-procscope")
    # create/read at SYSTEM (to raise + read); assess ONLY via a concrete PROCESS grant (no SYSTEM).
    await _grant(subject, ("changeRequest.create", "changeRequest.read"))
    h = _auth(token_factory, subject)
    process_id, doc_id = await _seed_process_and_linked_doc(subject)

    dcr_id = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "REVISE",
                "change_significance": "MINOR",
                "reason_class": "error_correction",
                "reason_text": "initial",
                "target_document_id": doc_id,
            },
        )
    ).json()["id"]

    # Without any assess grant, PATCH is denied (deny-by-default — proves no stray SYSTEM grant).
    r = await app_client.patch(f"/api/v1/dcrs/{dcr_id}", headers=h, json={"reason_text": "v2"})
    assert r.status_code == 403, r.text

    # A PROCESS grant bound to a DIFFERENT process must NOT authorize (the doc isn't linked to it).
    other_process, _ = await _seed_process_and_linked_doc(subject)
    await _grant_process(subject, "changeRequest.assess", other_process)
    r = await app_client.patch(f"/api/v1/dcrs/{dcr_id}", headers=h, json={"reason_text": "v2"})
    assert r.status_code == 403, r.text

    # A PROCESS grant bound to the target doc's process authorizes the PATCH (process_ids matched).
    await _grant_process(subject, "changeRequest.assess", process_id)
    r = await app_client.patch(f"/api/v1/dcrs/{dcr_id}", headers=h, json={"reason_text": "v2"})
    assert r.status_code == 200, r.text
    assert r.json()["reason_text"] == "v2"


async def test_create_denied_without_grant(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A user with no changeRequest.create grant (no SYSTEM override) is denied (deny-by-default).
    subject = _subject("dcr-nogrant")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, subject)
        await s.commit()
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "CREATE",
            "change_significance": "MINOR",
            "reason_class": "other",
            "reason_text": "x",
        },
    )
    assert r.status_code == 403, r.text


async def test_grant_backfill_present(app_under_test: object) -> None:
    """The S-dcr-1 grant backfill (0040) granted the two orphaned keys to the right roles. Needs
    ``app_under_test`` to repoint ``get_sessionmaker()`` at the testcontainer DB (no app_client)."""
    async with get_sessionmaker()() as s:
        for role_name, perm_key in (
            ("Process Owner", "changeRequest.assess"),
            ("QMS Owner", "changeRequest.assess"),
            ("Process Owner", "changeRequest.close"),
            ("QMS Owner", "changeRequest.close"),
        ):
            cnt = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM role_grant rg "
                        "JOIN role r ON r.id = rg.role_id "
                        "JOIN permission p ON p.id = rg.permission_id "
                        "WHERE r.name = :rn AND p.key = :pk"
                    ),
                    {"rn": role_name, "pk": perm_key},
                )
            ).scalar_one()
            assert cnt >= 1, f"{role_name} missing {perm_key}"
