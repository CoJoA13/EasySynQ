"""S-risk-4a integration proofs — GET /risks/summary (the doc-13 / Home PLAN high-risk read).

The endpoint projects the GOVERNING (current Effective) register snapshot via the pure
``summarize_register`` — the CONTROLLED read-of-record, never the live working satellite (the MR
input-(e) discipline, S-risk-2). It is gated ``register.read`` @ SYSTEM (org-level, not a per-row
filter): a SYSTEM grant matches, a no-grant caller gets 403. ``published`` is false (with all-zero
counts) before the first publish/release, and equals the head's ``has_governing`` thereafter.

⚠ The RSK head is a per-org SINGLETON shared across the one-org integration DB. Once ANY test
releases the register, ``governing`` is never ``None`` again (the pointer persists through
UnderRevision), so the ``published:false`` branch is NOT deterministically reachable here — it is
covered at the unit level (``test_risk_summary.py::test_empty_published_register_is_all_zeros``).
This file proves: the positive summary + the read-of-record invariance (deterministic, drives the
shared head and restores it), the no-grant 403, and the ``published == has_governing`` invariant
(which exercises the ``published:false`` + zeros branch whenever a shard runs it before any
release). Counts are asserted ``>=`` / delta-robust (the shared register accretes sibling rows).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from .test_processes import _grant
from .test_risk_lifecycle import (
    _approve_and_release,
    _create_risk,
    _drive_to_editable,
    _setup_actors,
    _status,
    restore_register_head,
    subj,
)
from .test_vault import _auth

pytestmark = pytest.mark.integration

# Re-export the imported fixtures so pytest collects them in THIS module (the test_risk_lifecycle
# fixture-reuse idiom). ``restore_register_head`` depends on ``subj`` + app_client/token_factory.
__all__ = ["restore_register_head", "subj"]


async def _summary(client: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get("/api/v1/risks/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_summary_reads_governing_and_is_invariant_to_live_edits(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_register_head: None,
) -> None:
    """The strong positive + the read-of-record proof. Drive a critical (4x5=20), treated +
    effectiveness-recorded risk to Effective → GET /risks/summary is published:true with the
    governing counts (high_risk ≥ 1, by_band.critical ≥ 1, effectiveness over treated rows). Then
    start a revision and RE-SCORE that row to low on the LIVE satellite — the summary is UNCHANGED
    (it reads the frozen governing snapshot, not the working edit), while GET /risks shows the live
    low band. The restore_register_head teardown returns the shared head to editable."""
    await _setup_actors(subj)  # grants the steward register.manage + register.read @ SYSTEM
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    row = await _create_risk(
        app_client, hs, likelihood=4, severity=5, description="summary-critical"
    )
    head_id = row["register_doc_id"]
    patched = await app_client.patch(
        f"/api/v1/risks/{row['id']}",
        headers=hs,
        json={"treatment": "mitigate the exposure", "effectiveness": "verified effective"},
    )
    assert patched.status_code == 200, patched.text

    # publish → approve → (third-party) release → the frozen Effective governing snapshot.
    assert (await app_client.post("/api/v1/risks/register/publish", headers=hs)).status_code == 200
    released = await _approve_and_release(app_client, head_id, hap, hrl)
    assert released["state"] == "Effective"

    summ = await _summary(app_client, hs)
    assert summ["published"] is True, summ
    assert set(summ["by_band"]) == {"critical", "high", "medium", "low", "unscored"}
    governing_high_risk = summ["high_risk"]
    assert governing_high_risk >= 1, summ  # my critical row (others may add; delta-robust)
    assert summ["by_band"]["critical"] >= 1
    assert summ["by_type"]["risk"] >= 1
    assert summ["effectiveness"]["treated"] >= 1
    assert summ["effectiveness"]["recorded"] >= 1
    assert (
        summ["effectiveness"]["recorded"] + summ["effectiveness"]["pending"]
        == summ["effectiveness"]["treated"]
    )

    # start a revision and RE-SCORE the row to low (4x1=4) on the LIVE satellite. The governing
    # Effective version is unchanged (its pointer only moves at the next release).
    assert (
        await app_client.post("/api/v1/risks/register/start-revision", headers=hs)
    ).status_code == 200
    rescored = await app_client.patch(
        f"/api/v1/risks/{row['id']}", headers=hs, json={"severity": 1}
    )
    assert rescored.status_code == 200, rescored.text
    assert rescored.json()["band"] == "low"  # the LIVE band reflects the re-score

    # GET /risks shows the row low — yet the summary's controlled read stays the governing snapshot.
    listed = await app_client.get("/api/v1/risks", headers=hs)
    assert next(r["band"] for r in listed.json()["data"] if r["id"] == row["id"]) == "low"

    # the summary STILL reads the governing snapshot: high_risk is UNCHANGED (the live low edit is
    # invisible to the controlled read-of-record) — the read-of-record proof at the endpoint level.
    summ2 = await _summary(app_client, hs)
    assert summ2["published"] is True
    assert summ2["high_risk"] == governing_high_risk
    assert summ2["by_band"]["critical"] >= 1
    # the restore_register_head teardown returns the shared head to editable (even on failure).


async def test_summary_requires_register_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The summary is an ENFORCED org-level read (register.read @ SYSTEM), not a filtered list: a
    no-grant caller gets 403, never a 200+empty. Head-state independent (the require dependency
    fires before the handler; no register need exist)."""
    subject = f"rsk-summ-noread-{uuid.uuid4().hex[:8]}"
    h = _auth(token_factory, subject)
    r = await app_client.get("/api/v1/risks/summary", headers=h)
    assert r.status_code == 403, r.text


async def test_summary_published_tracks_has_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The deterministic invariant tying the ``published`` wrapper to the canonical lifecycle
    signal, whatever the shared head's current state: ``summary.published == has_governing``, and
    when NOT published every count is zero (the published:false branch — exercised whenever this
    runs before any release in the shard; otherwise the published:true side is asserted). Needs only
    register.read."""
    subject = f"rsk-summ-inv-{uuid.uuid4().hex[:8]}"
    await _grant(subject, "register.read")
    h = _auth(token_factory, subject)

    reg = await _status(app_client, h)
    summ = await _summary(app_client, h)
    assert summ["published"] == reg["has_governing"], (summ, reg)
    if not summ["published"]:
        assert summ["total"] == 0, summ
        assert summ["high_risk"] == 0, summ
        assert all(v == 0 for v in summ["by_band"].values()), summ
        assert summ["by_type"] == {"risk": 0, "opportunity": 0}, summ
        assert summ["effectiveness"] == {"treated": 0, "recorded": 0, "pending": 0}, summ
