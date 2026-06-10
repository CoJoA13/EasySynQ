"""S-drift-3 endpoint proofs — drift.read gates both admin GETs (deny-by-default; the seeded
System Administrator grant from 0047 admits; a grant-less user gets 403, never a 500/leak)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_drift_endpoints_deny_without_key_allow_with_role(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    salt = uuid.uuid4().hex[:10]
    admin, nobody = f"kc-driftadmin-{salt}", f"kc-driftnobody-{salt}"
    await s5.grant_role(admin, "System Administrator")  # holds drift.read via the 0047 seed

    for path in ("/api/v1/admin/drift/status", "/api/v1/admin/drift/superseded-copies"):
        r = await app_client.get(path, headers=_auth(token_factory, nobody))
        assert r.status_code == 403, f"{path}: expected deny-by-default, got {r.status_code}"

    r = await app_client.get("/api/v1/admin/drift/status", headers=_auth(token_factory, admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"scans", "blob_coverage", "superseded_copies"}
    assert set(body["scans"]) == {"MIRROR", "BLOB_REHASH"}

    r = await app_client.get(
        "/api/v1/admin/drift/superseded-copies", headers=_auth(token_factory, admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"total", "items"}
    assert set(body["total"]) == {"versions", "copies"}


async def test_superseded_copies_pagination_validation(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    salt = uuid.uuid4().hex[:10]
    admin = f"kc-driftadmin2-{salt}"
    await s5.grant_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?limit=0", headers=h)
    ).status_code == 422
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?limit=501", headers=h)
    ).status_code == 422
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?offset=-1", headers=h)
    ).status_code == 422
