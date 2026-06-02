"""S8c unit proofs — the OIDC-discovery reachability probe + the new auth event_type values (no DB).

The DB-bound G-D gate flow (configure-auth → persisted attestation → finalize gating) is proven in
``tests/integration/test_setup.py``; here we pin the pure probe parsing (a misconfigured issuer must
be an honest FAIL, never a false-PASS) + the enum guard (a missing Python EventType member is a
runtime crash, not a CI failure — see 0011-0015).
"""

from __future__ import annotations

from typing import Any

import pytest

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.services.setup import auth_check

_ISSUER = "https://kc.test/realms/easysynq"
_GOOD_DOC = {"issuer": _ISSUER, "jwks_uri": f"{_ISSUER}/protocol/openid-connect/certs"}


class _FakeResp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    def __init__(self, *, resp: _FakeResp | None = None, exc: Exception | None = None) -> None:
        self._resp = resp
        self._exc = exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, _url: str) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(auth_check.httpx, "AsyncClient", lambda **_: client)


async def test_probe_verifies_well_formed_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(200, _GOOD_DOC)))
    ok, _ = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is True


async def test_probe_fails_on_issuer_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A discovery doc whose issuer differs would break token validation → must FAIL (lock-out)."""
    bad = {**_GOOD_DOC, "issuer": "https://evil"}
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(200, bad)))
    ok, detail = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False
    assert "issuer" in detail


async def test_probe_fails_on_missing_jwks_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(200, {"issuer": _ISSUER})))
    ok, _ = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False


async def test_probe_fails_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(404, {})))
    ok, _ = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False


async def test_probe_fails_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable issuer is a clean (False, detail), never a 500/hang (worm_probe rule)."""
    _patch_client(monkeypatch, _FakeClient(exc=auth_check.httpx.ConnectError("refused")))
    ok, detail = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False
    assert "not reachable" in detail


async def test_probe_fails_on_non_dict_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed IdP returning a JSON array/string (not an object) is a clean FAIL, never a 500
    AttributeError (the 'never raises' contract)."""
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(200, ["not", "a", "dict"])))
    ok, _ = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False


async def test_probe_fails_on_non_string_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-string ``issuer`` field must not raise on ``.rstrip`` — a clean FAIL."""
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(200, {"issuer": 123, "jwks_uri": "x"})))
    ok, _ = await auth_check.probe_oidc_discovery(_ISSUER)
    assert ok is False


async def test_probe_fails_on_empty_issuer() -> None:
    ok, _ = await auth_check.probe_oidc_discovery("")
    assert ok is False


def test_new_event_types_present() -> None:
    """0015's three ALTER TYPE ADD VALUEs must also be Python EventType members, or a from-scratch
    ``upgrade head`` (which rebuilds the type from EVENT_TYPE_VALUES) drops them → inserts crash."""
    for name in ("AUTH_CONFIGURED", "AUTH_TEST_LOGIN_OK", "AUTH_TEST_LOGIN_FAILED"):
        assert EventType(name).value == name
        assert name in EVENT_TYPE_VALUES
