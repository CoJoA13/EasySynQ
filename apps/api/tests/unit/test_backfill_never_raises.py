"""`backup bind-versions` must fail cleanly, never leave a traceback in an operator's terminal."""

from __future__ import annotations

import pytest

from easysynq_api.services.backup import version_backfill


def test_a_broken_object_store_client_is_a_clean_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client construction sits outside the catalog read, and the CLI has no handler of its own."""

    def _explode(_settings: object) -> object:
        raise RuntimeError("endpoint misconfigured")

    monkeypatch.setattr(
        version_backfill, "_unbound_rows", lambda _s: [("a" * 64, "documents", "k")]
    )
    monkeypatch.setattr(version_backfill, "_s3", _explode)

    out = version_backfill.backfill_version_bindings()

    assert out["result"] == "FAIL"
    assert "RuntimeError" in out["reason"]


def test_an_unreadable_catalog_is_a_clean_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(_settings: object) -> object:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(version_backfill, "_unbound_rows", _explode)

    out = version_backfill.backfill_version_bindings()

    assert out["result"] == "FAIL"
    assert "catalog unavailable" in out["reason"]
