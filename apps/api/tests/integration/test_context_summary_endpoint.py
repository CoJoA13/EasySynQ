"""S-context-2 integration proofs — GET /context/summary (the Home/dashboard + Context-SPA seam).

The endpoint projects the GOVERNING (current Effective) context register snapshot via the pure
``summarize_register`` — the CONTROLLED read-of-record, never the live working satellite (the MR
read-of-record discipline, S-risk-2/-4a). It is gated ``register.read`` @ SYSTEM (org-level, not a
per-row filter — clause 4.1 is fully org-wide): a SYSTEM grant matches, a no-grant caller gets 403.
``published`` is false (with all-zero counts) before the first publish/release, and equals the
head's ``has_governing`` thereafter.

⚠ The CTX head is a per-org SINGLETON shared across the one-org integration DB. Once ANY test
releases the register, ``governing`` is never ``None`` again (the pointer persists through
UnderRevision), so the ``published:false`` branch is NOT deterministically reachable here — it is
covered at the unit level (``test_context_summary.py::test_empty_published_register_is_all_zeros``).
This file proves: the positive summary + the read-of-record invariance (deterministic — drives the
shared head and restores it), the no-grant 403, and the ``published == has_governing`` invariant
(which exercises the ``published:false`` + zeros branch whenever a shard runs it before any
release). Counts are asserted ``>=`` / delta-robust (the shared register accretes sibling rows)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from .test_context_lifecycle import (
    _approve_and_release,
    _create_issue,
    _drive_to_editable,
    _setup_actors,
    _status,
    restore_context_head,
    subj,
)
from .test_processes import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

# Re-export the imported fixtures so pytest collects them in THIS module (the test_context_lifecycle
# fixture-reuse idiom — the test_risk_summary_endpoint precedent). ``restore_context_head`` depends
# on ``subj`` + app_client/token_factory.
__all__ = ["restore_context_head", "subj"]


async def _summary(client: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get("/api/v1/context/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _context_ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/context", headers=h)
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()["data"]}


async def test_summary_reads_governing_and_is_invariant_to_live_edits(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_context_head: None,
) -> None:
    """The strong positive + the read-of-record proof. Drive an internal issue to Effective → GET
    /context/summary is published:true with the governing counts (total ≥ 1, internal ≥ 1, active
    ≥ 1). Then start a revision and ADD a new issue on the LIVE satellite — the summary is UNCHANGED
    (it reads the frozen governing snapshot, not the working edit), while GET /context shows the new
    live row. The restore_context_head teardown returns the shared head to editable."""
    await _setup_actors(subj)  # grants the steward register.manage + register.read @ SYSTEM
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    row = await _create_issue(app_client, hs, description="summary-governing-internal")
    head_id = row["register_doc_id"]

    # publish → approve → (third-party) release → the frozen Effective governing snapshot.
    assert (
        await app_client.post("/api/v1/context/register/publish", headers=hs)
    ).status_code == 200
    released = await _approve_and_release(app_client, head_id, hap, hrl)
    assert released["state"] == "Effective"

    summ = await _summary(app_client, hs)
    assert summ["published"] is True, summ
    assert set(summ["by_classification"]) == {"internal", "external"}
    assert set(summ["by_category"]) == {
        "strength",
        "weakness",
        "opportunity",
        "threat",
        "uncategorized",
    }
    assert set(summ["by_status"]) == {"active", "closed"}
    assert summ["total"] >= 1, summ  # my internal row (others may add; delta-robust)
    assert summ["by_classification"]["internal"] >= 1, summ
    assert summ["by_status"]["active"] >= 1, summ
    assert summ["active"] == summ["by_status"]["active"]  # the headline is the active count
    # my just-created row had no last_reviewed_at → it counts toward never_reviewed.
    assert summ["never_reviewed"] >= 1, summ
    governing_ids = await _context_ids(app_client, hs)
    assert row["id"] in governing_ids

    # start a revision and ADD a new issue on the LIVE satellite. The governing Effective version is
    # unchanged (its pointer only moves at the next release).
    assert (
        await app_client.post("/api/v1/context/register/start-revision", headers=hs)
    ).status_code == 200
    new_row = await _create_issue(app_client, hs, description="live-edit-after-publish")

    # GET /context (the live satellite) shows the new row — yet the summary's controlled read stays
    # the governing snapshot.
    live_ids = await _context_ids(app_client, hs)
    assert new_row["id"] in live_ids
    assert new_row["id"] not in governing_ids  # it post-dates the frozen governing version

    # the summary STILL reads the governing snapshot: byte-identical to the pre-edit read (the live
    # add is invisible to the controlled read-of-record) — the read-of-record proof at the endpoint.
    summ2 = await _summary(app_client, hs)
    assert summ2 == summ, (summ2, summ)
    # the restore_context_head teardown returns the shared head to editable (even on failure).


async def test_summary_requires_register_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The summary is an ENFORCED org-level read (register.read @ SYSTEM), not a filtered list: a
    no-grant caller gets 403, never a 200+empty. Head-state independent (the require dependency
    fires before the handler; no register need exist)."""
    subject = f"ctx-summ-noread-{uuid.uuid4().hex[:8]}"
    h = _auth(token_factory, subject)
    r = await app_client.get("/api/v1/context/summary", headers=h)
    assert r.status_code == 403, r.text


async def test_summary_published_tracks_has_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The deterministic invariant tying the ``published`` wrapper to the canonical lifecycle
    signal, whatever the shared head's current state: ``summary.published == has_governing``, and
    when NOT published every count is zero (the published:false branch — exercised whenever this
    runs before any release in the shard; otherwise the published:true side is asserted). Needs only
    register.read."""
    subject = f"ctx-summ-inv-{uuid.uuid4().hex[:8]}"
    await _grant(subject, "register.read")
    h = _auth(token_factory, subject)

    reg = await _status(app_client, h)
    summ = await _summary(app_client, h)
    assert summ["published"] == reg["has_governing"], (summ, reg)
    if not summ["published"]:
        assert summ["total"] == 0, summ
        assert summ["active"] == 0, summ
        assert summ["never_reviewed"] == 0, summ
        assert all(v == 0 for v in summ["by_classification"].values()), summ
        assert all(v == 0 for v in summ["by_category"].values()), summ
        assert all(v == 0 for v in summ["by_status"].values()), summ
