"""S-capa-overdue Task 6 integration proofs — PATCH /capas/{id} target date + serializer fields.

Tests:
- GET /capas/{id} returns target_completion_date (severity default) + overdue: bool.
- PATCH /capas/{id} sets a past date → 200; GET shows new date + overdue: true; overdue_notified_at
  cleared (re-armed).
- PATCH on a terminal (Closed/Rejected) CAPA → 409 capa_terminal.
- A caller WITHOUT capa.update → 403.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.db.models._capa_enums import CapaCloseState
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.session import get_sessionmaker

from .test_capa import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

_UPDATE_KEYS = (
    "capa.read",
    "capa.create",
    "capa.update",
)

_READ_ONLY_KEYS = (
    "capa.read",
    "capa.create",
)


def _subject(prefix: str) -> str:
    return f"kc-tgt-{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _get_capa_row(capa_id: str) -> Capa:
    async with get_sessionmaker()() as s:
        row = await s.get(Capa, uuid.UUID(capa_id))
        assert row is not None
        return row


async def _stamp_overdue_notified(capa_id: str) -> None:
    """Stamp overdue_notified_at so we can verify PATCH clears it (re-arms the sweep)."""
    async with get_sessionmaker()() as s:
        row = await s.get(Capa, uuid.UUID(capa_id))
        assert row is not None
        row.overdue_notified_at = datetime.datetime.now(datetime.UTC)
        await s.commit()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_get_capa_returns_target_completion_date_and_overdue(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """GET /capas/{id} returns target_completion_date (severity-defaulted at raise) + overdue."""
    subject = _subject("rd")
    await _grant(subject, _UPDATE_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Target date read test", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa = r.json()
    capa_id = capa["id"]

    # The create response itself should carry these fields.
    assert "target_completion_date" in capa
    assert "overdue" in capa

    # GET returns them too.
    r2 = await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert "target_completion_date" in data
    assert "overdue" in data
    # A freshly-raised CAPA has a future target date → not overdue.
    assert isinstance(data["overdue"], bool)
    assert data["overdue"] is False
    # The default target date is non-null (seeded by default_target_date).
    assert data["target_completion_date"] is not None


async def test_patch_target_date_sets_date_and_rears(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """PATCH /capas/{id} with a past date → 200; GET shows new date + overdue: true;
    overdue_notified_at is cleared (re-armed for the sweep)."""
    subject = _subject("set")
    await _grant(subject, _UPDATE_KEYS)
    h = _auth(token_factory, subject)

    # Raise a CAPA.
    capa_id = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Set target test", "severity": "Minor"}
        )
    ).json()["id"]

    # Manually stamp overdue_notified_at to prove PATCH clears it.
    await _stamp_overdue_notified(capa_id)
    row_before = await _get_capa_row(capa_id)
    assert row_before.overdue_notified_at is not None

    # PATCH with a date in the past → should mark overdue.
    past_date = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    r = await app_client.patch(
        f"/api/v1/capas/{capa_id}",
        headers=h,
        json={"target_completion_date": past_date},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_completion_date"] == past_date
    assert body["overdue"] is True

    # GET also reflects the change.
    r2 = await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["target_completion_date"] == past_date
    assert data["overdue"] is True

    # overdue_notified_at was cleared (re-armed).
    row_after = await _get_capa_row(capa_id)
    assert row_after.overdue_notified_at is None


async def test_patch_target_date_clear_to_none(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """PATCH with target_completion_date: null clears the date; overdue becomes false."""
    subject = _subject("clr")
    await _grant(subject, _UPDATE_KEYS)
    h = _auth(token_factory, subject)

    capa_id = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Clear target test", "severity": "Minor"}
        )
    ).json()["id"]

    r = await app_client.patch(
        f"/api/v1/capas/{capa_id}",
        headers=h,
        json={"target_completion_date": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_completion_date"] is None
    assert body["overdue"] is False


async def test_patch_terminal_capa_is_409(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """PATCH on a terminal (Closed/Rejected) CAPA → 409 capa_terminal."""
    subject = _subject("term")
    await _grant(subject, _UPDATE_KEYS)
    h = _auth(token_factory, subject)

    # Create a CAPA and manually force it into the Rejected terminal state.
    r = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Terminal test", "severity": "Minor"}
    )
    assert r.status_code == 201, r.text
    capa_id = r.json()["id"]

    # Force the CAPA to a terminal state directly in the DB (no endpoint closes it trivially).
    async with get_sessionmaker()() as s:
        row = await s.get(Capa, uuid.UUID(capa_id))
        assert row is not None
        row.close_state = CapaCloseState.Rejected
        await s.commit()

    r2 = await app_client.patch(
        f"/api/v1/capas/{capa_id}",
        headers=h,
        json={"target_completion_date": "2030-01-01"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "capa_terminal"


async def test_patch_target_date_requires_capa_update(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """A caller without capa.update → 403 on PATCH."""
    owner_subject = _subject("own")
    reader_subject = _subject("rdr")
    await _grant(owner_subject, _UPDATE_KEYS)
    # Reader has only capa.read + capa.create — no capa.update.
    await _grant(reader_subject, _READ_ONLY_KEYS)
    h_owner = _auth(token_factory, owner_subject)
    h_reader = _auth(token_factory, reader_subject)

    capa_id = (
        await app_client.post(
            "/api/v1/capas", headers=h_owner, json={"title": "Authz test", "severity": "Minor"}
        )
    ).json()["id"]

    r = await app_client.patch(
        f"/api/v1/capas/{capa_id}",
        headers=h_reader,
        json={"target_completion_date": "2030-01-01"},
    )
    assert r.status_code == 403, r.text
