"""[Audit U37] The evidence-pack CLAUSE scope leg must anchor the clause to the caller's org
through its framework — it was the only scope leg accepting any org's ids (PROCESS/FINDING/CAPA
all check ``.org_id``)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException
from easysynq_api.services.packs.service import _validate_scope

pytestmark = pytest.mark.integration


async def test_clause_scope_rejects_a_foreign_orgs_clause(app_under_test: object) -> None:
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org_a_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        own_clause_id = (
            await s.execute(
                select(Clause.id)
                .join(Framework, Framework.id == Clause.framework_id)
                .where(Framework.org_id == org_a_id)
                .limit(1)
            )
        ).scalar_one()
        org_b = Organization(
            legal_name=f"Foreign Clause {salt}", short_code=f"FC{salt[:6].upper()}"
        )
        s.add(org_b)
        await s.flush()
        fw_b = Framework(org_id=org_b.id, code=f"FW-{salt}", name="Foreign framework")
        s.add(fw_b)
        await s.flush()
        clause_b = Clause(
            framework_id=fw_b.id,
            number="4",
            title="Foreign clause",
            intent_text="foreign",
            pdca_phase=PdcaPhase.PLAN,
        )
        s.add(clause_b)
        await s.commit()
        org_b_id, fw_b_id, clause_b_id = org_b.id, fw_b.id, clause_b.id

    try:
        async with get_sessionmaker()() as s:
            # The caller's own clause validates.
            ids = await _validate_scope(s, org_a_id, "CLAUSE", {"clause_ids": [str(own_clause_id)]})
            assert ids == [own_clause_id]
            # A foreign org's clause is refused exactly like an unknown id (422, no existence
            # oracle across tenants).
            with pytest.raises(ProblemException) as exc:
                await _validate_scope(s, org_a_id, "CLAUSE", {"clause_ids": [str(clause_b_id)]})
            assert exc.value.status == 422
    finally:
        # A second Organization row breaks test_restore's scalar_one() — clean up FK-safe.
        async with get_sessionmaker()() as s:
            await s.execute(delete(Clause).where(Clause.id == clause_b_id))
            await s.execute(delete(Framework).where(Framework.id == fw_b_id))
            await s.execute(delete(Organization).where(Organization.id == org_b_id))
            await s.commit()
