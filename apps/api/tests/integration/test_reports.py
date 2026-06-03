"""S10 integration proofs — the org-wide Compliance Checklist (doc 13 §3.1, doc 02 §2.1 / R30).

Covers: the report.compliance_checklist.read gate (403 without it), the 20-★ shape + rollup, the
GAP→PARTIAL→COVERED coverage transition (Mapped+Effective rule, delta-asserted so the shared
session DB can't perturb it), and that Internal Auditor holds the key via the 0021 backfill grant.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration

_CHECKLIST = "/api/v1/reports/compliance-checklist"


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-a-{salt}", b=f"kc-b-{salt}", c=f"kc-c-{salt}")


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
            s.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=perm.id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
            )
        await s.commit()
        return user.id


async def _clause_by_number(number: str) -> str:
    async with get_sessionmaker()() as s:
        return str(
            (
                await s.execute(
                    select(Clause.id)
                    .join(Framework, Clause.framework_id == Framework.id)
                    .where(Framework.code == "iso9001:2015", Clause.number == number)
                )
            ).scalar_one()
        )


async def _map(client: AsyncClient, h: dict[str, str], doc_id: str, clause_id: str) -> None:
    r = await client.post(
        f"/api/v1/documents/{doc_id}/clause-mappings", headers=h, json={"clause_id": clause_id}
    )
    assert r.status_code == 201, r.text


def _row(body: dict, number: str) -> dict:
    return next(r for r in body["rows"] if r["number"] == number)


async def test_checklist_requires_compliance_key(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a, ("document.read",))  # has *a* permission, but not the checklist key
    r = await app_client.get(_CHECKLIST, headers=_auth(token_factory, subj.a))
    assert r.status_code == 403, r.text


async def test_checklist_shape_and_rollup(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a, ("report.compliance_checklist.read",))
    r = await app_client.get(_CHECKLIST, headers=_auth(token_factory, subj.a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["framework"] == "iso9001:2015"
    assert len(body["rows"]) == 20  # the doc 02 §2.1 / R30 ★ set (incl. 8.5.6)
    assert {row["number"] for row in body["rows"]} >= {"4.3", "5.2", "8.4", "8.5.6", "9.2", "10.2"}
    for row in body["rows"]:
        assert row["status"] in {"COVERED", "PARTIAL", "GAP"}
        assert set(row) >= {
            "clause_id",
            "number",
            "title",
            "pdca_phase",
            "mapped_count",
            "effective_count",
            "status",
        }
    rollup = body["rollup"]
    assert rollup["total"] == 20
    assert rollup["covered"] + rollup["partial"] + rollup["gap"] == 20


async def test_checklist_gap_to_partial_to_covered(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Map a Draft doc to ★ 8.4 → PARTIAL; an Effective doc → COVERED. Delta-asserted so other
    tests' mappings (which use clause '10', not ★ clauses) cannot perturb the result."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await _grant(subj.a, ("report.compliance_checklist.read",))
    org_id = await s5.default_org_id()
    await s5.set_approver_release(
        org_id, True
    )  # SoD-2: approver may release (b approves + releases)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    clause_84 = await _clause_by_number("8.4")

    base = _row((await app_client.get(_CHECKLIST, headers=ha)).json(), "8.4")
    m0, e0 = base["mapped_count"], base["effective_count"]

    # A Draft doc mapped to 8.4 raises mapped_count; not Effective → contributes PARTIAL only.
    draft = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    await _map(app_client, ha, draft, clause_84)
    after_map = _row((await app_client.get(_CHECKLIST, headers=ha)).json(), "8.4")
    assert after_map["mapped_count"] == m0 + 1
    assert after_map["effective_count"] == e0
    if e0 == 0:
        assert after_map["status"] == "PARTIAL"

    # An Effective doc mapped to 8.4 raises effective_count → COVERED.
    eff = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"cov-8.4")
    await _map(app_client, ha, eff["id"], clause_84)
    after_eff = _row((await app_client.get(_CHECKLIST, headers=ha)).json(), "8.4")
    assert after_eff["effective_count"] == e0 + 1
    assert after_eff["status"] == "COVERED"


async def test_internal_auditor_holds_checklist_key(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The 0021 backfill granted report.compliance_checklist.read to the Internal Auditor role —
    a user holding that seeded role reads the checklist out of the box (no override)."""
    await s5.grant_role(subj.c, "Internal Auditor")
    r = await app_client.get(_CHECKLIST, headers=_auth(token_factory, subj.c))
    assert r.status_code == 200, r.text
    assert len(r.json()["rows"]) == 20
