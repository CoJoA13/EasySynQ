"""The CAPA / Risk / Audit registers must be reachable BEYOND their scan window.

Those three listings took no query parameters at all: they loaded a fixed newest-first window
(``REGISTER_SCAN_CAP``) and reported ``truncated``, so once an org passed the cap its OLDEST rows
were unreachable through the API and the SPA alike. For a CAPA register that is ISO-9001 audit
evidence, "you cannot retrieve your oldest entries" is the wrong answer.

Each test shrinks the cap to 1 (the ``test_register_scan_cap`` precedent) and then narrows to the
row the window CANNOT hold. That is the whole claim in one assertion: a filter applied after the
window could only trim what came back inside it, so retrieving the older row proves the condition
reached SQL ahead of the LIMIT.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.services.common import listing

from .test_audits import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


def _ids(body: dict[str, object]) -> list[str]:
    return [row["id"] for row in body["data"]]  # type: ignore[index,union-attr]


async def test_a_capa_older_than_the_window_is_reachable_by_narrowing(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("filt-capa")
    await _grant(subject, ("capa.create", "capa.read"))
    h = _auth(token_factory, subject)

    older = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "older", "severity": "Critical"}
        )
    ).json()["id"]
    newer = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "newer", "severity": "Minor"}
        )
    ).json()["id"]

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)

    # Unfiltered, the window holds only the newest row — `older` is unreachable.
    assert older not in _ids((await app_client.get("/api/v1/capas", headers=h)).json())

    # Narrowing to its severity retrieves it, which is only possible if the condition ran in SQL
    # before the LIMIT.
    narrowed = (
        await app_client.get("/api/v1/capas?filter[severity][eq]=Critical", headers=h)
    ).json()
    assert older in _ids(narrowed)
    assert newer not in _ids(narrowed)


async def test_a_risk_older_than_the_window_is_reachable_by_narrowing(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("filt-risk")
    await _grant(subject, ("register.manage", "register.read"))
    h = _auth(token_factory, subject)

    def _body(kind: str, label: str) -> dict[str, object]:
        return {"type": kind, "description": label, "likelihood": 2, "severity": 2}

    older = (
        await app_client.post("/api/v1/risks", headers=h, json=_body("opportunity", "older"))
    ).json()["id"]
    (await app_client.post("/api/v1/risks", headers=h, json=_body("risk", "newer"))).json()

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    assert older not in _ids((await app_client.get("/api/v1/risks", headers=h)).json())
    narrowed = (
        await app_client.get("/api/v1/risks?filter[type][eq]=opportunity", headers=h)
    ).json()
    assert older in _ids(narrowed)


async def test_an_audit_older_than_the_window_is_reachable_by_a_date_window(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The date window is the facet every register shares — it is what an auditor actually asks
    for ("show me last quarter"), and the only one that works when every row has the same state."""
    subject = _subject("filt-aud")
    await _grant(subject, ("audit.read", "audit.plan", "audit.create"))
    h = _auth(token_factory, subject)
    program_id = (
        await app_client.post(
            "/api/v1/audit-programs",
            headers=h,
            json={"title": "Filter probe programme", "period": "2026"},
        )
    ).json()["id"]

    ids: list[str] = []
    for i in range(2):
        plan = (
            await app_client.post(
                f"/api/v1/audit-programs/{program_id}/plans",
                headers=h,
                json={"scheduled_date": f"2026-09-0{i + 1}"},
            )
        ).json()["id"]
        ids.append(
            (
                await app_client.post(
                    "/api/v1/audits",
                    headers=h,
                    json={"plan_id": plan, "title": f"Filter probe audit {i}"},
                )
            ).json()["id"]
        )

    monkeypatch.setattr(listing, "REGISTER_SCAN_CAP", 1)
    assert ids[0] not in _ids((await app_client.get("/api/v1/audits", headers=h)).json())

    # A wide-open lower bound still narrows nothing away, so both rows are candidates and the
    # window keeps the newest; the point here is that the parameter is ACCEPTED and applied in SQL.
    ok = await app_client.get("/api/v1/audits?filter[created_at][gte]=2000-01-01", headers=h)
    assert ok.status_code == 200
    # An upper bound in the past excludes everything — proof the bound reached the query rather
    # than being ignored, which a permissive parser would make indistinguishable from success.
    empty = (
        await app_client.get("/api/v1/audits?filter[created_at][lte]=2000-01-01", headers=h)
    ).json()
    assert empty["data"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/capas?filter[nonsense][eq]=x",
        "/api/v1/risks?filter[nonsense][eq]=x",
        "/api/v1/audits?filter[nonsense][eq]=x",
        "/api/v1/capas?filter[severity][gte]=Minor",
    ],
)
async def test_an_unsupported_filter_is_refused_not_ignored(
    app_client: AsyncClient, token_factory: Callable[..., str], path: str
) -> None:
    """Silently ignoring an unknown facet would let a client believe it had narrowed the register
    — the same ``unknown_filter`` contract GET /documents uses (doc 15 §3.2)."""
    subject = _subject("filt-unk")
    await _grant(subject, ("capa.read", "register.read", "audit.read"))
    h = _auth(token_factory, subject)
    response = await app_client.get(path, headers=h)
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "unknown_filter"


async def test_a_malformed_date_is_a_validation_error(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("filt-bad")
    await _grant(subject, ("capa.read",))
    h = _auth(token_factory, subject)
    response = await app_client.get("/api/v1/capas?filter[created_at][gte]=not-a-date", headers=h)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
