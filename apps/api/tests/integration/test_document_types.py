"""S-web-2 integration proofs — GET /document-types (the friendly Type column / facet source).

Authentication-only (JIT-provisions the caller, like GET /documents); org-scoped; the seeded catalog
(0006_seed_vault) includes SOP. Row shape: {id, code, name, document_level, is_singleton}.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_list_document_types_shape_and_seeded(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A bare token JIT-provisions the caller into the org; no grant needed (authenticated-only).
    h = _auth(token_factory, f"kc-dt-{uuid.uuid4().hex[:10]}")
    r = await app_client.get("/api/v1/document-types", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list) and rows  # the seed creates at least the default types
    codes = {row["code"] for row in rows}
    assert "SOP" in codes  # 0006_seed_vault default
    for row in rows:
        assert set(row) == {"id", "code", "name", "document_level", "is_singleton"}
        assert isinstance(row["name"], str)


async def test_document_types_requires_auth(app_client: AsyncClient) -> None:
    r = await app_client.get("/api/v1/document-types")
    assert r.status_code == 401, r.text
