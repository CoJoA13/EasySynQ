"""S-risk-1b integration proofs — the Risk & Opportunity register's controlled-document
publish/freeze/release lifecycle (clause 6.1).

A register goes Draft → (publish) InReview → (approve) Approved → (release) Effective: the working
rows + per-method scoring criteria are FROZEN into an immutable version at publish, and the live
band grades against the GOVERNING version's frozen criteria. While Effective the satellite is
read-only; a ``start-revision`` reopens the edit window, and a second publish→approve→release
SUPERSEDES in place (one head per org, never a second). The head lifecycle is register.manage @
SYSTEM (the steward) — a PROCESS-bound owner cannot publish. The managed-doc heads (OBJ/MR/RSK) are
reserved from the generic metadata/distribution/link mutations (D-3b).

⚠ The RSK head is a per-org SINGLETON shared across the one-org integration DB, so a lifecycle test
that drives it to Effective would 409 every other test's risk-create. The head-advancing test
therefore NORMALIZES the head to editable at the start (advancing a prior crashed cycle if needed)
and RESTORES it to an editable UnderRevision at the end — self-provisioning, non-polluting (the
"never assume a clean/dirty shared DB" discipline). The gate + reservation tests are head-state
independent. Grants are SYSTEM-scope overrides on JIT users; SoD-2: author ≠ approver ≠ releaser.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._risk_enums import RiskOpportunityType, ScoringMethod
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.risk import add_risk_row
from easysynq_api.services.vault import get_vault_audit_sink

from . import s5_helpers as s5
from .test_processes import _create_process, _grant, _user_id
from .test_vault import _auth, _create, _ensure_user, _sop_type_id

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        steward=f"kc-rlf-s-{salt}",
        approver=f"kc-rlf-a-{salt}",
        releaser=f"kc-rlf-r-{salt}",
        owner=f"kc-rlf-o-{salt}",
    )


@pytest.fixture
async def restore_register_head(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> Any:
    """Teardown guard for the head-advancing lifecycle test: restore the shared per-org RSK head to
    editable so sibling tests' risk-creates succeed — even if the body fails mid-lifecycle (the
    diff-critic cascade guard; a 500 after release() commits would otherwise leave it Effective).
    Best-effort (never masks the body's failure); re-derives the steward/approver/releaser auth from
    subj (the _setup_actors grants persist as DB rows). app_client outlives this teardown (reverse
    finalizer order)."""
    yield
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    with contextlib.suppress(Exception):
        await _drive_to_editable(app_client, hs, hap, hrl)


async def _create_risk(
    client: AsyncClient,
    h: dict[str, str],
    *,
    process_id: str | None = None,
    likelihood: int = 4,
    severity: int = 5,
    description: str = "R",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "risk",
        "description": description,
        "likelihood": likelihood,
        "severity": severity,
    }
    if process_id is not None:
        body["process_id"] = process_id
    r = await client.post("/api/v1/risks", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _assign_owner(
    client: AsyncClient, h: dict[str, str], process_id: str, user_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/processes/{process_id}/owner", headers=h, json={"user_id": str(user_id)}
    )
    assert r.status_code == 201, r.text


async def _setup_actors(subj: SimpleNamespace) -> None:
    """Steward (author/publisher) holds register.manage+read @ SYSTEM AND document.release — yet
    SoD-2 (not a missing grant) blocks them releasing their OWN register. The approver joins the
    document_approval pool via the Approver role; the releaser is a THIRD party (SoD-2: author ≠
    approver ≠ releaser)."""
    await _grant(subj.steward, "register.manage")
    await _grant(subj.steward, "register.read")
    for key in ("document.release", "document.read", "document.read_draft"):
        await _grant(
            subj.steward, key
        )  # the steward holds release; SoD-2 still denies self-release
    await s5.grant_role(subj.approver, "Approver")
    for key in ("document.release", "document.read", "document.read_draft"):
        await _grant(subj.releaser, key)


async def _status(client: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get("/api/v1/risks/register", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _approve_and_release(
    client: AsyncClient, head_id: str, hap: dict[str, str], hrl: dict[str, str]
) -> dict[str, Any]:
    task_id = await s5.task_for_doc(head_id)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await client.post("/api/v1/risks/register/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    return rel.json()


async def _drive_to_editable(
    client: AsyncClient, hs: dict[str, str], hap: dict[str, str], hrl: dict[str, str]
) -> None:
    """Normalize the org's shared RSK head to an EDITABLE state (Draft/UnderRevision or absent),
    advancing a prior crashed cycle forward if needed. Bounded loop — every transition is monotonic
    toward Effective, from which one start-revision reaches UnderRevision."""
    for _ in range(6):
        st = await _status(client, hs)
        if not st["exists"]:
            return
        state, head_id = st["state"], st["register_doc_id"]
        if state in ("Draft", "UnderRevision"):
            return
        if state == "Effective":
            assert (
                await client.post("/api/v1/risks/register/start-revision", headers=hs)
            ).status_code == 200
            return
        if state == "InReview":
            await _approve_and_release(client, head_id, hap, hrl)
            continue
        if state == "Approved":
            assert (
                await client.post("/api/v1/risks/register/release", headers=hrl)
            ).status_code == 200
            continue
        raise AssertionError(f"unexpected register state {state}")
    raise AssertionError("could not normalize the register head to editable")


async def test_register_publish_freeze_and_revision_lifecycle(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_register_head: None,
) -> None:
    """The full controlled-document lifecycle on the shared head: publish freezes (rows+criteria) →
    approve → release → Effective + read-only; then start-revision → re-score → publish → approve →
    release SUPERSEDES v1 in place (new Effective version; v1 → Superseded), the governing snapshot
    carrying the re-scored row. The restore_register_head teardown returns the head to editable
    (non-polluting) even if the body fails mid-lifecycle."""
    await _setup_actors(subj)
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    row = await _create_risk(app_client, hs, likelihood=4, severity=5)  # 20 → critical
    head_id = row["register_doc_id"]

    pub = await app_client.post("/api/v1/risks/register/publish", headers=hs)
    assert pub.status_code == 200, pub.text
    assert pub.json()["state"] == "InReview"

    # the frozen Draft version carries the rows + the per-method criteria (the freeze).
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == uuid.UUID(head_id))
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one()
        reg = (v.metadata_snapshot or {}).get("risk_register")
        assert reg is not None
        assert any(r["id"] == row["id"] for r in reg["rows"])
        assert "5x5_matrix" in reg["criteria"]

    # approve, then prove SoD-2 FIRES: the steward (the version author) holds document.release but
    # cannot release their OWN register (403 sod_violation, not a missing-grant permission_denied).
    task_id = await s5.task_for_doc(head_id)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    self_rel = await app_client.post("/api/v1/risks/register/release", headers=hs)
    assert self_rel.status_code == 403, self_rel.text
    assert self_rel.json()["code"] == "sod_violation"
    # the distinct third-party releaser completes the cutover.
    rel = await app_client.post("/api/v1/risks/register/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    released = rel.json()
    assert released["state"] == "Effective"
    assert released["has_governing"] is True
    v1_id = released["current_effective_version_id"]

    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(head_id))
        assert doc is not None and doc.current_state is DocumentCurrentState.Effective
        v = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert v is not None and v.version_state is VersionState.Effective

    # read-only while Effective — the S-risk-1 edit gate now bites.
    add = await app_client.post(
        "/api/v1/risks",
        headers=hs,
        json={"type": "risk", "description": "late", "likelihood": 1, "severity": 1},
    )
    assert add.status_code == 409, add.text
    patch = await app_client.patch(f"/api/v1/risks/{row['id']}", headers=hs, json={"severity": 1})
    assert patch.status_code == 409, patch.text

    # the live band still grades critical (governing frozen criteria == the v1 default).
    listed = await app_client.get("/api/v1/risks", headers=hs)
    assert next(r["band"] for r in listed.json()["data"] if r["id"] == row["id"]) == "critical"

    # --- the revision: re-score → publish → approve → release SUPERSEDES in place ---
    sr = await app_client.post("/api/v1/risks/register/start-revision", headers=hs)
    assert sr.status_code == 200, sr.text
    assert sr.json()["state"] == "UnderRevision"
    rescored = await app_client.patch(
        f"/api/v1/risks/{row['id']}", headers=hs, json={"severity": 1}
    )  # 4x1 = 4 → low
    assert rescored.status_code == 200, rescored.text
    assert rescored.json()["risk_rating"] == 4
    assert rescored.json()["band"] == "low"

    assert (await app_client.post("/api/v1/risks/register/publish", headers=hs)).status_code == 200
    rel2 = await _approve_and_release(app_client, head_id, hap, hrl)
    assert rel2["state"] == "Effective"
    v2_id = rel2["current_effective_version_id"]
    assert v2_id != v1_id  # a new governing version

    async with get_sessionmaker()() as s:
        v1 = await s.get(DocumentVersion, uuid.UUID(v1_id))
        assert v1 is not None and v1.version_state is VersionState.Superseded
        doc = await s.get(DocumentedInformation, uuid.UUID(head_id))
        v2 = await s.get(DocumentVersion, doc.current_effective_version_id)  # type: ignore[union-attr]
        reg2 = (v2.metadata_snapshot or {}).get("risk_register")  # type: ignore[union-attr]
        frozen_row = next(r for r in reg2["rows"] if r["id"] == row["id"])
        assert frozen_row["risk_rating"] == 4  # the new governing snapshot carries the re-score
    # the restore_register_head teardown returns the shared head to editable (even on failure).


async def test_publish_and_start_revision_require_system_register_manage(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The head lifecycle is gated register.manage @ SYSTEM (D-2b): a PROCESS-bound Process Owner —
    who holds register.manage @ PROCESS via the owner-assignment binding — is 403'd on
    publish/start-revision against the org head (the require dependency fires at SYSTEM scope before
    the handler, so this is head-state independent: no register need exist)."""
    await _grant(subj.steward, "register.manage")
    await _grant(subj.steward, "process.create")
    await _grant(subj.steward, "process.assign_owner")
    hs = _auth(token_factory, subj.steward)
    p1 = await _create_process(app_client, hs)
    owner_id = await _user_id(subj.owner)
    await _assign_owner(app_client, hs, p1["id"], owner_id)
    ho = _auth(token_factory, subj.owner)  # PROCESS register.manage only

    assert (await app_client.post("/api/v1/risks/register/publish", headers=ho)).status_code == 403
    assert (
        await app_client.post("/api/v1/risks/register/start-revision", headers=ho)
    ).status_code == 403


async def test_managed_doc_heads_reserved_from_generic_mutations(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """D-3b: the RSK head is reserved from the generic metadata-PATCH, distribution, and
    document-link mutations (a SYSTEM document.* holder gets 422 risk_register_managed_via_risks),
    and the fold extends to the OBJ head (objective_managed_via_objectives) — the round-3 trim
    residual, now closed. State-independent (the reject fires on the doc subtype, pre-mutation), so
    this does not advance the shared head."""
    await _grant(subj.steward, "register.manage")
    for key in (
        "document.read",
        "document.create",
        "document.manage_metadata",
        "document.distribute",
        "document.obsolete",
        "objective.manage",
    ):
        await _grant(subj.steward, key)
    hs = _auth(token_factory, subj.steward)
    # Use the existing org head if one exists; only create a risk to mint one if absent.
    st = await _status(app_client, hs)
    head_id = (
        st["register_doc_id"]
        if st["exists"]
        else (await _create_risk(app_client, hs))["register_doc_id"]
    )

    meta = await app_client.patch(
        f"/api/v1/documents/{head_id}", headers=hs, json={"title": "hijack"}
    )
    assert meta.status_code == 422, meta.text
    assert meta.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    dist = await app_client.post(
        f"/api/v1/documents/{head_id}/distribution",
        headers=hs,
        json={"acknowledgement_required": True},
    )
    assert dist.status_code == 422, dist.text
    assert dist.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    link = await app_client.post(
        f"/api/v1/documents/{head_id}/links",
        headers=hs,
        json={"to_document_id": head_id, "link_type": "references"},
    )
    assert link.status_code == 422, link.text
    assert link.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    # Codex P2: the clause-mapping mutation is reserved (a random clause_id 422s on the reject
    # before the clause is even looked up) — removing the auto 6.1 map would DoS publish's gate.
    cmap = await app_client.post(
        f"/api/v1/documents/{head_id}/clause-mappings",
        headers=hs,
        json={"clause_id": str(uuid.uuid4()), "is_requirement_level": True},
    )
    assert cmap.status_code == 422, cmap.text
    assert cmap.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    # Codex P2: generic obsolete is reserved for the RSK head (else the singleton would be retired,
    # find_head would ignore it, and the next risk would mint a second head orphaning the old rows).
    obs = await app_client.post(
        f"/api/v1/documents/{head_id}/obsolete", headers=hs, json={"reason": "retire the register"}
    )
    assert obs.status_code == 422, obs.text
    assert obs.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    # Codex P2: the RSK head is reserved as a link TARGET too (a link from a normal doc TO it).
    sop = await _create(app_client, hs, await _sop_type_id())
    link_to = await app_client.post(
        f"/api/v1/documents/{sop['id']}/links",
        headers=hs,
        json={"to_document_id": head_id, "link_type": "references"},
    )
    assert link_to.status_code == 422, link_to.text
    assert link_to.json()["errors"][0]["code"] == "risk_register_managed_via_risks"

    # the fold extends to OBJ: a generic metadata PATCH on an objective head 422s too.
    obj = await app_client.post(
        "/api/v1/objectives",
        headers=hs,
        json={
            "title": "Reserved objective",
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
        },
    )
    assert obj.status_code == 201, obj.text
    obj_meta = await app_client.patch(
        f"/api/v1/documents/{obj.json()['id']}", headers=hs, json={"title": "hijack obj"}
    )
    assert obj_meta.status_code == 422, obj_meta.text
    assert obj_meta.json()["errors"][0]["code"] == "objective_managed_via_objectives"


async def test_row_write_serializes_on_head_lock(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Codex P1: add_risk_row / update_risk_row lock the RSK head FOR UPDATE, exactly as
    publish_register does while it freezes the rows — so a row write cannot interleave with a
    publish freeze, leaving live content out of the version the approver signs. Proof: while one
    HOLDS the head lock, a concurrent add_risk_row BLOCKS until it releases, then completes (without
    the lock it would commit immediately, failing the .done() assertion). The S-records-W
    held-lock-blocks idiom. Adds rows only → the head stays editable (non-polluting)."""
    author = f"rsk-ser-{uuid.uuid4().hex[:8]}"
    await _grant(author, "register.manage")
    ha = _auth(token_factory, author)
    first = await _create_risk(app_client, ha)  # mint/find the head + a first row (head editable)
    head_id = uuid.UUID(first["register_doc_id"])

    sink = get_vault_audit_sink()
    sm = get_sessionmaker()
    locker = sm()
    worker = sm()
    try:
        # Hold the head FOR UPDATE, exactly as publish_register / the row writers do.
        await locker.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == head_id)
            .with_for_update()
        )
        actor = await _ensure_user(worker, author)
        add_task = asyncio.create_task(
            add_risk_row(
                worker,
                sink,
                actor,
                type=RiskOpportunityType.risk,
                description="raced row",
                likelihood=2,
                severity=2,
                scoring_method=ScoringMethod.MATRIX_5X5,
            )
        )
        await asyncio.sleep(0.5)
        assert not add_task.done(), "add_risk_row did not block on the held head lock"
        await locker.rollback()  # release the lock → the blocked writer proceeds and serializes
        row = await asyncio.wait_for(add_task, timeout=10)
        assert row is not None
    finally:
        await worker.close()
        await locker.close()
