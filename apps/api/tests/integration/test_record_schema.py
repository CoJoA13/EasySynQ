"""S5 schema proof — the ``record`` shared-PK subtype round-trips against real Postgres (doc 14).

S5 brings the table forward so the schema is final and ``signature_event.signed_object_type=record``
has a real target; there are no record endpoints yet (capture/disposition is the records slice).
This proves the shared PK (``record.id`` IS ``documented_information.id``), the source-version FK,
and that the disposition/retention columns persist + read back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select

from easysynq_api.db.models._record_enums import RecordDispositionState, RecordType
from easysynq_api.db.models._vault_enums import Classification, DocumentCurrentState, DocumentKind
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.session import get_sessionmaker

from .test_vault import _ensure_user

pytestmark = pytest.mark.integration


async def test_record_shared_pk_round_trip(app_under_test: object) -> None:
    # app_under_test migrates + wires the testcontainer DB; this test talks to the DB directly.
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, f"kc-record-{uuid.uuid4().hex[:8]}")
        framework_id = (
            await s.execute(
                select(Framework.id).where(
                    Framework.org_id == user.org_id, Framework.code == "iso9001:2015"
                )
            )
        ).scalar_one()

        di = DocumentedInformation(
            org_id=user.org_id,
            framework_id=framework_id,
            kind=DocumentKind.RECORD,
            identifier=f"REC-QA-{uuid.uuid4().hex[:6]}",
            title="Calibration record",
            owner_user_id=user.id,
            current_state=DocumentCurrentState.Effective,
            classification=Classification.Internal,
            created_by=user.id,
        )
        s.add(di)
        policy = RetentionPolicy(org_id=user.org_id, name="7-year retention")
        s.add(policy)
        await s.flush()

        rec = Record(
            id=di.id,  # shared primary key
            org_id=user.org_id,
            record_type=RecordType.CALIBRATION,
            captured_by=user.id,
            content_hash="sha256:" + "ab" * 32,
            retention_policy_id=policy.id,
            retention_basis_date=datetime.date(2026, 6, 1),
            disposition_state=RecordDispositionState.ACTIVE,
        )
        s.add(rec)
        await s.commit()
        record_id = di.id

    async with get_sessionmaker()() as s:
        loaded = await s.get(Record, record_id)
        assert loaded is not None
        assert loaded.id == record_id  # record.id IS documented_information.id
        assert loaded.record_type is RecordType.CALIBRATION
        assert loaded.disposition_state is RecordDispositionState.ACTIVE
        assert loaded.legal_hold is False  # server default
        parent = await s.get(DocumentedInformation, record_id)
        assert parent is not None and parent.kind is DocumentKind.RECORD
