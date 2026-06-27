"""Integration test for the backfill_capa_target_dates CLI (S-capa-overdue, Task 7).

TDD shape:
1. Create a non-terminal CAPA via HTTP (Task 2 auto-sets target_completion_date at raise).
2. Force target_completion_date = NULL via UPDATE to simulate a pre-feature CAPA.
3. --dry-run: date stays NULL; the report says it WOULD set 1.
4. Real run: target_completion_date == created_at_in_org_tz.date() + offset[severity].
5. Idempotent re-run: 0 changed (IS NULL filter finds nothing).
6. Terminal CAPA (Rejected, NULL date): NOT touched in any run.

Run-scoped assertions on the specific CAPA we created; reuses the default org; never the full suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from easysynq_api.cli.backfill_capa_target_dates import backfill
from easysynq_api.db.models._capa_enums import CapaCloseState, NcSeverity
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.capa import CAPA_TARGET_DAYS, default_target_date
from easysynq_api.services.common.org_clock import resolve_org_tz

from .test_capa import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

_CAPA_CREATE_KEYS = ("capa.create", "capa.read")


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def test_backfill_capa_target_dates(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Dry-run preserves NULL; real run sets the canonical default; idempotent re-run changes 0.
    A terminal (Rejected) CAPA with NULL date is never touched.
    """
    org_id = await _default_org_id()

    # --- Create a non-terminal CAPA via HTTP (Major severity) ---
    subject = f"kc-backfill-td-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Backfill Target Date Test CAPA", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    # --- Create a terminal (Rejected) CAPA to prove it is never backfilled ---
    r2 = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Rejected Terminal CAPA", "severity": "Minor"},
    )
    assert r2.status_code == 201, r2.text
    terminal_capa_id = uuid.UUID(r2.json()["id"])

    # Force both CAPAs to have NULL target_completion_date (simulating pre-feature rows).
    # Set the terminal CAPA to Rejected state.
    async with get_sessionmaker()() as s:
        await s.execute(update(Capa).where(Capa.id == capa_id).values(target_completion_date=None))
        await s.execute(
            update(Capa)
            .where(Capa.id == terminal_capa_id)
            .values(target_completion_date=None, close_state=CapaCloseState.Rejected)
        )
        await s.commit()

    # --- DRY-RUN: date must stay NULL; report says "would set 1" (for this specific CAPA) ---
    async with get_sessionmaker()() as s:
        changed_dry = await backfill(s, dry_run=True)

    # Our CAPA must appear in the "would change" list
    assert any(c[0] == capa_id for c in changed_dry), (
        f"Dry-run should report capa_id {capa_id} as needing backfill"
    )
    # Terminal CAPA must NOT appear in the list
    assert all(c[0] != terminal_capa_id for c in changed_dry), (
        "Terminal (Rejected) CAPA must not appear in backfill report"
    )

    # The DB value must still be NULL after dry-run
    async with get_sessionmaker()() as s:
        still_null = await s.scalar(select(Capa.target_completion_date).where(Capa.id == capa_id))
    assert still_null is None, "target_completion_date must remain NULL after --dry-run"

    # Terminal CAPA date also still NULL
    async with get_sessionmaker()() as s:
        terminal_still_null = await s.scalar(
            select(Capa.target_completion_date).where(Capa.id == terminal_capa_id)
        )
    assert terminal_still_null is None, "Terminal CAPA date must remain NULL after --dry-run"

    # --- REAL RUN: target_completion_date must equal default_target_date(severity, created_at) ---
    async with get_sessionmaker()() as s:
        changed_real = await backfill(s, dry_run=False)

    # Our CAPA must appear in the changed list
    assert any(c[0] == capa_id for c in changed_real), (
        f"Real run should report capa_id {capa_id} as changed"
    )
    # Terminal CAPA must NOT be changed
    assert all(c[0] != terminal_capa_id for c in changed_real), (
        "Terminal (Rejected) CAPA must not be changed by the real run"
    )

    # Verify the value is correct: created_at_in_org_tz.date() + offset[severity]
    async with get_sessionmaker()() as s:
        org_tz = await resolve_org_tz(s, org_id)
        row = (
            await s.execute(
                select(Capa.target_completion_date, DocumentedInformation.created_at)
                .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
                .where(Capa.id == capa_id)
            )
        ).one()
        actual_date, created_at = row
        raised_on = created_at.astimezone(org_tz).date()
        expected_date = default_target_date(NcSeverity.Major, raised_on)

    assert actual_date == expected_date, (
        f"target_completion_date mismatch: got {actual_date}, expected {expected_date} "
        f"(raised_on={raised_on}, offset={CAPA_TARGET_DAYS[NcSeverity.Major]}d)"
    )

    # Terminal CAPA must still be NULL after the real run
    async with get_sessionmaker()() as s:
        terminal_after = await s.scalar(
            select(Capa.target_completion_date).where(Capa.id == terminal_capa_id)
        )
    assert terminal_after is None, "Terminal CAPA date must still be NULL after real run"

    # --- IDEMPOTENT RE-RUN: our CAPA is already set → 0 changed ---
    async with get_sessionmaker()() as s:
        changed_idem = await backfill(s, dry_run=False)

    assert all(c[0] != capa_id for c in changed_idem), (
        "Idempotent re-run must not re-process an already-set CAPA (IS NULL filter)"
    )
