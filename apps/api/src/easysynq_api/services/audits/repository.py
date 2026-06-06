"""Read helpers for the internal-audit family (S-aud-1/2). Loads are org-checked in the service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._capa_enums import CapaCloseState
from ...db.models._iso_audit_enums import FindingType
from ...db.models.audit import Audit
from ...db.models.audit_finding import AuditFinding
from ...db.models.audit_plan import AuditPlan
from ...db.models.audit_program import AuditProgram
from ...db.models.capa import Capa
from ...db.models.documented_information import DocumentedInformation
from ...db.models.record import Record


async def get_audit_program(session: AsyncSession, program_id: uuid.UUID) -> AuditProgram | None:
    return await session.get(AuditProgram, program_id)


async def get_audit_plan(session: AsyncSession, plan_id: uuid.UUID) -> AuditPlan | None:
    return await session.get(AuditPlan, plan_id)


async def get_identifier(session: AsyncSession, record_id: uuid.UUID) -> str | None:
    """The human identifier of a record subtype (audit / finding), read off the base row."""
    base = await session.get(DocumentedInformation, record_id)
    return base.identifier if base is not None else None


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


# --- findings (S-aud-2) -----------------------------------------------------------------------

# A finding read row: (finding, identifier, correction_of, superseded_by_correction) — the human
# identifier + the correction chain live on the documented_information / record base rows.
FindingRow = tuple[AuditFinding, str, uuid.UUID | None, uuid.UUID | None]


def _finding_select() -> Select[Any]:
    return (
        select(
            AuditFinding,
            DocumentedInformation.identifier,
            Record.correction_of,
            Record.superseded_by_correction,
        )
        .join(DocumentedInformation, DocumentedInformation.id == AuditFinding.id)
        .join(Record, Record.id == AuditFinding.id)
    )


async def get_finding(
    session: AsyncSession, finding_id: uuid.UUID, *, for_update: bool = False
) -> AuditFinding | None:
    if for_update:
        return (
            await session.execute(
                select(AuditFinding).where(AuditFinding.id == finding_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(AuditFinding, finding_id)


async def get_finding_row(session: AsyncSession, finding_id: uuid.UUID) -> FindingRow | None:
    row = (
        await session.execute(_finding_select().where(AuditFinding.id == finding_id))
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2], row[3])


async def list_findings(session: AsyncSession, audit_id: uuid.UUID) -> Sequence[FindingRow]:
    rows = await session.execute(
        _finding_select()
        .where(AuditFinding.audit_id == audit_id)
        .order_by(DocumentedInformation.created_at.asc())
    )
    return [(f, ident, co, sbc) for f, ident, co, sbc in rows.all()]


# A close-gate row: (finding_type, is_superseded, linked CAPA close_state | None). The pure
# domain.audits.finding_blocks_close predicate is applied to each.
CloseGateRow = tuple[FindingType, bool, CapaCloseState | None]


async def findings_for_close_gate(
    session: AsyncSession, audit_id: uuid.UUID
) -> Sequence[CloseGateRow]:
    """Every finding of the audit with the facts the close gate needs: its type, whether it has been
    superseded by a correction (on the record base), and its linked CAPA's close_state (LEFT JOIN on
    auto_capa_id; None when unlinked). Read under the audit FOR UPDATE the caller already holds."""
    rows = await session.execute(
        select(
            AuditFinding.finding_type,
            Record.superseded_by_correction,
            Capa.close_state,
        )
        .join(Record, Record.id == AuditFinding.id)
        .outerjoin(Capa, Capa.id == AuditFinding.auto_capa_id)
        .where(AuditFinding.audit_id == audit_id)
    )
    return [(ft, sbc is not None, cs) for ft, sbc, cs in rows.all()]
