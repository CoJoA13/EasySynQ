"""Audit-remediation proofs — unbounded numeric query/path params must 422 at the edge.

The contract already documents the bounds (evidence-packs ``limit`` 1..100, import-files ``limit``
1..200 / ``offset`` >= 0, audit keyset ints ``format: int64``); the runtime previously passed the
raw values through to SQL, where a negative LIMIT or an out-of-int64 keyset value produced a 500.
Each test sends an AUTHORIZED request carrying only the invalid parameter, so the 422 is
attributable to param validation, not the gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from .test_capa import _grant, _subject
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_evidence_packs_negative_limit_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    subject = _subject("bounds-packs")
    await _grant(subject, ("report.evidence_pack.generate",))
    h = _auth(token_factory, subject)
    r = await app_client.get("/api/v1/evidence-packs", headers=h, params={"limit": -1})
    assert r.status_code == 422, r.text
    ok = await app_client.get("/api/v1/evidence-packs", headers=h, params={"limit": 1})
    assert ok.status_code == 200, ok.text


async def test_import_files_negative_limit_and_offset_are_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    subject = _subject("bounds-imports")
    await _grant(subject, ("import.review",))
    h = _auth(token_factory, subject)
    url = f"/api/v1/admin/imports/{uuid.uuid4()}/files"
    r = await app_client.get(url, headers=h, params={"limit": -5})
    assert r.status_code == 422, r.text
    r = await app_client.get(url, headers=h, params={"offset": -1})
    assert r.status_code == 422, r.text


async def test_audit_out_of_int64_keyset_values_are_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """AuditEvent.id is a BigInteger; 2**63 overflows int64 and previously reached the driver as a
    500. The full int64 range stays accepted (the contract sets no minimum)."""
    subject = _subject("bounds-audit")
    await _grant(subject, ("system.audit_log.read",))
    h = _auth(token_factory, subject)
    over = 2**63
    r = await app_client.get("/api/v1/audit-events", headers=h, params={"cursor": over})
    assert r.status_code == 422, r.text
    r = await app_client.get(f"/api/v1/audit-events/{over}", headers=h)
    assert r.status_code == 422, r.text
    r = await app_client.get("/api/v1/audit-events/verify-chain", headers=h, params={"to": over})
    assert r.status_code == 422, r.text
    # A negative cursor stays contract-valid (int64, no documented minimum): 200 + empty page.
    ok = await app_client.get("/api/v1/audit-events", headers=h, params={"cursor": -5})
    assert ok.status_code == 200, ok.text
    assert ok.json()["events"] == []
    # The exact int64 boundaries stay accepted (the bound rejects only out-of-range values).
    for boundary in (-(2**63), 2**63 - 1):
        ok = await app_client.get("/api/v1/audit-events", headers=h, params={"cursor": boundary})
        assert ok.status_code == 200, ok.text
