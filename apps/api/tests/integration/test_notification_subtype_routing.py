"""Integration test: resolve_subject routes OBJ/MR DOCUMENT notifications to dedicated surfaces.

Task 9 (S-notify-3a) — verifies the full DB path: resolve_subject loads DocumentedInformation,
reads its DocumentType.code, and passes it to deep_link_for.

Seeding strategy: create Draft documents via the API for each type (OBJ/MR/SOP).
resolve_subject only needs the row + its document_type_id to resolve the code — it doesn't
require the document to be Effective.  OBJ/MR/SOP document_type rows are guaranteed present
from migrations 0049/0050/0006.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

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
    """resolve_subject for a DOCUMENT whose type is OBJ links to /objectives/{id}."""
    salt = uuid.uuid4().hex[:10]
    subject = f"kc-subtype-obj-{salt}"
    await s5.grant_lifecycle(subject)
    h = _auth(token_factory, subject)

    type_id = await s5.type_id("OBJ")
    doc = await _create(app_client, h, type_id)
    doc_id = uuid.UUID(doc["id"])

    async with get_sessionmaker()() as s:
        info = await resolve_subject(s, "DOCUMENT", doc_id)

    assert info.deep_link.endswith(f"/objectives/{doc_id}"), (
        f"Expected deep_link ending /objectives/{doc_id}, got {info.deep_link!r}"
    )


async def test_mr_document_resolves_to_management_reviews_surface(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    """resolve_subject for a DOCUMENT whose type is MR links to /management-reviews/{id}."""
    salt = uuid.uuid4().hex[:10]
    subject = f"kc-subtype-mr-{salt}"
    await s5.grant_lifecycle(subject)
    h = _auth(token_factory, subject)

    type_id = await s5.type_id("MR")
    doc = await _create(app_client, h, type_id)
    doc_id = uuid.UUID(doc["id"])

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
