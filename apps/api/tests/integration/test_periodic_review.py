"""S-drift-1 integration proofs — periodic re-review write paths.

Note: the GET serializer fields (review_period_months / next_review_due / review_state) land in
the read-surface task (Task 7 of this PR). These tests assert the final API shape; they will be
green once that task lands. Direct DB reads are used as a fallback where the GET body doesn't
yet carry the field, but the test assertions are written for the final API shape.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.review import REVIEW_PERIOD_DEFAULT_MONTHS, add_months

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-pr-author-{salt}", b=f"kc-pr-approver-{salt}")


async def _release_doc(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    type_id: str,
    content: bytes,
) -> tuple[str, dict]:
    """Create → checkout → upload unique bytes → checkin MAJOR → map clause →
    submit-review → approve → release. Returns (doc_id, GET body after release)."""
    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, content)
    ci = await _checkin(
        app_client, ha, did, sha, change_reason="initial", change_significance="MAJOR"
    )
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=ha, json={})
    assert rel.status_code == 200, rel.text
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    return did, body


async def test_create_defaults_review_period_to_24(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A freshly created document gets review_period_months=24 by default; next_review_due
    and review_state are None until first release."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()

    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS  # 24
    assert body.get("next_review_due") is None
    assert body.get("review_state") is None


async def test_release_computes_next_review_due(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """After release, next_review_due == effective_from + 24 months; review_state == 'current'."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-release-{subj.a}".encode()

    _did, body = await _release_doc(app_client, ha, hb, type_id, content)

    assert body.get("current_state") == "Effective"
    # Derive expected next_review_due from the body's effective_from using the same add_months rule.
    eff_from_str = body.get("effective_from")
    assert eff_from_str is not None
    eff_from_dt = datetime.datetime.fromisoformat(eff_from_str)
    from easysynq_api.services.vault.review import _org_tz

    eff_from_date = eff_from_dt.astimezone(_org_tz()).date()
    expected_due = add_months(eff_from_date, REVIEW_PERIOD_DEFAULT_MONTHS)

    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS
    assert body.get("next_review_due") == expected_due.isoformat()
    assert body.get("review_state") == "current"


async def test_patch_review_period_recomputes_and_null_clears(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """PATCH review_period_months recomputes next_review_due; explicit null clears both;
    value=0 is 422; an unrelated PATCH that omits the review field leaves it unchanged
    (model_fields_set guard — the 'unconditional assignment' trap)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-patch-{subj.a}".encode()

    did, body = await _release_doc(app_client, ha, hb, type_id, content)
    eff_from_str = body.get("effective_from")
    assert eff_from_str is not None
    eff_from_dt = datetime.datetime.fromisoformat(eff_from_str)
    from easysynq_api.services.vault.review import _org_tz

    eff_from_date = eff_from_dt.astimezone(_org_tz()).date()

    # PATCH review_period_months=12 → recomputes next_review_due to eff_from + 12 months.
    r1 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 12}
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    expected_12 = add_months(eff_from_date, 12)
    assert b1.get("review_period_months") == 12
    assert b1.get("next_review_due") == expected_12.isoformat()

    # PATCH review_period_months=None → next_review_due clears; review_state clears.
    r2 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": None}
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2.get("next_review_due") is None
    assert b2.get("review_state") is None

    # PATCH review_period_months=0 → 422 (ge=1 constraint).
    r3 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 0}
    )
    assert r3.status_code == 422, r3.text

    # Re-set to 12.
    r4 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 12}
    )
    assert r4.status_code == 200, r4.text

    # PATCH title only (review field OMITTED) → review_period_months STILL 12, next_review_due
    # unchanged. An unconditional assignment without model_fields_set would pass the other three
    # cases but fail this one (the S-web-7d trap).
    r5 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"title": "Drift Test Updated"}
    )
    assert r5.status_code == 200, r5.text
    b5 = r5.json()
    assert b5.get("review_period_months") == 12
    assert b5.get("next_review_due") == expected_12.isoformat()


async def test_submit_review_autodefaults_null_period(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A legacy doc whose review_period_months is NULL gets T2 auto-defaulted at submit-review
    (never a 422); GET after submit → review_period_months == 24."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc = await _create(app_client, ha, type_id)
    did = doc["id"]

    # NULL the review_period_months column directly to simulate a legacy/pre-migration row.
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET review_period_months = NULL WHERE id = :id"),
            {"id": uuid.UUID(did)},
        )
        await s.commit()

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"drift-auto-{subj.a}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)

    # submit-review must succeed (NEVER a 422) even with NULL review_period_months.
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text

    # GET → review_period_months == 24 (the T2 auto-default).
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS  # 24
