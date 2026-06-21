"""S-interested-parties-1 integration proofs — the Interested Parties register's controlled-document
publish/freeze/release lifecycle (clause 4.2).

A register goes Draft → (publish) InReview → (approve) Approved → (release) Effective: the working
rows are FROZEN into an immutable version at publish (clause 4.2 has no scoring criteria — rows
only). While Effective the satellite is read-only; a ``start-revision`` reopens the edit window, and
a
second publish→approve→release SUPERSEDES in place (one head per org, never a second). The head
lifecycle is register.manage @ SYSTEM (the steward); release is document.release + SoD-2.

⚠ The IPR head is a per-org SINGLETON shared across the one-org integration DB, so a lifecycle test
that drives it to Effective would 409 every other test's party-create. The head-advancing test
therefore NORMALIZES the head to editable at the start (advancing a prior crashed cycle if needed)
and
RESTORES it to editable at the end — self-provisioning, non-polluting (the context-lifecycle
discipline).
Grants are SYSTEM-scope overrides on JIT users; SoD-2: author ≠ approver ≠ releaser.
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

from easysynq_api.db.models._interested_party_enums import InterestedPartyType
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.interested_parties import add_interested_party
from easysynq_api.services.vault import get_vault_audit_sink

from . import s5_helpers as s5
from .test_mgmt_review import _MR_KEYS, _create_review
from .test_processes import _grant
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        steward=f"kc-ip-s-{salt}",
        approver=f"kc-ip-a-{salt}",
        releaser=f"kc-ip-r-{salt}",
    )


@pytest.fixture
async def restore_interested_party_head(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> Any:
    """Teardown guard for the head-advancing lifecycle test: restore the shared per-org IPR head to
    editable so sibling tests' party-creates succeed — even if the body fails mid-lifecycle (the
    context restore_context_head precedent; a 500 after release() commits would otherwise leave it
    Effective). Best-effort (never masks the body's failure)."""
    yield
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    with contextlib.suppress(Exception):
        await _drive_to_editable(app_client, hs, hap, hrl)


async def _create_party(
    client: AsyncClient, h: dict[str, str], *, needs: str = "fair pricing"
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/interested-parties",
        headers=h,
        json={"party_type": "customer", "party_name": "Acme", "needs_expectations": needs},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _setup_actors(subj: SimpleNamespace) -> None:
    """Steward (author/publisher) holds register.manage+read @ SYSTEM AND document.release — yet
    SoD-2 (not a missing grant) blocks them releasing their OWN register. The approver joins the
    document_approval pool via the Approver role; the releaser is a THIRD party (SoD-2)."""
    await _grant(subj.steward, "register.manage")
    await _grant(subj.steward, "register.read")
    for key in ("document.release", "document.read", "document.read_draft"):
        await _grant(subj.steward, key)
    await s5.grant_role(subj.approver, "Approver")
    for key in ("document.release", "document.read", "document.read_draft"):
        await _grant(subj.releaser, key)


async def _status(client: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get("/api/v1/interested-parties/register", headers=h)
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
    rel = await client.post("/api/v1/interested-parties/register/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    return rel.json()


async def _drive_to_editable(
    client: AsyncClient, hs: dict[str, str], hap: dict[str, str], hrl: dict[str, str]
) -> None:
    """Normalize the org's shared IPR head to an EDITABLE state (Draft/UnderRevision or absent),
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
                await client.post("/api/v1/interested-parties/register/start-revision", headers=hs)
            ).status_code == 200
            return
        if state == "InReview":
            await _approve_and_release(client, head_id, hap, hrl)
            continue
        if state == "Approved":
            assert (
                await client.post("/api/v1/interested-parties/register/release", headers=hrl)
            ).status_code == 200
            continue
        raise AssertionError(f"unexpected register state {state}")
    raise AssertionError("could not normalize the register head to editable")


async def test_register_publish_freeze_and_revision_lifecycle(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_interested_party_head: None,
) -> None:
    """The full controlled-document lifecycle on the shared head: publish freezes (rows only) →
    approve → (SoD-2 blocks self-release) → third-party release → Effective + read-only; then
    start-revision → edit → publish → approve → release SUPERSEDES v1 in place, the governing
    snapshot carrying the edited row. The restore_interested_party_head teardown returns the head to
    editable (non-polluting) even if the body fails mid-lifecycle."""
    await _setup_actors(subj)
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    row = await _create_party(app_client, hs, needs="responsive support")
    head_id = row["register_doc_id"]

    pub = await app_client.post("/api/v1/interested-parties/register/publish", headers=hs)
    assert pub.status_code == 200, pub.text
    assert pub.json()["state"] == "InReview"

    # the frozen Draft version carries the rows under the interested_party_register key (no
    # criteria).
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == uuid.UUID(head_id))
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one()
        reg = (v.metadata_snapshot or {}).get("interested_party_register")
        assert reg is not None
        assert any(r["id"] == row["id"] for r in reg["rows"])
        assert "criteria" not in reg  # clause 4.2 has no scoring criteria (unlike risk)

    # approve, then prove SoD-2 FIRES: the steward (the version author) holds document.release but
    # cannot release their OWN register (403 sod_violation, not a missing-grant permission_denied).
    task_id = await s5.task_for_doc(head_id)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    # the GET /interested-parties/register caps PREDICT the release outcomes below: the steward (the
    # version author) holds document.release but SoD-2 blocks self-release → can_release False, yet
    # holds register.manage → can_manage True. The third-party releaser is the mirror.
    s_caps = await _status(app_client, hs)
    assert s_caps["can_manage"] is True
    assert s_caps["can_release"] is False
    r_caps = await _status(app_client, hrl)
    assert r_caps["can_release"] is True
    assert r_caps["can_manage"] is False
    self_rel = await app_client.post("/api/v1/interested-parties/register/release", headers=hs)
    assert self_rel.status_code == 403, self_rel.text
    assert self_rel.json()["code"] == "sod_violation"
    # the distinct third-party releaser completes the cutover.
    rel = await app_client.post("/api/v1/interested-parties/register/release", headers=hrl)
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

    # read-only while Effective — the edit gate now bites (POST + PATCH 409).
    add = await app_client.post(
        "/api/v1/interested-parties",
        headers=hs,
        json={"party_type": "supplier", "party_name": "late", "needs_expectations": "late"},
    )
    assert add.status_code == 409, add.text
    patch = await app_client.patch(
        f"/api/v1/interested-parties/{row['id']}", headers=hs, json={"status": "closed"}
    )
    assert patch.status_code == 409, patch.text

    # --- the revision: edit → publish → approve → release SUPERSEDES in place ---
    sr = await app_client.post("/api/v1/interested-parties/register/start-revision", headers=hs)
    assert sr.status_code == 200, sr.text
    assert sr.json()["state"] == "UnderRevision"
    edited = await app_client.patch(
        f"/api/v1/interested-parties/{row['id']}",
        headers=hs,
        json={"status": "closed", "influence": "high"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "closed"

    assert (
        await app_client.post("/api/v1/interested-parties/register/publish", headers=hs)
    ).status_code == 200
    rel2 = await _approve_and_release(app_client, head_id, hap, hrl)
    assert rel2["state"] == "Effective"
    v2_id = rel2["current_effective_version_id"]
    assert v2_id != v1_id  # a new governing version

    async with get_sessionmaker()() as s:
        v1 = await s.get(DocumentVersion, uuid.UUID(v1_id))
        assert v1 is not None and v1.version_state is VersionState.Superseded
        doc = await s.get(DocumentedInformation, uuid.UUID(head_id))
        assert doc is not None
        v2 = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert v2 is not None
        reg2 = (v2.metadata_snapshot or {}).get("interested_party_register")
        frozen_row = next(r for r in reg2["rows"] if r["id"] == row["id"])
        assert frozen_row["status"] == "closed"  # the new governing snapshot carries the edit
        assert frozen_row["influence"] == "high"
    # the restore_interested_party_head teardown returns the shared head to editable (even on fail).


async def test_row_write_serializes_on_head_lock(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """add_interested_party locks the IPR head FOR UPDATE, exactly as publish_register does while it
    freezes the rows — so a row write cannot interleave with a publish freeze, leaving live content
    out of the version the approver signs. Proof: while one HOLDS the head lock, a concurrent
    add_interested_party BLOCKS until it releases, then completes (without the lock it would commit
    immediately, failing the .done() assertion). The S-context-1 held-lock-blocks idiom. Adds rows
    only → the head stays editable (non-polluting)."""
    author = f"ip-ser-{uuid.uuid4().hex[:8]}"
    await _grant(author, "register.manage")
    ha = _auth(token_factory, author)
    first = await _create_party(app_client, ha)  # mint/find the head + a first row (head editable)
    head_id = uuid.UUID(first["register_doc_id"])

    sink = get_vault_audit_sink()
    sm = get_sessionmaker()
    locker = sm()
    worker = sm()
    try:
        # Hold the head FOR UPDATE, exactly as publish_register / add_interested_party do.
        await locker.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == head_id)
            .with_for_update()
        )
        actor = await _ensure_user(worker, author)
        add_task = asyncio.create_task(
            add_interested_party(
                worker,
                sink,
                actor,
                party_type=InterestedPartyType.regulator,
                party_name="raced party",
                needs_expectations="x",
            )
        )
        await asyncio.sleep(0.5)
        assert not add_task.done(), "add_interested_party did not block on the held head lock"
        await locker.rollback()  # release the lock → the blocked writer proceeds and serializes
        row = await asyncio.wait_for(add_task, timeout=10)
        assert row is not None
    finally:
        await worker.close()
        await locker.close()


async def test_register_status_capabilities(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """GET /interested-parties/register carries the server-computed can_release/can_manage (the
    steward console's faithful gate). A no-grant member sees both False; a register.manage holder
    sees can_manage True (the SYSTEM steward probe). can_release stays False without that grant
    regardless of the shared head's state (the SoD-2-aware multi-axis True path is proven by the
    lifecycle test, where the caps predict the exact 403/200 release outcomes). Read-only — no head
    drive, non-polluting."""
    plain = f"ip-caps-{uuid.uuid4().hex[:8]}"
    hp = _auth(token_factory, plain)
    st = await _status(app_client, hp)
    assert st["can_manage"] is False
    assert st["can_release"] is False
    await _grant(plain, "register.manage")
    st2 = await _status(app_client, hp)
    assert st2["can_manage"] is True
    assert st2["can_release"] is False  # no document.release grant → faithful False


def _mr_context_changes_input(compile_json: dict[str, Any]) -> dict[str, Any]:
    """The CONTEXT_CHANGES (9.3.2(b)) input row from a compile-inputs response."""
    by_type = {ri["input_type"]: ri for ri in compile_json["inputs"]}
    return by_type["CONTEXT_CHANGES"]


async def test_mr_input_b_sources_governing_parties_snapshot(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_interested_party_head: None,
) -> None:
    """S-interested-parties-2: the Management-Review 9.3.2(b) CONTEXT_CHANGES input sources the
    clause-4 registers' CONTROLLED read-of-record — the GOVERNING (Effective) frozen snapshots of
    BOTH the 4.1 context AND 4.2 interested-parties registers, NEVER the live working satellites.
    The nested {context, interested_parties} envelope carries each half (null when its register is
    unpublished). Drive a customer party to Effective → input-(b) is available with the 4.2 half
    summarized (by_party_type/by_influence). Then start a revision and ADD a live party — a fresh MR
    still reads the SAME governing 4.2 snapshot (the live add is invisible to the read-of-record),
    proving the WORM minutes can never freeze the steward's unpublished UnderRevision edits. The
    restore_interested_party_head teardown returns the shared head to editable."""
    await _setup_actors(subj)  # grants the steward register.manage + register.read @ SYSTEM
    for key in _MR_KEYS:  # the steward IS the MR owner whose grants gate the per-source reads (F3)
        await _grant(subj.steward, key)
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    # a customer party (high influence) on the working satellite, driven to Effective.
    row = await _create_party(app_client, hs, needs="mr-input-b")
    head_id = row["register_doc_id"]
    patched = await app_client.patch(
        f"/api/v1/interested-parties/{row['id']}", headers=hs, json={"influence": "high"}
    )
    assert patched.status_code == 200, patched.text

    assert (
        await app_client.post("/api/v1/interested-parties/register/publish", headers=hs)
    ).status_code == 200
    released = await _approve_and_release(app_client, head_id, hap, hrl)
    assert released["state"] == "Effective"

    # MR #1 — input-(b) is available; the nested envelope's 4.2 half is the governing summary.
    rid1 = await _create_review(app_client, hs, "MR reading the governing registers")
    c1 = await app_client.post(f"/api/v1/management-reviews/{rid1}/compile-inputs", headers=hs)
    assert c1.status_code == 200, c1.text
    ctx1 = _mr_context_changes_input(c1.json())
    assert ctx1["available"] is True, ctx1
    summary1 = ctx1["source_ref"]["summary"]
    assert set(summary1) == {"context", "interested_parties"}, summary1
    parties1 = summary1["interested_parties"]
    assert parties1 is not None, summary1  # the 4.2 register IS published (I just released it)
    assert set(parties1["by_party_type"]) == {
        "customer",
        "regulator",
        "supplier",
        "employee",
        "owner",
        "community",
        "partner",
    }
    assert set(parties1["by_influence"]) == {"low", "medium", "high", "unspecified"}
    assert parties1["by_party_type"]["customer"] >= 1  # my row (others may add; delta-robust)
    assert parties1["by_influence"]["high"] >= 1
    # the 4.1 context half is whatever this shard's shared CTX head state is (null OR a dict).

    # start a revision and ADD a live party. The governing Effective version is unchanged (its
    # pointer only moves at the next release).
    assert (
        await app_client.post("/api/v1/interested-parties/register/start-revision", headers=hs)
    ).status_code == 200
    await _create_party(app_client, hs, needs="live-party-after-publish")

    # MR #2 — a fresh compile during UnderRevision STILL reads the governing 4.2 snapshot: the
    # interested_parties half is BYTE-IDENTICAL (the live add is invisible to the read-of-record).
    rid2 = await _create_review(app_client, hs, "MR after the live add")
    c2 = await app_client.post(f"/api/v1/management-reviews/{rid2}/compile-inputs", headers=hs)
    assert c2.status_code == 200, c2.text
    ctx2 = _mr_context_changes_input(c2.json())
    assert ctx2["available"] is True, ctx2
    assert ctx2["source_ref"]["summary"]["interested_parties"] == parties1
    # the restore_interested_party_head teardown returns the shared head to editable (even on fail).
