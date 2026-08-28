"""[Audit U14] The four register listings (/capas, /risks, /improvement-initiatives, /audits)
bound their pre-authz scan window (newest-first, the S-web-2 posture) and flag an at-cap scan
with ``truncated`` — previously every org row was loaded + per-row authorized on each request,
and a cap without the flag would silently read as a complete register. Each test shrinks the
cap to 1 via monkeypatch (the repos and endpoints read ``listing.REGISTER_SCAN_CAP`` at call
time), proves the window + flag, then restores and proves the untruncated shape."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.audits import repository as audits_repo
from easysynq_api.services.capa import repository as capa_repo
from easysynq_api.services.common import listing

from .test_audits import _grant
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


def _mine(body: dict[str, Any], ids: list[str]) -> list[str]:
    """This run's rows, in the response's own order."""
    wanted = set(ids)
    return [row["id"] for row in body["data"] if row["id"] in wanted]


async def test_capas_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-capa")
    await _grant(subject, ("capa.create", "capa.read"))
    h = _auth(token_factory, subject)
    ids: list[str] = []
    for i in range(2):
        r = await app_client.post(
            "/api/v1/capas", headers=h, json={"title": f"cap probe {i}", "severity": "Minor"}
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/capas", headers=h)).json()
    assert capped["truncated"] is True
    # NEWEST-first: the window must hold the LAST row created, not the oldest (an .asc()
    # window would serve the oldest rows and hide every recent one while still flagging
    # truncated — the count-only assertion could not tell the difference).
    assert _mine(capped, ids) == [ids[-1]]
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/capas", headers=h)).json()
    assert full["truncated"] is False
    assert sorted(_mine(full, ids)) == sorted(ids)


async def test_improvement_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-imp")
    await _grant(subject, ("improvement.manage", "improvement.read"))
    h = _auth(token_factory, subject)
    ids: list[str] = []
    for i in range(2):
        r = await app_client.post(
            "/api/v1/improvement-initiatives", headers=h, json={"title": f"cap probe {i}"}
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/improvement-initiatives", headers=h)).json()
    assert capped["truncated"] is True
    assert _mine(capped, ids) == [ids[-1]]
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/improvement-initiatives", headers=h)).json()
    assert full["truncated"] is False
    assert sorted(_mine(full, ids)) == sorted(ids)


async def test_risks_scan_window_is_capped_and_flagged(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("cap-risk")
    await _grant(subject, ("register.manage", "register.read"))
    h = _auth(token_factory, subject)
    ids: list[str] = []
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
        ids.append(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/risks", headers=h)).json()
    assert capped["truncated"] is True
    assert _mine(capped, ids) == [ids[-1]]
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/risks", headers=h)).json()
    assert full["truncated"] is False
    assert sorted(_mine(full, ids)) == sorted(ids)


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
    ids: list[str] = []
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
        ids.append(r.json()["id"])

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    capped = (await app_client.get("/api/v1/audits", headers=h)).json()
    assert capped["truncated"] is True
    assert _mine(capped, ids) == [ids[-1]]
    monkeypatch.undo()

    full = (await app_client.get("/api/v1/audits", headers=h)).json()
    assert full["truncated"] is False
    assert sorted(_mine(full, ids)) == sorted(ids)


async def test_management_review_compile_is_not_capped(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[U14 regression guard] The Management Review 9.3.2 input compiler shares list_capas /
    list_audits with the register listings, but its summaries are FROZEN into the minutes as
    ISO-9001 evidence — a silently capped count would seal an under-counted compliance summary
    into an immutable record. The cap belongs to the listing surface (an explicit ``limit``
    argument), so compile must count every row even with the cap shrunk to 1."""
    from .test_mgmt_review import _MR_KEYS, _SOURCE_KEYS, _create_review
    from .test_quality_objectives import _grant as _grant_keys

    subject = _subject("cap-mrcompile")
    await _grant_keys(
        subject,
        _MR_KEYS + _SOURCE_KEYS + ("audit.plan", "audit.create", "capa.create"),
    )
    h = _auth(token_factory, subject)

    # Baseline the org's CURRENT totals (the integration DB is shared, so a bare >= 2 would be
    # vacuous once neighbouring files have seeded rows) — read through the same uncapped repo
    # calls the compiler uses.
    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        audits_before = len(await audits_repo.list_audits(s, org_id))
        capas_before = len(await capa_repo.list_capas(s, org_id))

    # Two audits + two CAPAs, so a cap of 1 would visibly under-count either summary.
    r = await app_client.post(
        "/api/v1/audit-programs",
        headers=h,
        json={"title": "MR compile programme", "period": "2026"},
    )
    assert r.status_code == 201, r.text
    program_id = r.json()["id"]
    for i in range(2):
        plan = await app_client.post(
            f"/api/v1/audit-programs/{program_id}/plans",
            headers=h,
            json={"scheduled_date": f"2026-10-0{i + 1}"},
        )
        assert plan.status_code == 201, plan.text
        made = await app_client.post(
            "/api/v1/audits",
            headers=h,
            json={"plan_id": plan.json()["id"], "title": f"MR compile audit {i}"},
        )
        assert made.status_code == 201, made.text
    for i in range(2):
        made = await app_client.post(
            "/api/v1/capas", headers=h, json={"title": f"MR compile capa {i}", "severity": "Minor"}
        )
        assert made.status_code == 201, made.text

    # Structural pin: the shared repo functions must default to UNBOUNDED. A literal
    # `limit: int | None = 2000` default reads the cap early — the monkeypatch below could not
    # see it, and the MAJOR this test exists to prevent would ship green (false-pass-hunter E3).
    assert inspect.signature(audits_repo.list_audits).parameters["limit"].default is None
    assert inspect.signature(capa_repo.list_capas).parameters["limit"].default is None

    rid = await _create_review(app_client, h, "Cap-independence compile")
    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 200, r.text
    by_type = {ri["input_type"]: ri for ri in r.json()["inputs"]}

    audits = by_type["AUDIT_RESULTS"]
    assert audits["available"] is True, audits
    # EXACT delta (the org is shared, so a bare >= 2 is vacuous once neighbours seed rows).
    assert audits["source_ref"]["summary"]["total"] == audits_before + 2, (
        "the MR audit summary inherited the listing scan cap — frozen evidence would under-count"
    )
    capas = by_type["NONCONFORMITIES_CAPA"]
    assert capas["available"] is True, capas
    # summarize_capas_ncrs counts each CAPA into by_close_state (newly raised → not Closed).
    assert sum(capas["source_ref"]["summary"]["by_close_state"].values()) == capas_before + 2, (
        "the MR CAPA summary inherited the listing scan cap"
    )


async def test_truncated_reflects_the_pre_authz_scan_not_the_visible_rows(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[U14 semantics] ``truncated`` describes the SCAN window, not the caller's visible slice.
    A caller whose grants hide every scanned row must still be told the window filled — if the
    flag were computed post-authz they would see an empty register reported as complete, the
    exact 'silently missing rows reads as covered everything' hazard the flag exists to prevent.
    """
    author = _subject("cap-preauthz")
    await _grant(author, ("capa.create", "capa.read"))
    ha = _auth(token_factory, author)
    for i in range(2):
        r = await app_client.post(
            "/api/v1/capas", headers=ha, json={"title": f"preauthz {i}", "severity": "Minor"}
        )
        assert r.status_code == 201, r.text

    # A JIT user with NO capa.read: the row filter hides everything (200 + empty, never 403).
    blind = _subject("cap-blind")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, blind)
        await s.commit()
    hb = _auth(token_factory, blind)

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    body = (await app_client.get("/api/v1/capas", headers=hb)).json()
    assert body["data"] == []
    assert body["truncated"] is True, (
        "truncated was computed from the VISIBLE rows — a scoped caller would read an empty "
        "register as complete"
    )
