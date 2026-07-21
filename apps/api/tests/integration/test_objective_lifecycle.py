"""S-obj-3 integration: the objective lifecycle (submit → approve → release → Effective), the
6.2-★ flip to COVERED, and the new reads. Grants are SYSTEM-scope PermissionOverrides on JIT users
keyed by keycloak_subject (the test_quality_objectives / s5_helpers precedent)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._process_enums import ProcessState
from easysynq_api.db.models._signature_enums import SignatureMeaning
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.vault import repository as vault_repo

from . import s5_helpers as s5
from .test_quality_objectives import _grant
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")


async def _create_objective(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": title,
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_submit_freezes_the_commitment_and_enters_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-sub-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "On-time delivery")

    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "InReview"

    # a Draft version exists with the frozen commitment in its metadata_snapshot
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(oid))
            )
        ).scalar_one()
        commitment = (v.metadata_snapshot or {}).get("objective_commitment")
        assert commitment is not None
        assert commitment["target_value"] == "98"
        assert commitment["unit"] == "%"
        assert commitment["direction"] == "HIGHER_IS_BETTER"
        assert commitment["at_risk_threshold"] == "95"


async def test_submit_requires_objective_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    owner = f"obj-own-{uuid.uuid4()}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    oid = await _create_objective(app_client, ho, "Needs manage")

    # a reader without objective.manage cannot submit
    reader = f"obj-rdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hr)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "permission_denied"  # the PEP deny, not a stray 403


async def test_submit_twice_is_a_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-dbl-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Submit once")
    first = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert first.status_code == 200, first.text
    again = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert again.status_code == 409, again.text


async def _clause_6_2_row(client: AsyncClient, h: dict[str, str]) -> dict:
    body = (await client.get("/api/v1/reports/compliance-checklist", headers=h)).json()
    return next(r for r in body["rows"] if r["number"] == "6.2")


async def test_full_lifecycle_to_effective_flips_6_2_covered(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"obj-sm-{salt}", f"obj-ap-{salt}", f"obj-rl-{salt}"
    hs, hap, hrl = (
        _auth(token_factory, submitter),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    # submitter owns + submits the objective; approver joins the document_approval pool via the
    # role; releaser is a THIRD party with document.release (SoD-2: author/approver ≠ releaser).
    await _grant(submitter, _OBJ_KEYS)
    await _grant(submitter, ("report.compliance_checklist.read",))
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    before = await _clause_6_2_row(app_client, hs)
    eff0 = before["effective_count"]

    oid = await _create_objective(app_client, hs, "Lifecycle objective")
    submitted = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text

    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    assert dec.json()["signature_event"]["meaning"] == "approval"

    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"

    # the released version is Effective + carries a release signature; the doc points at it
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert doc is not None
        assert doc.current_state is DocumentCurrentState.Effective
        assert doc.current_effective_version_id is not None
        v = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert v is not None
        assert v.version_state is VersionState.Effective
        n = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEventRow)
                .where(
                    SignatureEventRow.signed_object_id == v.id,
                    SignatureEventRow.meaning == SignatureMeaning.release,
                )
            )
        ).scalar_one()
        assert n == 1

    # the 6.2 ★ checklist node now counts this Effective objective (delta-asserted — shared DB)
    after = await _clause_6_2_row(app_client, hs)
    assert after["effective_count"] == eff0 + 1
    assert after["status"] == "COVERED"


async def test_author_cannot_release_their_own_objective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    submitter, approver = f"obj-sa-{salt}", f"obj-aa-{salt}"
    hs, hap = _auth(token_factory, submitter), _auth(token_factory, approver)
    await _grant(submitter, _OBJ_KEYS)
    # the submitter holds the release key but IS the version author (SoD-2 must block them)
    await _grant(submitter, ("document.release", "document.read"))
    await s5.grant_role(approver, "Approver")
    oid = await _create_objective(app_client, hs, "SoD objective")
    submitted = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    # SoD-2: the version author cannot release their own objective → 403 sod_violation
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hs)
    assert rel.status_code == 403, rel.text
    assert rel.json()["code"] == "sod_violation"


async def test_approval_read_is_null_before_submit_then_present(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-apr-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Approval read")

    # null before submit (no cycle)
    pre = await app_client.get(f"/api/v1/objectives/{oid}/approval", headers=h)
    assert pre.status_code == 200, pre.text
    assert pre.json() is None

    submitted = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert submitted.status_code == 200, submitted.text
    post = await app_client.get(f"/api/v1/objectives/{oid}/approval", headers=h)
    assert post.status_code == 200, post.text
    inst = post.json()
    assert inst["subject_type"] == "DOCUMENT"
    assert inst["subject_id"] == oid
    assert any(t["type"] == "APPROVE" for t in inst["tasks"])


async def test_detail_exposes_capabilities_for_the_manager(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-cap-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Caps objective")
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail["capabilities"]["submit"] is True  # holds objective.manage
    assert detail["capabilities"]["release"] is False  # no document.release
    assert detail["effective_from"] is None  # Draft — present-but-null until Effective
    assert detail["capabilities"]["edit"] is True  # S-obj-4: same objective.manage answer
    assert detail["capabilities"]["start_revision"] is True
    assert detail["pending_commitment"] is None  # Draft, no governing version yet


async def test_list_omits_capabilities(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-lst-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    await _create_objective(app_client, h, "List objective")
    rows = (await app_client.get("/api/v1/objectives", headers=h)).json()["data"]
    assert rows  # at least our row
    assert all("capabilities" not in r for r in rows)  # detail-only, no per-row authz cost


async def test_policy_endpoint_null_or_effective_pol(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A fresh install has the POL document_type but no Effective POL document → null. Tolerate a
    # sibling test having released one into the shared DB: then the shape contract is pinned.
    subject = f"obj-pol-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, ("objective.read",))
    r = await app_client.get("/api/v1/objectives/policy", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body is None or set(body) == {"id", "identifier", "title"}


async def test_policy_endpoint_requires_objective_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-pol2-{uuid.uuid4()}"
    h = _auth(token_factory, subject)  # no grant
    r = await app_client.get("/api/v1/objectives/policy", headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "permission_denied"


async def _add_override(
    subject: str,
    permission_key: str,
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict[str, object] | None = None,
) -> None:
    """Seed a scoped permission override for ``subject`` (the test_search/test_authz precedent) —
    used here to seed a FRAMEWORK-scoped ALLOW + a PROCESS-scoped DENY for ``document.release``."""
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


async def _seed_process(subject: str) -> str:
    """Insert an ACTIVE Process in the subject's org; return its id (str)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        proc = Process(
            org_id=user.org_id,
            name=f"OBJ-P-{uuid.uuid4().hex[:8]}",
            pdca_phase=PdcaPhase.DO,
            state=ProcessState.ACTIVE,
            created_by=user.id,
        )
        s.add(proc)
        await s.commit()
        return str(proc.id)


async def test_release_framework_allow_process_deny_denies_the_objective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """#346: an objective binds its process on ``quality_objective.process_id`` (a satellite), NOT a
    ``ProcessLink`` — so the release scope must UNION that satellite process (the shared
    ``process_ids_for_docs`` loader now does). A third-party releaser with a FRAMEWORK-scoped
    ``document.release`` ALLOW + a PROCESS-scoped ``document.release`` DENY on the objective's bound
    process must be DENIED (deny-always-wins). Pre-#346 the satellite process was dropped from the
    scope, the DENY didn't match, and the newly-#333-matching framework ALLOW released it (200)."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"obj-fd-sm-{salt}", f"obj-fd-ap-{salt}", f"obj-fd-rl-{salt}"
    hs, hap = _auth(token_factory, submitter), _auth(token_factory, approver)
    await _grant(submitter, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")

    proc_id = await _seed_process(submitter)
    # create the objective BOUND to the process (satellite process_id, deliberately no ProcessLink)
    r = await app_client.post(
        "/api/v1/objectives",
        headers=hs,
        json={
            "title": f"FwDeny objective {salt}",
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
            "process_id": proc_id,
        },
    )
    assert r.status_code == 201, r.text
    oid = r.json()["id"]

    # drive to Approved (submitter authors + submits, approver approves)
    submitted = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    # the objective's framework (the single seeded framework) — read straight from the row
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert di is not None
        framework_id = str(di.framework_id)

    # releaser = a SoD-2-clean third party: a broad FRAMEWORK document.release ALLOW, but a
    # PROCESS-scoped document.release DENY on the objective's bound process (+ SYSTEM read so the
    # only blocker is the release gate). Deny-always-wins → a 403, not a SoD violation.
    await _add_override(
        releaser,
        "document.release",
        Effect.ALLOW,
        ScopeLevel.FRAMEWORK,
        selector={"framework_id": framework_id},
    )
    await _add_override(
        releaser,
        "document.release",
        Effect.DENY,
        ScopeLevel.PROCESS,
        selector={"process_id": proc_id},
    )
    await _add_override(releaser, "document.read", Effect.ALLOW, ScopeLevel.SYSTEM)
    hrl = _auth(token_factory, releaser)

    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 403, rel.text  # the PROCESS DENY wins over the framework ALLOW
    assert rel.json()["code"] == "permission_denied"  # deny-wins, NOT a SoD violation


async def test_process_ids_for_doc_unions_objective_satellite(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """#346: the canonical ``process_ids_for_doc(s)`` loader UNIONs the objective's satellite
    (``quality_objective.process_id``). Every document authz scope built from it — release, the
    approve/review decision, instance/approval read, search, the DCR target scope, and the record
    source/correction scopes — therefore sees a bound-process objective's process, so a
    PROCESS-scoped DENY participates (deny-always-wins). A process-less objective yields the empty
    set (proving it's the satellite value, not a phantom); pre-#346 the bound objective also
    yielded empty."""
    salt = uuid.uuid4().hex[:8]
    owner = f"obj-pid-{salt}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    proc_id = await _seed_process(owner)

    bound = await app_client.post(
        "/api/v1/objectives",
        headers=ho,
        json={
            "title": f"Bound objective {salt}",
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
            "process_id": proc_id,
        },
    )
    assert bound.status_code == 201, bound.text
    unbound = await _create_objective(app_client, ho, f"Unbound objective {salt}")  # no process_id

    async with get_sessionmaker()() as s:
        bound_pids = await vault_repo.process_ids_for_doc(s, uuid.UUID(bound.json()["id"]))
        unbound_pids = await vault_repo.process_ids_for_doc(s, uuid.UUID(unbound))
    assert bound_pids == frozenset({proc_id})  # satellite unioned (empty pre-#346)
    assert unbound_pids == frozenset()  # a process-less objective has no phantom process
