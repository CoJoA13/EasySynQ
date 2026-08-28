"""[Audit U14] The four register listings (/capas, /risks, /improvement-initiatives, /audits)
bound their pre-authz scan window (newest-first, the S-web-2 posture) and flag an at-cap scan
with ``truncated`` — previously every org row was loaded + per-row authorized on each request,
and a cap without the flag would silently read as a complete register. Each test shrinks the
cap to 1 via monkeypatch (the repos and endpoints read ``listing.REGISTER_SCAN_CAP`` at call
time), proves the window + flag, then restores and proves the untruncated shape."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from easysynq_api.services.common import listing

from .test_audits import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


def _mine(body: dict[str, Any], ids: set[str]) -> list[str]:
    return [row["id"] for row in body["data"] if row["id"] in ids]


async def test_capas_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-capa")
    await _grant(subject, ("capa.create", "capa.read"))
    h = _auth(token_factory, subject)
    ids = set()
    for i in range(2):
        r = await app_client.post(
            "/api/v1/capas", headers=h, json={"title": f"cap probe {i}", "severity": "Minor"}
        )
        assert r.status_code == 201, r.text
        ids.add(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/capas", headers=h)).json()
    assert capped["truncated"] is True
    assert len(_mine(capped, ids)) == 1  # only the newest row fits the window
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/capas", headers=h)).json()
    assert full["truncated"] is False
    assert len(_mine(full, ids)) == 2


async def test_improvement_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-imp")
    await _grant(subject, ("improvement.manage", "improvement.read"))
    h = _auth(token_factory, subject)
    ids = set()
    for i in range(2):
        r = await app_client.post(
            "/api/v1/improvement-initiatives", headers=h, json={"title": f"cap probe {i}"}
        )
        assert r.status_code == 201, r.text
        ids.add(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/improvement-initiatives", headers=h)).json()
    assert capped["truncated"] is True
    assert len(_mine(capped, ids)) == 1
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/improvement-initiatives", headers=h)).json()
    assert full["truncated"] is False
    assert len(_mine(full, ids)) == 2


async def test_risks_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-risk")
    await _grant(subject, ("register.manage", "register.read"))
    h = _auth(token_factory, subject)
    ids = set()
    for i in range(2):
        r = await app_client.post(
            "/api/v1/risks",
            headers=h,
            json={
                "type": "risk",
                "description": f"cap probe {i}",
                "likelihood": 2,
                "severity": 2,
            },
        )
        assert r.status_code == 201, r.text
        ids.add(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/risks", headers=h)).json()
    assert capped["truncated"] is True
    assert len(_mine(capped, ids)) == 1
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/risks", headers=h)).json()
    assert full["truncated"] is False
    assert len(_mine(full, ids)) == 2


async def test_audits_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-aud")
    await _grant(subject, ("audit.read", "audit.plan", "audit.create"))
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/audit-programs",
        headers=h,
        json={"title": "Cap probe programme", "period": "2026"},
    )
    assert r.status_code == 201, r.text
    program_id = r.json()["id"]
    ids = set()
    for i in range(2):
        r = await app_client.post(
            f"/api/v1/audit-programs/{program_id}/plans",
            headers=h,
            json={"scheduled_date": f"2026-09-0{i + 1}"},
        )
        assert r.status_code == 201, r.text
        r = await app_client.post(
            "/api/v1/audits",
            headers=h,
            json={"plan_id": r.json()["id"], "title": f"Cap probe audit {i}"},
        )
        assert r.status_code == 201, r.text
        ids.add(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/audits", headers=h)).json()
    assert capped["truncated"] is True
    assert len(_mine(capped, ids)) == 1
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/audits", headers=h)).json()
    assert full["truncated"] is False
    assert len(_mine(full, ids)) == 2
