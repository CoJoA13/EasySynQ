"""Audit C5 — the extension path's initial retention read tolerates an EXPIRED/ABSENT prior
lock (an aged shared object being recaptured) while the promotion verify and the final read-back
stay strict."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from easysynq_api.services.vault.staged_identity import WormNotApplied
from easysynq_api.services.vault.storage import _target_retention_sync

_FUTURE = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)
_PAST = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)


class _Events:
    def register(self, *_a: Any, **_k: Any) -> None:
        return None


class _Meta:
    events = _Events()


class _Client:
    meta = _Meta()

    def __init__(self, dates: list[datetime.datetime]) -> None:
        self._dates = dates
        self.puts: list[dict[str, Any]] = []

    def get_object_retention(self, **_kw: Any) -> dict[str, Any]:
        return {"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": self._dates.pop(0)}}

    def put_object_retention(self, **kw: Any) -> None:
        self.puts.append(kw)


def test_expired_prior_lock_is_replaced_not_fail_closed() -> None:
    client = _Client([_PAST, _FUTURE])  # initial read: lapsed; read-back: the fresh horizon
    out = _target_retention_sync(
        client,
        target_bucket="records",
        target_key="k",
        target_version_id="v",
        min_retain_until=_FUTURE,
    )
    assert out == _FUTURE
    assert len(client.puts) == 1
    assert client.puts[0]["Retention"]["RetainUntilDate"] == _FUTURE


def test_strict_promotion_verify_still_rejects_a_lapsed_lock() -> None:
    client = _Client([_PAST])
    with pytest.raises(WormNotApplied):
        _target_retention_sync(
            client, target_bucket="records", target_key="k", target_version_id="v"
        )
    assert client.puts == []


def test_final_read_back_stays_strict() -> None:
    # The put is silently ignored (read-back still lapsed) -> fail closed, never under-lock.
    client = _Client([_PAST, _PAST])
    with pytest.raises(WormNotApplied):
        _target_retention_sync(
            client,
            target_bucket="records",
            target_key="k",
            target_version_id="v",
            min_retain_until=_FUTURE,
        )
