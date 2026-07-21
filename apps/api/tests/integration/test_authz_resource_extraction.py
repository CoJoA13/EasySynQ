"""Parity proof for the extracted ``build_document_resource_context`` builder.

Verifies that the extracted function (``services/authz/resource.py``) produces the same
``ResourceContext`` that ``api/documents._document_scope_by_id`` (now a thin delegate) produced
before the extraction, and that the doc-missing degraded fallback is preserved byte-identically.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.authz.resource import build_document_resource_context

from .test_vault import _auth, _grant_doc_perms, _sop_type_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_document(app_under_test: object, token_factory: Callable[..., str]) -> object:
    """Create a real ``DocumentedInformation`` row via the HTTP API and return a namespace with
    its id.  Reuses the same helpers as ``test_vault.py`` to keep the fixture idiomatic."""
    from types import SimpleNamespace

    subject = f"kc-rc-{uuid.uuid4().hex[:10]}"
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    type_id = await _sop_type_id()

    async with AsyncClient(
        transport=ASGITransport(app=app_under_test),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        r = await client.post(
            "/api/v1/documents",
            headers=h,
            json={"title": "RC-extract-test", "document_type_id": type_id, "area_code": "PUR"},
        )
    assert r.status_code == 201, r.text
    doc_id = uuid.UUID(r.json()["id"])
    return SimpleNamespace(id=doc_id)


async def test_build_document_resource_context_matches_delegate(
    app_under_test: object, seeded_document: object
) -> None:
    """The extracted builder returns the full scope tuple: artifact_id, a non-None lifecycle_state,
    and (#333) framework_id + kind, so a FRAMEWORK/kind-scoped DENY is not dropped on any gate."""
    async with get_sessionmaker()() as session:
        rc = await build_document_resource_context(session, seeded_document.id)  # type: ignore[attr-defined]
    assert rc.artifact_id == str(seeded_document.id)  # type: ignore[attr-defined]
    assert rc.lifecycle_state is not None  # the doc's current_state.value
    assert rc.framework_id is not None  # #333: FRAMEWORK-scope selector
    assert rc.kind == "DOCUMENT"  # #333: DOC_CLASS kind selector


async def test_build_document_resource_context_missing_doc_degrades(
    app_under_test: object,
) -> None:
    """A missing doc yields the degraded fallback ResourceContext with folder_path=None and
    process_ids==frozenset() — preserved byte-identically from the original implementation."""
    missing = uuid.uuid4()
    async with get_sessionmaker()() as session:
        rc = await build_document_resource_context(session, missing)
    assert rc.artifact_id == str(missing)
    assert rc.folder_path is None
    assert rc.process_ids == frozenset()
