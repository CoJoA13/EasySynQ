"""Integration test: resolve_subject routes OBJ/MR DOCUMENT notifications to dedicated surfaces.

Task 9 (S-notify-3a) — verifies the full DB path: resolve_subject loads DocumentedInformation,
reads its DocumentType.code, and passes it to deep_link_for.

Seeding strategy: create Draft documents via the API for each type (OBJ/MR/SOP).
resolve_subject only needs the row + its document_type_id to resolve the code — it doesn't
require the document to be Effective.  OBJ/MR/SOP document_type rows are guaranteed present
from migrations 0049/0050/0006.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.db.models._vault_enums import DocumentKind
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.document_type import DocumentType
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.management_review import ManagementReview
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.quality_objective import QualityObjective
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.subjects import resolve_subject

from . import s5_helpers as s5
from .test_vault import _auth, _create

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_obj_document_resolves_to_objectives_surface(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """resolve_subject for a DOCUMENT with an OBJ type AND a QualityObjective satellite links
    to /objectives/{id}.

    R2-2: the satellite probe means an OBJ doc without a QualityObjective row falls back to
    /documents/{id}.  This test seeds the satellite to confirm the happy-path routing still works.
    """
    salt = uuid.uuid4().hex[:10]
    subject = f"kc-subtype-obj-{salt}"
    await s5.grant_lifecycle(subject)
    h = _auth(token_factory, subject)

    type_id = await s5.type_id("OBJ")
    doc = await _create(app_client, h, type_id)
    doc_id = uuid.UUID(doc["id"])

    # Seed the QualityObjective satellite so resolve_subject routes to /objectives/{id}.
    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        s.add(
            QualityObjective(
                id=doc_id,
                org_id=org_id,
                target_value=Decimal("100"),
                unit="units",
                direction=ObjectiveDirection.HIGHER_IS_BETTER,
                due_date=datetime.date(2030, 12, 31),
            )
        )
        await s.commit()

    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", doc_id)

    assert info.deep_link.endswith(f"/objectives/{doc_id}"), (
        f"Expected deep_link ending /objectives/{doc_id}, got {info.deep_link!r}"
    )


async def test_mr_document_resolves_to_management_reviews_surface(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """resolve_subject for a DOCUMENT with an MR type AND a ManagementReview satellite links
    to /management-reviews/{id}.

    R2-2: the satellite probe means an MR doc without a ManagementReview row falls back to
    /documents/{id}.  This test seeds the satellite to confirm the happy-path routing still works.
    """
    salt = uuid.uuid4().hex[:10]
    subject = f"kc-subtype-mr-{salt}"
    await s5.grant_lifecycle(subject)
    h = _auth(token_factory, subject)

    type_id = await s5.type_id("MR")
    doc = await _create(app_client, h, type_id)
    doc_id = uuid.UUID(doc["id"])

    # Seed the ManagementReview satellite so resolve_subject routes to /management-reviews/{id}.
    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        s.add(
            ManagementReview(
                id=doc_id,
                org_id=org_id,
                period_label=f"Q1-{salt}",
            )
        )
        await s.commit()

    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", doc_id)

    assert info.deep_link.endswith(f"/management-reviews/{doc_id}"), (
        f"Expected deep_link ending /management-reviews/{doc_id}, got {info.deep_link!r}"
    )


async def test_plain_document_resolves_to_generic_documents_surface(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """resolve_subject for a plain DOCUMENT (type SOP) links to /documents/{id}."""
    salt = uuid.uuid4().hex[:10]
    subject = f"kc-subtype-sop-{salt}"
    await s5.grant_lifecycle(subject)
    h = _auth(token_factory, subject)

    type_id = await s5.type_id("SOP")
    doc = await _create(app_client, h, type_id)
    doc_id = uuid.UUID(doc["id"])

    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", doc_id)

    assert info.deep_link.endswith(f"/documents/{doc_id}"), (
        f"Expected deep_link ending /documents/{doc_id}, got {info.deep_link!r}"
    )


async def test_resolve_subject_missing_doc_falls_back_gracefully(
    app_under_test: Any,
) -> None:
    """resolve_subject with a non-existent subject_id degrades gracefully (no crash)."""
    missing_id = uuid.uuid4()
    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", missing_id)

    # Falls back: identifier = str(id), deep_link = /documents/{id}
    assert info.identifier == str(missing_id)
    assert info.deep_link.endswith(f"/documents/{missing_id}")


async def test_obj_orphan_document_falls_back_to_generic_surface(
    app_under_test: Any,
) -> None:
    """R2-2: an OBJ-typed DocumentedInformation WITHOUT a QualityObjective satellite falls back
    to /documents/{id} (not /objectives/{id}) — the dedicated route 404s for an orphan.

    The generic POST /documents path does NOT block OBJ-typed creates, so this state can arise
    in practice.  resolve_subject probes the satellite before rerouting.
    """
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        framework_id = (
            await s.execute(select(Framework.id).where(Framework.code == "iso9001:2015").limit(1))
        ).scalar_one()
        obj_type_id = (
            await s.execute(select(DocumentType.id).where(DocumentType.code == "OBJ"))
        ).scalar_one()
        # Seed a minimal AppUser to own the orphan document.
        owner = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-orphan-obj-{salt}",
            display_name=f"Orphan Owner {salt}",
            status=UserStatus.ACTIVE,
        )
        s.add(owner)
        await s.flush()

        # Insert a DocumentedInformation with document_type OBJ but NO QualityObjective row.
        orphan = DocumentedInformation(
            org_id=org_id,
            framework_id=framework_id,
            kind=DocumentKind.DOCUMENT,
            identifier=f"ORP-{salt}",
            title=f"Orphan OBJ {salt}",
            document_type_id=obj_type_id,
            owner_user_id=owner.id,
            created_by=owner.id,
        )
        s.add(orphan)
        await s.commit()
        orphan_id = orphan.id

    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", orphan_id)

    # Must fall back to /documents/{id}, NOT /objectives/{id}
    assert info.deep_link.endswith(f"/documents/{orphan_id}"), (
        f"Expected fallback to /documents/{orphan_id}, got {info.deep_link!r}"
    )
    assert "/objectives/" not in info.deep_link, (
        f"Orphan OBJ must not route to /objectives/: {info.deep_link!r}"
    )
