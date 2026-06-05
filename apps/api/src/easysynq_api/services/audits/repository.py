"""Read helpers for the internal-audit family (S-aud-1). Loads are org-checked in the service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.audit import Audit
from ...db.models.audit_plan import AuditPlan
from ...db.models.audit_program import AuditProgram


async def get_audit_program(session: AsyncSession, program_id: uuid.UUID) -> AuditProgram | None:
    return await session.get(AuditProgram, program_id)


async def get_audit_plan(session: AsyncSession, plan_id: uuid.UUID) -> AuditPlan | None:
    return await session.get(AuditPlan, plan_id)


async def get_audit(
    session: AsyncSession, audit_id: uuid.UUID, *, for_update: bool = False
) -> Audit | None:
    if for_update:
        return (
            await session.execute(select(Audit).where(Audit.id == audit_id).with_for_update())
        ).scalar_one_or_none()
    return await session.get(Audit, audit_id)


async def list_audit_programs(session: AsyncSession, org_id: uuid.UUID) -> Sequence[AuditProgram]:
    return (
        (
            await session.execute(
                select(AuditProgram)
                .where(AuditProgram.org_id == org_id)
                .order_by(AuditProgram.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def list_audit_plans(session: AsyncSession, program_id: uuid.UUID) -> Sequence[AuditPlan]:
    return (
        (
            await session.execute(
                select(AuditPlan)
                .where(AuditPlan.program_id == program_id)
                .order_by(AuditPlan.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def list_audits(session: AsyncSession, org_id: uuid.UUID) -> Sequence[Audit]:
    return (await session.execute(select(Audit).where(Audit.org_id == org_id))).scalars().all()
