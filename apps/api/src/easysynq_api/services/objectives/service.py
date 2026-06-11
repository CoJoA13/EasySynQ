"""Quality Objectives service (S-obj-1) — the txn owner. ``create_objective`` reuses the vault
``create_document`` (kind=DOCUMENT, type OBJ), then adds the satellite + a clause_mapping to 6.2.
``record_measurement`` (Task 6) captures a KPI_READING record + projection + rolls up
current_value."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._objective_enums import ObjectiveDirection
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.quality_objective import QualityObjective
from ...problems import ProblemException
from ..vault import VaultAuditSink, create_document


async def _obj_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "OBJ")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="OBJ document_type is not seeded",
        )
    return dt.id


async def current_effective_policy(
    session: AsyncSession, org_id: uuid.UUID
) -> DocumentedInformation | None:
    """The single Effective Quality Policy (POL singleton, R25), or None."""
    return (
        await session.execute(
            select(DocumentedInformation)
            .join(
                DocumentType,
                DocumentedInformation.document_type_id == DocumentType.id,
            )
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentType.code == "POL",
                DocumentedInformation.current_state == DocumentCurrentState.Effective,
            )
        )
    ).scalar_one_or_none()


async def create_objective(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    *,
    title: str,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    baseline_value: Decimal | None = None,
    at_risk_threshold: Decimal | None = None,
    process_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    area_code: str | None = None,
    folder_path: str | None = None,
    classification: str = "Internal",
) -> QualityObjective:
    """Create a Quality Objective as a kind=DOCUMENT subtype (type OBJ), auto-mapped to 6.2."""
    # Measurable-by-construction: title/target/unit/direction/due are required by the
    # caller/pydantic.
    if policy_id is not None:
        eff = await current_effective_policy(session, actor.org_id)
        if eff is None or eff.id != policy_id:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="policy_id must be the current Effective Quality Policy",
            )
    dt_id = await _obj_document_type_id(session, actor.org_id)
    # create_document commits the base doc (the form_template two-step precedent).
    doc = await create_document(
        session,
        sink,
        actor,
        title=title,
        document_type_id=dt_id,
        area_code=area_code,
        folder_path=folder_path,
        classification=classification,
    )
    qo = QualityObjective(
        id=doc.id,
        org_id=actor.org_id,
        target_value=target_value,
        unit=unit,
        baseline_value=baseline_value,
        current_value=None,
        direction=direction,
        at_risk_threshold=at_risk_threshold,
        due_date=due_date,
        process_id=process_id,
        policy_id=policy_id,
    )
    session.add(qo)
    # Auto-map to clause 6.2 so the ★ checklist resolves on release.
    clause_6_2 = (
        await session.execute(select(Clause).where(Clause.number == "6.2"))
    ).scalar_one_or_none()
    if clause_6_2 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=doc.framework_id,
                clause_id=clause_6_2.id,
                documented_information_id=doc.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
    await session.commit()
    await session.refresh(qo)
    return qo
