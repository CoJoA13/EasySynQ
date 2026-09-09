"""Unit tests for the signed-checkpoint verification (Batch 7, doc 12 §4.4) — the detection control
that exposes a privileged DB-owner chain rewrite the self-consistent chain walk alone cannot see.

The threat: an owner rewrites the audit payloads AND recomputes prev_hash/row_hash so the chain is
internally consistent. Only the Ed25519 signature on the latest checkpoint — which the attacker
cannot forge and cannot re-sign over the rewritten latest_row_hash — surfaces the tamper."""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from easysynq_api.services.audit import checkpoint as cp
from easysynq_api.services.audit import sink as sink_service

_ORG = "11111111-1111-1111-1111-111111111111"
_HASH = b"\xaa" * 32
_TS = datetime.datetime(2026, 7, 24, 12, 0, tzinfo=datetime.UTC)


def _patch_settings(monkeypatch: Any, *, public: str, private: str) -> None:
    monkeypatch.setattr(
        cp,
        "get_settings",
        lambda: SimpleNamespace(
            audit_checkpoint_public_key_path=public,
            audit_checkpoint_signing_key_path=private,
        ),
    )


def test_verify_checkpoint_signature_roundtrip_and_tamper() -> None:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    sig = key.sign(cp._payload(_ORG, 42, _HASH, _TS))

    def _verify(**over: Any) -> bool:
        args: dict[str, Any] = {
            "org_id": _ORG,
            "latest_id": 42,
            "latest_row_hash": _HASH,
            "timestamp": _TS,
            "signature": sig,
        }
        args.update(over)
        return cp.verify_checkpoint_signature(pub, **args)

    assert _verify() is True
    # The DB-owner attack: a rewritten latest_row_hash the attacker cannot re-sign.
    assert _verify(latest_row_hash=b"\xbb" * 32) is False
    # A rewritten latest_id / timestamp likewise breaks the signed payload.
    assert _verify(latest_id=99) is False
    assert _verify(timestamp=_TS + datetime.timedelta(seconds=1)) is False
    # A null signature is never attested (fail-closed).
    assert _verify(signature=None) is False
    # A forgery signed by a DIFFERENT key fails against the trusted key.
    other = Ed25519PrivateKey.generate().sign(cp._payload(_ORG, 42, _HASH, _TS))
    assert _verify(signature=other) is False


def test_load_verify_key_uses_public_when_no_private(tmp_path: Any, monkeypatch: Any) -> None:
    key = Ed25519PrivateKey.generate()
    pub_path = tmp_path / "pub.pem"
    pub_path.write_bytes(
        key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    # Private path ABSENT → the api/CLI/off-host case: verify with the public key alone (no secret).
    _patch_settings(monkeypatch, public=str(pub_path), private=str(tmp_path / "absent.pem"))
    loaded = cp.load_verify_key()
    assert loaded is not None
    sig = key.sign(cp._payload(_ORG, 1, _HASH, _TS))
    assert (
        cp.verify_checkpoint_signature(
            loaded, org_id=_ORG, latest_id=1, latest_row_hash=_HASH, timestamp=_TS, signature=sig
        )
        is True
    )


def test_load_verify_key_prefers_private_over_stale_public(tmp_path: Any, monkeypatch: Any) -> None:
    # The beat holds the private key AND a STALE public key (a prior key's public) is at the export
    # path. Deriving from the private key must WIN, so the beat verifies the ACTUAL signer — else a
    # stale exported public key would false-alarm CHAIN_VERIFY_FAIL every night.
    signer = Ed25519PrivateKey.generate()
    stale = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(signer.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    pub_path = tmp_path / "stale_pub.pem"
    pub_path.write_bytes(
        stale.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    _patch_settings(monkeypatch, public=str(pub_path), private=str(priv_path))
    loaded = cp.load_verify_key()
    assert loaded is not None
    sig = signer.sign(cp._payload(_ORG, 1, _HASH, _TS))  # signed by the ACTUAL signer
    assert (
        cp.verify_checkpoint_signature(
            loaded, org_id=_ORG, latest_id=1, latest_row_hash=_HASH, timestamp=_TS, signature=sig
        )
        is True
    )


def test_load_verify_key_derives_from_private_when_no_public(
    tmp_path: Any, monkeypatch: Any
) -> None:
    key = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    # No public key exported → the beat case: derive the verify key from the private signing key.
    _patch_settings(monkeypatch, public=str(tmp_path / "absent_pub.pem"), private=str(priv_path))
    assert cp.load_verify_key() is not None


def test_load_verify_key_none_when_neither_available(tmp_path: Any, monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, public=str(tmp_path / "np.pem"), private=str(tmp_path / "nk.pem"))
    assert cp.load_verify_key() is None  # an api/CLI process with no key → walk-only, cannot attest


def test_load_verify_key_fails_closed_on_malformed_public(tmp_path: Any, monkeypatch: Any) -> None:
    # A truncated / invalid exported PEM must FAIL CLOSED to None (degrade the verify to a walk),
    # never raise — a raise would abort the nightly detection task and 500 the API verify endpoint.
    pub_path = tmp_path / "pub.pem"
    pub_path.write_bytes(b"-----BEGIN PUBLIC KEY-----\nnot-a-valid-pem\n-----END PUBLIC KEY-----\n")
    _patch_settings(monkeypatch, public=str(pub_path), private=str(tmp_path / "absent.pem"))
    assert cp.load_verify_key() is None


def test_load_verify_key_fails_closed_on_malformed_private(tmp_path: Any, monkeypatch: Any) -> None:
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(b"-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----\n")
    _patch_settings(monkeypatch, public=str(tmp_path / "absent_pub.pem"), private=str(priv_path))
    assert cp.load_verify_key() is None


def test_unanchored_is_overdue_boundary() -> None:
    """The grace window for a sink that is enabled but has NEVER anchored (Batch 11). Pure, so the
    boundary is pinned without a database or a live object store. Inside the window a fresh sink is
    benign; past it, a witness that was configured and never once produced is an operator failure.
    Before ``enabled_at`` existed that state was benign FOREVER, so a permanently dead witness was
    indistinguishable from one added five minutes ago."""
    import datetime

    from easysynq_api.services.audit.checkpoint import unanchored_is_overdue

    now = datetime.datetime(2026, 7, 26, 12, 0, tzinfo=datetime.UTC)
    grace = datetime.timedelta(hours=24)

    # Just enabled → benign (it may not have hit its first 15-minute anchor yet).
    assert unanchored_is_overdue(now, now, grace) is False
    # One second inside the window → still benign.
    assert unanchored_is_overdue(now - grace + datetime.timedelta(seconds=1), now, grace) is False
    # EXACTLY at the boundary → still benign (strictly greater), so a 24h grace alarms from 24h+1s.
    assert unanchored_is_overdue(now - grace, now, grace) is False
    # One second past → a dead witness.
    assert unanchored_is_overdue(now - grace - datetime.timedelta(seconds=1), now, grace) is True
    # A naive timestamp is read as UTC, not raised on: this function decides whether to raise an
    # alarm, so it must never be the reason the nightly verify crashes.
    assert unanchored_is_overdue((now - grace * 2).replace(tzinfo=None), now, grace) is True
    # A zero grace makes any nonzero age overdue (AUDIT_WITNESS_GRACE_HOURS=0 is a valid choice).
    assert unanchored_is_overdue(now, now, datetime.timedelta(0)) is False
    assert (
        unanchored_is_overdue(now - datetime.timedelta(seconds=1), now, datetime.timedelta(0))
        is True
    )


def test_should_alarm_offhost_decision_table() -> None:
    """The nightly beat's off-host alarm decision. A wipe leaves a readable off-host object that
    FAILS attestation (attest_failures>0) → ALARM; a read failure (unreachable witness) → ALARM;
    but a not-yet-anchored empty sink — even alongside a healthy one (sinks_read>0) — stays quiet,
    since the decision is keyed on real attestation failures, not the global read count. Pins both
    the wipe alarm (the fail-open diff-critic caught) and the multi-sink false-positive fix."""
    from easysynq_api.services.audit.checkpoint import OffHostCheckpointResult as R
    from easysynq_api.tasks.audit import _should_alarm_offhost

    # No off-host witness configured → defer to the R13 soft-gate, never a nightly alarm.
    assert _should_alarm_offhost(R(False, 0, False, ["unavailable"])) is False
    # Configured + attested → healthy.
    assert _should_alarm_offhost(R(True, 1, True, [])) is False
    # Configured but nothing anchored yet (fresh org) → quiet, defers to the soft-gate.
    assert _should_alarm_offhost(R(True, 0, False, ["no object found"])) is False
    # A healthy sink PLUS a freshly-added empty sibling (read=1 for the healthy one, a "no object"
    # reason for the new one, NO attestation failure) must NOT alarm — keyed on attest_failures,
    # not the global sinks_read, so a second witness added before its first anchor stays quiet.
    assert _should_alarm_offhost(R(True, 1, False, ["sink B: no object found"])) is False
    # THE WIPE: an object was read back and REJECTED (references a now-missing chain row) → ALARM.
    assert _should_alarm_offhost(R(True, 1, False, ["deletion"], attest_failures=1)) is True
    # A read failure (unreachable witness) → fail-closed ALARM even though nothing was read back.
    assert _should_alarm_offhost(R(True, 0, False, ["read failed"], read_failed=True)) is True

    # --- Batch 11 additions -------------------------------------------------------------------
    # A sink enabled PAST the grace window that has never once anchored is a configured-but-dead
    # witness, not a fresh one → ALARM. (Before enabled_at existed this state was benign forever.)
    assert (
        _should_alarm_offhost(R(True, 0, False, ["never anchored"], unanchored_overdue=1)) is True
    )
    # AUDIT_WITNESS_REQUIRED declared out-of-band + NO off-host sink in the DB → ALARM. This is the
    # case a privileged DB owner creates by DELETING the sink row: without the out-of-DB
    # declaration the verify goes quiet exactly when it should shout.
    assert _should_alarm_offhost(R(False, 0, False, ["unavailable"]), witness_required=True) is True
    # ...but the declaration must not turn a HEALTHY witness into an alarm.
    assert _should_alarm_offhost(R(True, 1, True, []), witness_required=True) is False
    # ...and with the declaration OFF the missing-witness case stays quiet (the default above,
    # re-asserted explicitly so a future default flip is caught here).
    assert (
        _should_alarm_offhost(R(False, 0, False, ["unavailable"]), witness_required=False) is False
    )


def test_load_signing_key_exports_the_public_half(tmp_path: Any, monkeypatch: Any) -> None:
    priv_path = tmp_path / "priv.pem"
    pub_path = tmp_path / "pub.pem"
    _patch_settings(monkeypatch, public=str(pub_path), private=str(priv_path))
    key = cp.load_signing_key()
    assert priv_path.exists() and pub_path.exists()  # both persisted on first use
    # The exported public key attests a signature made by the signing key.
    loaded_pub = cp.load_verify_key()
    assert loaded_pub is not None
    sig = key.sign(cp._payload(_ORG, 7, _HASH, _TS))
    assert (
        cp.verify_checkpoint_signature(
            loaded_pub,
            org_id=_ORG,
            latest_id=7,
            latest_row_hash=_HASH,
            timestamp=_TS,
            signature=sig,
        )
        is True
    )


def _signed_legacy_doc(
    key: Ed25519PrivateKey,
    org_id: str,
    latest_id: int,
    row_hash: bytes,
    timestamp: datetime.datetime,
) -> dict[str, Any]:
    payload = cp._payload(org_id, latest_id, row_hash, timestamp)
    return {
        "checkpoint": json.loads(payload),
        "signature": base64.b64encode(key.sign(payload)).decode(),
    }


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _SinkScalars:
    def __init__(self, sinks: list[Any]) -> None:
        self.sinks = sinks

    def all(self) -> list[Any]:
        return self.sinks


class _SinkResult:
    def __init__(self, sinks: list[Any]) -> None:
        self.sinks = sinks

    def scalars(self) -> _SinkScalars:
        return _SinkScalars(self.sinks)


class _HistorySession:
    def __init__(self, sink: Any | list[Any], stored: dict[int, bytes]) -> None:
        self.sinks = sink if isinstance(sink, list) else [sink]
        self.stored = stored
        self.calls = 0

    async def execute(self, statement: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return _SinkResult(self.sinks)
        values = statement.compile().params.values()
        latest_id = next(value for value in values if type(value) is int)
        return _ScalarResult(self.stored.get(latest_id))


async def _run_history(
    monkeypatch: pytest.MonkeyPatch,
    documents: list[dict[str, Any]],
    *,
    stored: dict[int, bytes],
    now: datetime.datetime = _TS,
    pages: list[sink_service.CheckpointVersionsPage] | None = None,
    last_anchored_at: datetime.datetime | None = None,
    enabled_at: datetime.datetime | None = None,
    read_failure_at: int | None = None,
    kind: str = "worm_bucket",
) -> cp.OffHostCheckpointResult:
    refs = [
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/{index + 1}-a.json", f"v{index}")
        for index in range(len(documents))
    ]
    supplied_pages = pages or [
        sink_service.CheckpointVersionsPage(tuple(refs), (), False, None, None)
    ]
    page_index = 0
    read_index = 0

    def list_page(
        supplied_kind: str, *_args: Any, **_kwargs: Any
    ) -> sink_service.CheckpointVersionsPage:
        nonlocal page_index
        if supplied_kind != "worm_bucket":
            raise sink_service.SinkReadError(
                f"off-host version listing is not implemented for sink kind '{supplied_kind}'"
            )
        page = supplied_pages[page_index]
        page_index += 1
        return page

    def read_version(
        _kind: str,
        _connection: dict[str, Any] | None,
        _ref: sink_service.CheckpointVersionRef,
    ) -> dict[str, Any]:
        nonlocal read_index
        if read_failure_at == read_index:
            raise sink_service.SinkReadError("synthetic explicit-version read failure")
        document = documents[read_index]
        read_index += 1
        return document

    monkeypatch.setattr(cp, "list_offhost_checkpoint_versions_page", list_page)
    monkeypatch.setattr(cp, "read_offhost_checkpoint_version", read_version)
    monkeypatch.setattr(
        cp,
        "get_settings",
        lambda: SimpleNamespace(audit_witness_grace_hours=24),
    )
    configured = SimpleNamespace(
        id="synthetic-sink",
        kind=SimpleNamespace(value=kind),
        connection={"off_host": True, "bucket": "synthetic"},
        last_anchored_at=last_anchored_at,
        enabled_at=enabled_at or now,
    )
    session = _HistorySession(configured, stored)
    return await cp.verify_offhost_checkpoint(
        session, _ORG, verify_key=_TEST_KEY.public_key(), now=now
    )


_TEST_KEY = Ed25519PrivateKey.generate()


async def test_history_accepts_old_consistent_and_fresh_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS - datetime.timedelta(days=10))
    fresh = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    result = await _run_history(monkeypatch, [fresh, old], stored={7: _HASH})
    assert result.verified
    assert result.sinks_read == 1
    assert result.attest_failures == 0


async def test_fresh_checkpoint_cannot_mask_bad_historical_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS - datetime.timedelta(days=10))
    old["signature"] = base64.b64encode(b"x" * 64).decode()
    fresh = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    result = await _run_history(monkeypatch, [old, fresh], stored={7: _HASH})
    assert not result.verified
    assert result.attest_failures == 1
    assert any("signature" in reason for reason in result.reasons)


async def test_signed_timestamp_selects_heartbeat_not_filename_or_listing_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _signed_legacy_doc(_TEST_KEY, _ORG, 9, _HASH, _TS - datetime.timedelta(days=10))
    fresh = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    refs = (
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/999999-future.json", "old"),
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/1-past.json", "fresh"),
    )
    pages = [sink_service.CheckpointVersionsPage(refs, (), False, None, None)]
    result = await _run_history(monkeypatch, [old, fresh], stored={7: _HASH, 9: _HASH}, pages=pages)
    assert result.verified


async def test_latest_authenticated_heartbeat_is_checked_for_staleness_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS - datetime.timedelta(seconds=2701))
    result = await _run_history(monkeypatch, [stale], stored={7: _HASH})
    assert not result.verified
    assert result.attest_failures == 1
    assert sum("stale" in reason for reason in result.reasons) == 1


async def _run_policy_scan(
    monkeypatch: pytest.MonkeyPatch,
    documents: list[dict[str, Any]],
    *,
    target_id: int | None,
    target_known: bool,
    mismatches: dict[int, str] | None = None,
    freshness_policy: cp.HistoryFreshnessPolicy = "historical-target",
) -> tuple[cp.WitnessHistoryResult, list[int]]:
    refs = tuple(
        sink_service.CheckpointVersionRef(
            f"checkpoints/{_ORG}/{index + 1}-history.json", f"v{index}"
        )
        for index in range(len(documents))
    )
    by_version = {ref.version_id: document for ref, document in zip(refs, documents, strict=True)}
    monkeypatch.setattr(
        cp,
        "list_offhost_checkpoint_versions_page",
        lambda *_args, **_kwargs: sink_service.CheckpointVersionsPage(refs, (), False, None, None),
    )
    monkeypatch.setattr(
        cp,
        "read_offhost_checkpoint_version",
        lambda _kind, _connection, ref: by_version[ref.version_id],
    )
    compared: list[int] = []

    def verify_signature(**fields: Any) -> bool:
        return cp.verify_checkpoint_signature(_TEST_KEY.public_key(), **fields)

    async def compare(checkpoint: cp.AuthenticatedCheckpoint) -> str | None:
        compared.append(checkpoint.latest_id)
        return (mismatches or {}).get(checkpoint.latest_id)

    result = await cp.scan_offhost_history(
        _ORG,
        kind="worm_bucket",
        connection={"bucket": "synthetic"},
        reader=None,
        verify_signature=verify_signature,
        compare_checkpoint=compare,
        now=_TS,
        freshness_policy=freshness_policy,
        historical_target_id=target_id,
        historical_target_known=target_known,
    )
    return result, compared


async def test_historical_policy_accepts_signed_stale_evidence_and_classifies_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_at = _TS - datetime.timedelta(seconds=2701)
    applicable = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, stale_at)
    ahead = _signed_legacy_doc(_TEST_KEY, _ORG, 9, b"\xbb" * 32, stale_at)

    live, live_compared = await _run_policy_scan(
        monkeypatch,
        [applicable, ahead],
        target_id=None,
        target_known=False,
        freshness_policy="live",
    )
    historical, historical_compared = await _run_policy_scan(
        monkeypatch,
        [applicable, ahead],
        target_id=7,
        target_known=True,
    )

    assert live.attestation_failed is True
    assert any("stale" in reason for reason in live.reasons)
    assert live_compared == [7, 9]
    assert historical.attestation_failed is False
    assert historical.reasons == []
    assert historical_compared == [7]
    assert historical.historical_applicable_checkpoints == 1
    assert historical.historical_ahead_checkpoints == 1
    assert historical.historical_highest_ahead_id == 9
    assert historical.historical_covered_through_id == 7


async def test_historical_policy_does_not_let_ahead_evidence_mask_applicable_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applicable = _signed_legacy_doc(_TEST_KEY, _ORG, 5, _HASH, _TS)
    ahead = _signed_legacy_doc(_TEST_KEY, _ORG, 9, b"\xbb" * 32, _TS)

    result, compared = await _run_policy_scan(
        monkeypatch,
        [applicable, ahead],
        target_id=7,
        target_known=True,
        mismatches={5: "synthetic historical contradiction"},
    )

    assert compared == [5]
    assert result.attestation_failed is True
    assert result.historical_applicable_checkpoints == 1
    assert result.historical_ahead_checkpoints == 1
    assert result.historical_covered_through_id is None
    assert "synthetic historical contradiction" in result.reasons


async def test_historical_policy_authenticates_ahead_evidence_and_nulls_unknown_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forged_ahead = _signed_legacy_doc(_TEST_KEY, _ORG, 9, _HASH, _TS)
    forged_ahead["signature"] = base64.b64encode(b"x" * 64).decode()

    forged, compared = await _run_policy_scan(
        monkeypatch,
        [forged_ahead],
        target_id=7,
        target_known=True,
    )
    unknown, unknown_compared = await _run_policy_scan(
        monkeypatch,
        [_signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)],
        target_id=None,
        target_known=False,
    )

    assert forged.attestation_failed is True
    assert compared == []
    assert forged.historical_ahead_checkpoints == 0
    assert forged.historical_covered_through_id is None
    assert unknown.comparison_unavailable is True
    assert unknown_compared == []
    assert unknown.historical_applicable_checkpoints is None
    assert unknown.historical_ahead_checkpoints is None
    assert unknown.historical_highest_ahead_id is None
    assert unknown.historical_covered_through_id is None


async def test_existing_future_timestamp_behavior_remains_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS + datetime.timedelta(days=1))
    result = await _run_history(monkeypatch, [future], stored={7: _HASH})
    assert result.verified
    assert result.attest_failures == 0


@pytest.mark.parametrize("failure", ["org", "hash", "missing"])
async def test_every_authenticated_history_head_must_match_the_chain(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    org_id = "22222222-2222-2222-2222-222222222222" if failure == "org" else _ORG
    row_hash = b"\xbb" * 32 if failure == "hash" else _HASH
    document = _signed_legacy_doc(_TEST_KEY, org_id, 7, row_hash, _TS)
    stored = {} if failure == "missing" else {7: _HASH}
    result = await _run_history(monkeypatch, [document], stored=stored)
    assert not result.verified
    assert result.attest_failures == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["checkpoint"].__setitem__("latest_id", True),
        lambda doc: doc["checkpoint"].__setitem__("latest_id", 0),
        lambda doc: doc["checkpoint"].__setitem__("latest_id", 1.5),
        lambda doc: doc["checkpoint"].__setitem__("latest_id", 10**100),
        lambda doc: doc["checkpoint"].__setitem__("latest_row_hash", "aa"),
        lambda doc: doc["checkpoint"].__setitem__("timestamp", 1),
        lambda doc: doc.__setitem__("signature", "not-base64!"),
        lambda doc: doc.pop("signature"),
        lambda doc: doc.__setitem__("format_version", 2),
    ],
)
def test_authentication_rejects_strict_legacy_shape_and_value_boundaries(
    mutate: Any,
) -> None:
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    mutate(document)
    authenticated, reason = cp._authenticate_offhost_doc(_ORG, _TEST_KEY.public_key(), document)
    assert authenticated is None
    assert reason is not None


def test_authentication_handles_timestamp_normalization_overflow_as_malformed() -> None:
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    document["checkpoint"]["timestamp"] = "0001-01-01T00:00:00+23:59"
    authenticated, reason = cp._authenticate_offhost_doc(_ORG, _TEST_KEY.public_key(), document)
    assert authenticated is None
    assert "malformed" in str(reason)


def test_authentication_preserves_legacy_offset_normalization() -> None:
    offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    timestamp = _TS.astimezone(offset)
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    document["checkpoint"]["timestamp"] = timestamp.isoformat()
    authenticated, reason = cp._authenticate_offhost_doc(_ORG, _TEST_KEY.public_key(), document)
    assert reason is None
    assert authenticated is not None
    assert authenticated.timestamp == _TS


def test_callback_authentication_invokes_the_enrolled_verifier_once() -> None:
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    calls: list[dict[str, Any]] = []

    def verifier(**fields: Any) -> bool:
        calls.append(fields)
        return True

    authenticated, reason = cp._authenticate_offhost_doc_with_verifier(_ORG, verifier, document)

    assert reason is None
    assert authenticated == cp.AuthenticatedCheckpoint(7, _HASH, _TS)
    assert calls == [
        {
            "org_id": _ORG,
            "latest_id": 7,
            "latest_row_hash": _HASH,
            "timestamp": _TS,
            "signature": base64.b64decode(document["signature"]),
        }
    ]


def test_callback_authentication_rejects_without_retry_or_key_discovery() -> None:
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    calls = 0

    def verifier(**_fields: Any) -> bool:
        nonlocal calls
        calls += 1
        return False

    authenticated, reason = cp._authenticate_offhost_doc_with_verifier(_ORG, verifier, document)

    assert authenticated is None
    assert reason == "off-host checkpoint signature invalid (forged/corrupt)"
    assert calls == 1


async def test_equal_authenticated_heartbeat_tuples_are_all_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    second = _signed_legacy_doc(_TEST_KEY, _ORG, 7, b"\xbb" * 32, _TS)
    result = await _run_history(monkeypatch, [first, second], stored={7: _HASH})
    assert not result.verified
    assert result.sinks_read == 1
    assert result.attest_failures == 1
    assert any("mismatch" in reason for reason in result.reasons)


@pytest.mark.parametrize("failed_first", [False, True], ids=["healthy-first", "failed-first"])
@pytest.mark.parametrize("failure", ["attestation", "read"])
async def test_multi_sink_failures_remain_sticky_across_sink_order(
    monkeypatch: pytest.MonkeyPatch,
    failed_first: bool,
    failure: str,
) -> None:
    healthy_ref = sink_service.CheckpointVersionRef(
        f"checkpoints/{_ORG}/7-healthy.json", "healthy-version"
    )
    failed_ref = sink_service.CheckpointVersionRef(
        f"checkpoints/{_ORG}/7-failed.json", "failed-version"
    )
    marker = sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/deleted", "delete-marker")
    healthy_doc = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)

    def list_page(
        _kind: str,
        connection: dict[str, Any],
        _org_id: str,
        **_markers: Any,
    ) -> sink_service.CheckpointVersionsPage:
        if connection["name"] == "failed":
            if failure == "read":
                raise sink_service.SinkReadError("synthetic sibling listing failure")
            return sink_service.CheckpointVersionsPage((failed_ref,), (marker,), False, None, None)
        return sink_service.CheckpointVersionsPage((healthy_ref,), (), False, None, None)

    def read_version(
        _kind: str,
        _connection: dict[str, Any],
        _ref: sink_service.CheckpointVersionRef,
    ) -> dict[str, Any]:
        return healthy_doc

    monkeypatch.setattr(cp, "list_offhost_checkpoint_versions_page", list_page)
    monkeypatch.setattr(cp, "read_offhost_checkpoint_version", read_version)
    monkeypatch.setattr(cp, "get_settings", lambda: SimpleNamespace(audit_witness_grace_hours=24))
    sinks = [
        SimpleNamespace(
            id=name,
            kind=SimpleNamespace(value="worm_bucket"),
            connection={"off_host": True, "name": name},
            last_anchored_at=_TS,
            enabled_at=_TS,
        )
        for name in (["failed", "healthy"] if failed_first else ["healthy", "failed"])
    ]

    result = await cp.verify_offhost_checkpoint(
        _HistorySession(sinks, {7: _HASH}),
        _ORG,
        verify_key=_TEST_KEY.public_key(),
        now=_TS,
    )

    assert result.verified is False
    if failure == "attestation":
        assert result.sinks_read == 2
        assert result.attest_failures == 1
        assert result.read_failed is False
        assert any("delete marker" in reason for reason in result.reasons)
    else:
        assert result.sinks_read == 1
        assert result.attest_failures == 0
        assert result.read_failed is True
        assert any("listing failure" in reason for reason in result.reasons)


async def test_marker_failure_does_not_skip_valid_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    ref = sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/7-a.json", "v")
    marker = sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/removed", "m")
    pages = [sink_service.CheckpointVersionsPage((ref,), (marker,), False, None, None)]
    result = await _run_history(monkeypatch, [fresh], stored={7: _HASH}, pages=pages)
    assert result.sinks_read == 1
    assert result.attest_failures == 1
    assert any("delete marker" in reason for reason in result.reasons)


async def test_marker_only_prefix_is_an_attestation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/unrelated", "m")
    pages = [sink_service.CheckpointVersionsPage((), (marker,), False, None, None)]
    result = await _run_history(monkeypatch, [], stored={}, pages=pages)
    assert result.sinks_read == 0
    assert result.attest_failures == 1
    assert not result.verified


async def test_partial_read_failure_after_good_body_never_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    result = await _run_history(
        monkeypatch,
        [fresh, fresh],
        stored={7: _HASH},
        read_failure_at=1,
    )
    assert result.sinks_read == 1
    assert result.read_failed
    assert not result.verified


async def test_unknown_sink_kind_is_a_public_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_history(
        monkeypatch,
        [],
        stored={},
        kind="external_object_store",
    )
    assert result.read_failed
    assert not result.verified
    assert any("not implemented" in reason for reason in result.reasons)


async def test_ineligible_versions_count_toward_entry_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cp, "_HISTORY_MAX_ENTRIES", 2)
    refs = (
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/notes", "v1"),
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/other", "v2"),
    )
    pages = [sink_service.CheckpointVersionsPage(refs, (), False, None, None)]
    result = await _run_history(monkeypatch, [], stored={}, pages=pages)
    assert result.read_failed and not result.verified
    assert any("entry limit" in reason for reason in result.reasons)


async def test_cap_minus_one_entries_can_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "_HISTORY_MAX_ENTRIES", 2)
    ref = sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/notes", "v1")
    pages = [sink_service.CheckpointVersionsPage((ref,), (), False, None, None)]
    result = await _run_history(monkeypatch, [], stored={}, pages=pages)
    assert not result.read_failed
    assert any("no off-host checkpoint" in reason for reason in result.reasons)


async def test_exact_page_cap_and_repeated_cursor_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cp, "_HISTORY_MAX_PAGES", 2)
    cursor = ("opaque-key", "null")
    pages = [
        sink_service.CheckpointVersionsPage((), (), True, *cursor),
        sink_service.CheckpointVersionsPage((), (), False, None, None),
    ]
    capped = await _run_history(monkeypatch, [], stored={}, pages=pages)
    assert capped.read_failed
    assert any("page limit" in reason for reason in capped.reasons)

    monkeypatch.setattr(cp, "_HISTORY_MAX_PAGES", 10)
    repeated_pages = [
        sink_service.CheckpointVersionsPage((), (), True, *cursor),
        sink_service.CheckpointVersionsPage((), (), True, *cursor),
    ]
    repeated = await _run_history(monkeypatch, [], stored={}, pages=repeated_pages)
    assert repeated.read_failed
    assert any("cursor repeated" in reason for reason in repeated.reasons)


async def test_deadline_after_page_never_accepts_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0.0, 0.0, 301.0])
    monkeypatch.setattr(cp, "monotonic", lambda: next(times))
    result = await _run_history(monkeypatch, [], stored={})
    assert result.read_failed and not result.verified
    assert any("deadline" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "expiry_call",
    [1, 2, 3, 4, 5, 6, 7],
    ids=[
        "before-page",
        "after-page",
        "before-body",
        "after-body",
        "before-db-compare",
        "after-db-compare",
        "after-page-work",
    ],
)
async def test_deadline_boundaries_never_accept_a_partial_scan(
    monkeypatch: pytest.MonkeyPatch, expiry_call: int
) -> None:
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        value = 301.0 if calls == expiry_call else 0.0
        calls += 1
        return value

    monkeypatch.setattr(cp, "monotonic", monotonic)
    document = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    result = await _run_history(monkeypatch, [document], stored={7: _HASH})
    assert result.read_failed
    assert not result.verified
    assert any("deadline" in reason for reason in result.reasons)


async def test_reason_details_are_bounded_per_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = [{"checkpoint": {}, "signature": "invalid", "extra": index} for index in range(22)]
    result = await _run_history(monkeypatch, documents, stored={})
    assert result.attest_failures == 1
    assert len(result.reasons) == 21
    assert "2 additional failure reasons omitted" in result.reasons[-1]


async def test_external_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def cancelled(*_args: Any, **_kwargs: Any) -> sink_service.CheckpointVersionsPage:
        raise asyncio.CancelledError

    monkeypatch.setattr(cp, "list_offhost_checkpoint_versions_page", cancelled)
    monkeypatch.setattr(cp, "get_settings", lambda: SimpleNamespace(audit_witness_grace_hours=24))
    configured = SimpleNamespace(
        id="synthetic-sink",
        kind=SimpleNamespace(value="worm_bucket"),
        connection={"off_host": True},
        last_anchored_at=None,
        enabled_at=_TS,
    )
    with pytest.raises(asyncio.CancelledError):
        await cp.verify_offhost_checkpoint(
            _HistorySession(configured, {}),
            _ORG,
            verify_key=_TEST_KEY.public_key(),
            now=_TS,
        )


async def test_shared_scanner_continues_after_database_comparison_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _signed_legacy_doc(_TEST_KEY, _ORG, 7, _HASH, _TS)
    second = _signed_legacy_doc(_TEST_KEY, _ORG, 8, b"\xbb" * 32, _TS)
    refs = (
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/7-a.json", "v1"),
        sink_service.CheckpointVersionRef(f"checkpoints/{_ORG}/8-a.json", "v2"),
    )
    documents = iter([first, second])
    comparisons: list[int] = []

    monkeypatch.setattr(
        cp,
        "list_offhost_checkpoint_versions_page",
        lambda *_args, **_kwargs: sink_service.CheckpointVersionsPage(refs, (), False, None, None),
    )
    monkeypatch.setattr(
        cp,
        "read_offhost_checkpoint_version",
        lambda *_args, **_kwargs: next(documents),
    )

    def verify_signature(**fields: Any) -> bool:
        return cp.verify_checkpoint_signature(_TEST_KEY.public_key(), **fields)

    async def compare(checkpoint: cp.AuthenticatedCheckpoint) -> str | None:
        comparisons.append(checkpoint.latest_id)
        if checkpoint.latest_id == 7:
            raise cp.CheckpointComparisonUnavailable
        return None

    result = await cp.scan_offhost_history(
        _ORG,
        kind="worm_bucket",
        connection={"bucket": "legacy"},
        reader=None,
        verify_signature=verify_signature,
        compare_checkpoint=compare,
        now=_TS,
    )

    assert comparisons == [7, 8]
    assert result.parsed_any is True
    assert result.scan_complete is True
    assert result.read_failed is False
    assert result.attestation_failed is False
    assert result.comparison_unavailable is True
    assert result.reasons == ["database checkpoint comparison unavailable"]


@pytest.mark.parametrize(
    ("last_anchored", "enabled_at", "expected_attest", "expected_overdue"),
    [
        (_TS, _TS, 1, 0),
        (None, _TS, 0, 0),
        (None, _TS - datetime.timedelta(hours=25), 0, 1),
    ],
)
async def test_empty_history_preserves_existing_grace_semantics(
    monkeypatch: pytest.MonkeyPatch,
    last_anchored: datetime.datetime | None,
    enabled_at: datetime.datetime,
    expected_attest: int,
    expected_overdue: int,
) -> None:
    result = await _run_history(
        monkeypatch,
        [],
        stored={},
        last_anchored_at=last_anchored,
        enabled_at=enabled_at,
    )
    assert result.attest_failures == expected_attest
    assert result.unanchored_overdue == expected_overdue
    assert not result.verified
