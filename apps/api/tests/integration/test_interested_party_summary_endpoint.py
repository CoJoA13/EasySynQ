"""S-interested-parties-2 integration proofs — GET /interested-parties/summary (the Home/dashboard +
IP-SPA seam).

The endpoint projects the GOVERNING (current Effective) interested-parties register snapshot via the
pure ``summarize_register`` — the CONTROLLED read-of-record, never the live satellite (the MR
read-of-record discipline, S-risk-2/S-context-2). It is gated ``register.read`` @ SYSTEM (org-level,
not a per-row filter — clause 4.2 is fully org-wide): a SYSTEM grant matches, a no-grant caller gets
403. ``published`` is false (with all-zero counts) before the first publish/release, and equals the
head's ``has_governing`` thereafter.

⚠ The IPR head is a per-org SINGLETON shared across the one-org integration DB. Once ANY test
releases the register, ``governing`` is never ``None`` again (the pointer persists through
UnderRevision), so the ``published:false`` branch is NOT deterministically reachable here — it is
covered at the unit level (``test_interested_party_summary`` — the empty-register all-zeros test).
This file proves: the positive summary + the read-of-record invariance (deterministic — drives the
shared head and restores it), the no-grant 403, and the ``published == has_governing`` invariant
(which exercises the ``published:false`` + zeros branch when a shard runs before any release).
Counts are asserted ``>=`` / delta-robust (the shared register accretes sibling rows)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from .test_interested_party_lifecycle import (
    _approve_and_release,
    _create_party,
    _drive_to_editable,
    _setup_actors,
    _status,
    restore_interested_party_head,
    subj,
)
from .test_processes import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

# Re-export the imported fixtures so pytest collects them in THIS module (the fixture-reuse idiom —
# the test_context_summary_endpoint / test_risk_summary_endpoint precedent). The
# ``restore_interested_party_head`` fixture depends on ``subj`` + app_client/token_factory.
__all__ = ["restore_interested_party_head", "subj"]


async def _summary(client: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get("/api/v1/interested-parties/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _party_ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/interested-parties", headers=h)
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()["data"]}


async def test_summary_reads_governing_and_is_invariant_to_live_edits(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_interested_party_head: None,
) -> None:
    """The strong positive + the read-of-record proof. Drive a customer party to Effective → GET
    /interested-parties/summary is published:true (total ≥ 1, customer ≥ 1,
    active ≥ 1). Then start a revision and ADD a new party on the LIVE satellite — the summary is
    UNCHANGED (it reads the frozen governing snapshot, not the working edit), while GET
    /interested-parties shows the new live row. The restore_interested_party_head teardown returns
    the shared head to editable."""
    await _setup_actors(subj)  # grants the steward register.manage + register.read @ SYSTEM
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    row = await _create_party(app_client, hs, needs="summary-governing-customer")
    head_id = row["register_doc_id"]

    # publish → approve → (third-party) release → the frozen Effective governing snapshot.
    assert (
        await app_client.post("/api/v1/interested-parties/register/publish", headers=hs)
    ).status_code == 200
    released = await _approve_and_release(app_client, head_id, hap, hrl)
    assert released["state"] == "Effective"

    summ = await _summary(app_client, hs)
    assert summ["published"] is True, summ
    assert set(summ["by_party_type"]) == {
        "customer",
        "regulator",
        "supplier",
        "employee",
        "owner",
        "community",
        "partner",
    }
    assert set(summ["by_influence"]) == {"low", "medium", "high", "unspecified"}
    assert set(summ["by_status"]) == {"active", "closed"}
    assert summ["total"] >= 1, summ  # my customer row (others may add; delta-robust)
    assert summ["by_party_type"]["customer"] >= 1, summ
    assert summ["by_status"]["active"] >= 1, summ
    assert summ["active"] == summ["by_status"]["active"]  # the headline is the active count
    # my just-created party had no influence → unspecified; no last_reviewed_at → never_reviewed.
    assert summ["by_influence"]["unspecified"] >= 1, summ
    assert summ["never_reviewed"] >= 1, summ
    governing_ids = await _party_ids(app_client, hs)
    assert row["id"] in governing_ids

    # start a revision and ADD a new party on the LIVE satellite. The governing Effective version is
    # unchanged (its pointer only moves at the next release).
    assert (
        await app_client.post("/api/v1/interested-parties/register/start-revision", headers=hs)
    ).status_code == 200
    new_row = await _create_party(app_client, hs, needs="live-edit-after-publish")

    # GET /interested-parties (the live satellite) shows the new row — yet the summary's controlled
    # read stays the governing snapshot.
    live_ids = await _party_ids(app_client, hs)
    assert new_row["id"] in live_ids
    assert new_row["id"] not in governing_ids  # it post-dates the frozen governing version

    # the summary STILL reads the governing snapshot: byte-identical to the pre-edit read (the live
    # add is invisible to the controlled read-of-record) — the read-of-record proof at the endpoint.
    summ2 = await _summary(app_client, hs)
    assert summ2 == summ, (summ2, summ)
    # the restore_interested_party_head teardown returns the shared head to editable (even on fail).


async def test_summary_requires_register_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The summary is an ENFORCED org-level read (register.read @ SYSTEM), not a filtered list: a
    no-grant caller gets 403, never a 200+empty. Head-state independent (the require dep fires
    before the handler; no register need exist)."""
    subject = f"ip-summ-noread-{uuid.uuid4().hex[:8]}"
    h = _auth(token_factory, subject)
    r = await app_client.get("/api/v1/interested-parties/summary", headers=h)
    assert r.status_code == 403, r.text


async def test_summary_published_tracks_has_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The invariant tying the ``published`` wrapper to the canonical lifecycle signal,
    whatever the shared head's current state: ``summary.published == has_governing``, and when NOT
    published every count is zero (the published:false branch — exercised whenever this runs before
    any release in the shard; otherwise the published:true side is asserted). Needs only
    register.read."""
    subject = f"ip-summ-inv-{uuid.uuid4().hex[:8]}"
    await _grant(subject, "register.read")
    h = _auth(token_factory, subject)

    reg = await _status(app_client, h)
    summ = await _summary(app_client, h)
    assert summ["published"] == reg["has_governing"], (summ, reg)
    if not summ["published"]:
        assert summ["total"] == 0, summ
        assert summ["active"] == 0, summ
        assert summ["never_reviewed"] == 0, summ
        assert all(v == 0 for v in summ["by_party_type"].values()), summ
        assert all(v == 0 for v in summ["by_influence"].values()), summ
        assert all(v == 0 for v in summ["by_status"].values()), summ
