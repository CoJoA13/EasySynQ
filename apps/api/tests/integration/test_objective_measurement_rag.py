"""S-obj-charts (Part 1) integration: per-reading RAG on the measurement endpoints.

GET/POST `…/measurements` now carry a per-reading `rag` (value vs the FROZEN target_at_capture, with
direction/threshold from the GOVERNING commitment). The headline proof: after an S-obj-4 target
revision an OLD reading still grades against its frozen target_at_capture, not the new governing
target. CI-only on the owner's Windows box (testcontainers). Run-scoped / delta assertions — the
session DB is shared.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_objective_revision import _drive_to_effective
from .test_quality_objectives import _OBJ_KEYS, _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_get_measurements_carries_rag(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-rag-get-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = (
        await app_client.post(
            "/api/v1/objectives",
            headers=h,
            json={
                "title": "RAG on GET",
                "target_value": "98",
                "unit": "%",
                "direction": "HIGHER_IS_BETTER",
                "due_date": "2026-12-31",
                "at_risk_threshold": "95",
            },
        )
    ).json()["id"]
    await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": "2026-06-30", "value": "99", "unit": "%"},
    )
    hist = await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=h)
    assert hist.status_code == 200, hist.text
    rows = hist.json()["data"]
    assert len(rows) == 1
    assert rows[0]["rag"] == "green"  # 99 ≥ target 98


async def test_post_measurement_response_carries_rag(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-rag-post-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = (
        await app_client.post(
            "/api/v1/objectives",
            headers=h,
            json={
                "title": "RAG on POST",
                "target_value": "98",
                "unit": "%",
                "direction": "HIGHER_IS_BETTER",
                "due_date": "2026-12-31",
                "at_risk_threshold": "95",
            },
        )
    ).json()["id"]
    r = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": "2026-06-30", "value": "96", "unit": "%"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["rag"] == "amber"  # 95 ≤ 96 < 98


async def test_multi_reading_spans_green_amber_red(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-rag-span-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = (
        await app_client.post(
            "/api/v1/objectives",
            headers=h,
            json={
                "title": "Spanning objective",
                "target_value": "98",
                "unit": "%",
                "direction": "HIGHER_IS_BETTER",
                "due_date": "2026-12-31",
                "at_risk_threshold": "95",
            },
        )
    ).json()["id"]
    # three readings: green (>=98), amber (95..97), red (<95) — all against the frozen target 98
    for period, value in (("2026-03-31", "99"), ("2026-06-30", "96"), ("2026-09-30", "90")):
        rr = await app_client.post(
            f"/api/v1/objectives/{oid}/measurements",
            headers=h,
            json={"period": period, "value": value, "unit": "%"},
        )
        assert rr.status_code == 201, rr.text
    rows = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=h)).json()[
        "data"
    ]
    by_period = {m["period"]: m["rag"] for m in rows}
    assert by_period["2026-03-31"] == "green"
    assert by_period["2026-06-30"] == "amber"
    assert by_period["2026-09-30"] == "red"


async def test_old_reading_grades_against_frozen_target_after_revision(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE headline frozen-verdict proof. _create_objective freezes target=98, threshold=95
    (HIGHER). Record a reading at value 96 → frozen target_at_capture=98 → amber. Revise the target
    DOWN to 92 (threshold 90) and re-release. The old reading still grades against its frozen
    target_at_capture (98) → it stays **amber** (90 ≤ 96 < 98). If it (wrongly) graded against the
    NEW governing target 92, 96 ≥ 92 would read 'green'. So amber is the correctness assertion."""
    oid, ho, hap, hrl = await _drive_to_effective(
        app_client, token_factory, "Frozen-verdict objective"
    )
    # record an OLD reading while the governing target is 98 → frozen at 98, graded amber
    rec = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=ho,
        json={"period": "2026-06-30", "value": "96", "unit": "%"},
    )
    assert rec.status_code == 201, rec.text
    assert rec.json()["rag"] == "amber"
    assert rec.json()["target_at_capture"] == "98"

    # revise the commitment: target 98 → 92, threshold 95 → 90, then re-approve + re-release
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    p = await app_client.patch(
        f"/api/v1/objectives/{oid}",
        headers=ho,
        json={"target_value": "92", "at_risk_threshold": "90"},
    )
    assert p.status_code == 200, p.text
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text

    # the OLD reading STILL grades against its frozen target_at_capture (98), NOT the new
    # governing target (92) — its target_at_capture is unchanged + its rag stays amber.
    rows = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=ho)).json()[
        "data"
    ]
    old = next(m for m in rows if m["period"] == "2026-06-30")
    assert old["target_at_capture"] == "98"  # frozen, never rewritten by the revision
    assert old["rag"] == "amber"  # against frozen 98; would be 'green' against the new target 92


async def test_unknown_id_measurements_stays_200_empty(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The row-is-None branch keeps the current 200 + {"data": []} for an unknown id (no 404)."""
    subject = f"obj-rag-404-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    r = await app_client.get(f"/api/v1/objectives/{uuid.uuid4()}/measurements", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"data": []}
