"""Exact-version WORM storage contracts."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import datetime
import hashlib
import importlib
import inspect
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import easysynq_api.services.vault as vault
from easysynq_api.services.vault import storage
from easysynq_api.services.vault.staged_identity import (
    PromotionOutcome,
    PromotionResult,
    StagedObjectRef,
    StagedVersionLocator,
    StagingDomain,
)
from easysynq_api.services.vault.worm import (
    WormCapabilityDenied,
    WormIdentityMismatch,
    WormModeMismatch,
    WormObjectLocator,
    WormObjectState,
    WormProtectionWouldWeaken,
    WormReadbackMismatch,
    WormRequirement,
    WormStorageError,
    WormVersionMissing,
    worm_locator_from_promotion,
)

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 8, 19, 12, tzinfo=datetime.UTC)
LOCATOR = WormObjectLocator("documents", "a" * 64, "opaque-version-v1")
EXACT_KWARGS = {
    "Bucket": LOCATOR.bucket,
    "Key": LOCATOR.object_key,
    "VersionId": LOCATOR.object_version_id,
}


def _legal_hold_md5(status: str) -> str:
    payload = (
        '<LegalHold xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Status>{status}</Status></LegalHold>"
    ).encode()
    return base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()


def _retention_md5() -> str:
    payload = (
        b'<Retention xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Mode>GOVERNANCE</Mode>"
        b"<RetainUntilDate>2026-10-18T12:34:56.789123Z</RetainUntilDate>"
        b"</Retention>"
    )
    return base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()


def _s3_error(code: str, operation: str, *, status: int | None = None) -> ClientError:
    if status is None:
        status = 500 if code == "InternalError" else 400
    return ClientError(
        {
            "Error": {"Code": code, "Message": "provider-secret-detail-must-not-escape"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _promotion(
    *, bucket: str = "documents", key: str = "a" * 64, version: str = "v1"
) -> PromotionResult:
    source = StagedObjectRef(
        locator=StagedVersionLocator(StagingDomain.STAGING, key, "staged-v1"),
        expected_sha256=key,
        content_type="application/pdf",
        expected_size=8,
    )
    return PromotionResult(
        outcome=PromotionOutcome.COPIED,
        verified_sha256=key,
        size=8,
        content_type="application/pdf",
        retain_until=NOW + datetime.timedelta(days=30),
        source=source,
        source_etag='"etag"',
        target_bucket=bucket,
        target_key=key,
        target_version_id=version,
    )


def _state(
    *,
    locator: WormObjectLocator = LOCATOR,
    retain_until: datetime.datetime = NOW + datetime.timedelta(days=30),
    legal_hold: bool = False,
    read_at: datetime.datetime = NOW,
) -> WormObjectState:
    return WormObjectState(
        locator=locator,
        mode="GOVERNANCE",
        retain_until=retain_until,
        legal_hold=legal_hold,
        read_at=read_at,
    )


class _Body:
    def __init__(
        self,
        data: bytes,
        *,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._stream = io.BytesIO(data)
        self._error = error
        self._close_error = close_error
        self.reads: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.reads.append(size)
        if self._error is not None:
            error = self._error
            self._error = None
            raise error
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _FatalRead(BaseException):
    pass


class _ReadStateClient:
    def __init__(
        self,
        *,
        retention: Any = None,
        hold: Any = None,
        retention_error: BaseException | None = None,
        hold_error: BaseException | None = None,
    ) -> None:
        self.retention = (
            retention
            if retention is not None
            else {
                "Retention": {
                    "Mode": "GOVERNANCE",
                    "RetainUntilDate": datetime.datetime.now(datetime.UTC)
                    + datetime.timedelta(days=30),
                }
            }
        )
        self.hold = hold if hold is not None else {"LegalHold": {"Status": "OFF"}}
        self.retention_error = retention_error
        self.hold_error = hold_error
        self.retention_calls: list[dict[str, Any]] = []
        self.hold_calls: list[dict[str, Any]] = []

    def get_object_retention(self, **kwargs: Any) -> Any:
        self.retention_calls.append(kwargs)
        if self.retention_error is not None:
            raise self.retention_error
        return self.retention

    def get_object_legal_hold(self, **kwargs: Any) -> Any:
        self.hold_calls.append(kwargs)
        if self.hold_error is not None:
            raise self.hold_error
        return self.hold


def test_locator_is_frozen_and_preserves_exact_promotion_identity() -> None:
    promotion = _promotion(bucket="records", key="b" * 64, version="opaque+/=v1")

    locator = worm_locator_from_promotion(promotion)

    assert locator == WormObjectLocator("records", "b" * 64, "opaque+/=v1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        locator.object_version_id = "latest"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("bucket", "key"),
    [("", "key"), ("   ", "key"), ("documents", ""), ("documents", "\t")],
)
def test_locator_rejects_blank_bucket_or_key(bucket: str, key: str) -> None:
    with pytest.raises(WormIdentityMismatch):
        WormObjectLocator(bucket, key, "v1")


@pytest.mark.parametrize(
    "version",
    [None, b"v1", "", " ", "null", "v" * 1025, "é" * 513],
)
def test_locator_rejects_non_exact_version_identity(version: object) -> None:
    with pytest.raises(WormIdentityMismatch):
        WormObjectLocator("documents", "key", version)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["target_bucket", "target_key", "target_version_id"])
def test_locator_from_promotion_maps_invalid_identity_to_typed_failure(field: str) -> None:
    promotion = _promotion()
    object.__setattr__(promotion, field, " ")

    with pytest.raises(WormIdentityMismatch):
        worm_locator_from_promotion(promotion)


def test_worm_object_state_rejects_non_governance_mode() -> None:
    with pytest.raises(WormModeMismatch):
        WormObjectState(  # type: ignore[arg-type]
            locator=LOCATOR,
            mode="COMPLIANCE",
            retain_until=NOW + datetime.timedelta(days=1),
            legal_hold=False,
            read_at=NOW,
        )


@pytest.mark.parametrize(
    ("retain_until", "read_at"),
    [
        (datetime.datetime(2026, 8, 20), NOW),
        (NOW + datetime.timedelta(days=1), datetime.datetime(2026, 8, 19)),
        (NOW, NOW),
        (NOW - datetime.timedelta(microseconds=1), NOW),
    ],
)
def test_worm_object_state_rejects_naive_or_nonfuture_timestamps(
    retain_until: datetime.datetime, read_at: datetime.datetime
) -> None:
    with pytest.raises(WormReadbackMismatch):
        WormObjectState(
            locator=LOCATOR,
            mode="GOVERNANCE",
            retain_until=retain_until,
            legal_hold=False,
            read_at=read_at,
        )


def test_worm_requirement_rejects_naive_timestamp_and_non_boolean_hold() -> None:
    with pytest.raises(ValueError):
        WormRequirement(retain_until=datetime.datetime(2026, 8, 20), legal_hold=False)
    with pytest.raises(ValueError):
        WormRequirement(retain_until=NOW + datetime.timedelta(days=1), legal_hold=1)  # type: ignore[arg-type]


async def test_read_worm_state_uses_exact_version_for_retention_and_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retain_until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
    client = _ReadStateClient(
        retention={"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": retain_until}},
        hold={"LegalHold": {"Status": "ON"}},
    )
    monkeypatch.setattr(storage, "_client", lambda: client)

    state = await storage.read_worm_state(LOCATOR)

    assert state.locator is LOCATOR
    assert state.mode == "GOVERNANCE"
    assert state.retain_until == retain_until
    assert state.legal_hold is True
    assert state.read_at.tzinfo is not None
    assert client.retention_calls == [EXACT_KWARGS]
    assert client.hold_calls == [EXACT_KWARGS]


async def test_read_worm_state_maps_minio_unset_hold_after_valid_governance_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold_error = _s3_error(
        "NoSuchObjectLockConfiguration",
        "GetObjectLegalHold",
        status=400,
    )
    client = _ReadStateClient(hold_error=hold_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    state = await storage.read_worm_state(LOCATOR)

    assert state.legal_hold is False
    assert client.retention_calls == [EXACT_KWARGS]
    assert client.hold_calls == [EXACT_KWARGS]


async def test_read_worm_state_does_not_map_unset_hold_code_from_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = _s3_error(
        "NoSuchObjectLockConfiguration",
        "GetObjectRetention",
        status=400,
    )
    client = _ReadStateClient(retention_error=provider_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormStorageError) as caught:
        await storage.read_worm_state(LOCATOR)

    assert caught.value.__cause__ is provider_error
    assert client.hold_calls == []


@pytest.mark.parametrize(
    "retention",
    [
        {},
        {"Retention": None},
        {"Retention": {}},
        {"Retention": {"Mode": None, "RetainUntilDate": NOW + datetime.timedelta(days=1)}},
        {"Retention": {"Mode": "GOVERNANCE"}},
        {"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": "tomorrow"}},
        {
            "Retention": {
                "Mode": "GOVERNANCE",
                "RetainUntilDate": datetime.datetime(2026, 8, 20),
            }
        },
        {
            "Retention": {
                "Mode": "GOVERNANCE",
                "RetainUntilDate": datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
            }
        },
    ],
)
async def test_read_worm_state_rejects_missing_or_malformed_retention(
    monkeypatch: pytest.MonkeyPatch, retention: Any
) -> None:
    monkeypatch.setattr(storage, "_client", lambda: _ReadStateClient(retention=retention))

    with pytest.raises(WormReadbackMismatch):
        await storage.read_worm_state(LOCATOR)


async def test_read_worm_state_rejects_compliance(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ReadStateClient(
        retention={
            "Retention": {
                "Mode": "COMPLIANCE",
                "RetainUntilDate": datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(days=30),
            }
        }
    )
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormModeMismatch):
        await storage.read_worm_state(LOCATOR)


@pytest.mark.parametrize(
    "hold",
    [
        {},
        {"LegalHold": None},
        {"LegalHold": {}},
        {"LegalHold": {"Status": None}},
        {"LegalHold": {"Status": "UNKNOWN"}},
    ],
)
async def test_read_worm_state_rejects_missing_or_invalid_hold(
    monkeypatch: pytest.MonkeyPatch, hold: Any
) -> None:
    monkeypatch.setattr(storage, "_client", lambda: _ReadStateClient(hold=hold))

    with pytest.raises(WormReadbackMismatch):
        await storage.read_worm_state(LOCATOR)


@pytest.mark.parametrize(
    ("provider_error", "expected"),
    [
        (_s3_error("NoSuchVersion", "GetObjectRetention", status=404), WormVersionMissing),
        (_s3_error("AccessDenied", "GetObjectRetention", status=403), WormCapabilityDenied),
        (_s3_error("NoSuchBucket", "GetObjectRetention", status=404), WormStorageError),
        (_s3_error("InternalError", "GetObjectRetention", status=500), WormStorageError),
        (EndpointConnectionError(endpoint_url="http://provider-secret.invalid"), WormStorageError),
    ],
)
async def test_read_worm_state_maps_provider_failures_without_detail(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: BaseException,
    expected: type[WormStorageError],
) -> None:
    client = _ReadStateClient(retention_error=provider_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(expected) as caught:
        await storage.read_worm_state(LOCATOR)

    assert caught.value.__cause__ is provider_error
    assert "provider-secret" not in str(caught.value)
    assert LOCATOR.object_key not in str(caught.value)


async def test_read_worm_state_maps_hold_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = _s3_error("AccessDenied", "GetObjectLegalHold", status=403)
    client = _ReadStateClient(hold_error=provider_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormCapabilityDenied) as caught:
        await storage.read_worm_state(LOCATOR)

    assert caught.value.__cause__ is provider_error
    assert "provider-secret" not in str(caught.value)


async def test_read_worm_state_redacts_client_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_error = RuntimeError("provider-secret-client-bootstrap")

    def fail_client() -> Any:
        raise bootstrap_error

    monkeypatch.setattr(storage, "_client", fail_client)

    with pytest.raises(WormStorageError) as caught:
        await storage.read_worm_state(LOCATOR)

    assert caught.value.__cause__ is bootstrap_error
    assert "provider-secret" not in str(caught.value)


async def test_read_worm_state_does_not_catch_client_baseexception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_client() -> Any:
        raise _FatalRead

    monkeypatch.setattr(storage, "_client", fail_client)

    with pytest.raises(_FatalRead):
        await storage.read_worm_state(LOCATOR)


class _ApplyClient:
    def __init__(self, *, retention_error: BaseException | None = None) -> None:
        self.retention_error = retention_error
        self.retention_puts: list[dict[str, Any]] = []
        self.hold_puts: list[dict[str, Any]] = []

    def put_object_retention(self, **kwargs: Any) -> None:
        self.retention_puts.append(kwargs)
        if self.retention_error is not None:
            raise self.retention_error

    def put_object_legal_hold(self, **kwargs: Any) -> None:
        self.hold_puts.append(kwargs)


def _read_sequence(
    monkeypatch: pytest.MonkeyPatch, states: list[WormObjectState]
) -> list[WormObjectLocator]:
    calls: list[WormObjectLocator] = []

    async def fake_read(locator: WormObjectLocator) -> WormObjectState:
        calls.append(locator)
        return states.pop(0)

    monkeypatch.setattr(storage, "read_worm_state", fake_read)
    return calls


async def test_apply_keeps_later_retention_and_hold_without_puts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _state(retain_until=NOW + datetime.timedelta(days=30), legal_hold=True)
    verified = _state(retain_until=current.retain_until, legal_hold=True)
    reads = _read_sequence(monkeypatch, [current, verified])
    client = _ApplyClient()
    monkeypatch.setattr(storage, "_client", lambda: client)

    assertion = await storage.apply_worm_protection(
        LOCATOR,
        WormRequirement(retain_until=NOW + datetime.timedelta(days=10), legal_hold=False),
    )

    assert client.retention_puts == []
    assert client.hold_puts == []
    assert reads == [LOCATOR, LOCATOR]
    assert assertion.locator is LOCATOR
    assert assertion.asserted_retain_until == current.retain_until
    assert assertion.verified is verified


async def test_apply_extends_exact_version_then_freshly_reads_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = datetime.datetime(2026, 10, 18, 12, 34, 56, 789123, tzinfo=datetime.UTC)
    current = _state(retain_until=NOW + datetime.timedelta(days=30))
    verified = _state(retain_until=required + datetime.timedelta(seconds=1))
    reads = _read_sequence(monkeypatch, [current, verified])
    client = _ApplyClient()
    monkeypatch.setattr(storage, "_client", lambda: client)

    assertion = await storage.apply_worm_protection(
        LOCATOR, WormRequirement(retain_until=required, legal_hold=False)
    )

    assert client.retention_puts == [
        {
            **EXACT_KWARGS,
            "Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": required},
            "ContentMD5": _retention_md5(),
        }
    ]
    assert all("BypassGovernanceRetention" not in call for call in client.retention_puts)
    assert client.hold_puts == []
    assert reads == [LOCATOR, LOCATOR]
    assert assertion.asserted_retain_until == required
    assert assertion.verified is verified


async def test_apply_sets_only_hold_on_for_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _read_sequence(monkeypatch, [_state(legal_hold=False), _state(legal_hold=True)])
    client = _ApplyClient()
    monkeypatch.setattr(storage, "_client", lambda: client)

    await storage.apply_worm_protection(
        LOCATOR, WormRequirement(retain_until=None, legal_hold=True)
    )

    assert client.retention_puts == []
    assert client.hold_puts == [
        {
            **EXACT_KWARGS,
            "LegalHold": {"Status": "ON"},
            "ContentMD5": _legal_hold_md5("ON"),
        }
    ]


async def test_apply_refuses_fresh_hold_readback_weaker_than_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _state(legal_hold=True)
    weakened = _state(legal_hold=False)
    _read_sequence(monkeypatch, [current, weakened])
    client = _ApplyClient()
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormReadbackMismatch):
        await storage.apply_worm_protection(
            LOCATOR,
            WormRequirement(retain_until=None, legal_hold=False),
        )

    assert client.hold_puts == []


def _invalid_state(**overrides: object) -> WormObjectState:
    values: dict[str, object] = {
        "locator": LOCATOR,
        "mode": "GOVERNANCE",
        "retain_until": NOW + datetime.timedelta(days=60),
        "legal_hold": True,
        "read_at": NOW,
    }
    values.update(overrides)
    return cast(WormObjectState, SimpleNamespace(**values))


@pytest.mark.parametrize(
    ("readback", "expected"),
    [
        (
            _invalid_state(retain_until=NOW + datetime.timedelta(days=20)),
            WormProtectionWouldWeaken,
        ),
        (_invalid_state(mode="COMPLIANCE"), WormModeMismatch),
        (
            _invalid_state(locator=WormObjectLocator("documents", "a" * 64, "wrong-version")),
            WormIdentityMismatch,
        ),
        (_invalid_state(legal_hold=False), WormReadbackMismatch),
    ],
)
async def test_apply_refuses_weaker_or_mismatched_fresh_readback(
    monkeypatch: pytest.MonkeyPatch,
    readback: WormObjectState,
    expected: type[WormStorageError],
) -> None:
    required = NOW + datetime.timedelta(days=30)
    _read_sequence(
        monkeypatch,
        [_state(retain_until=NOW + datetime.timedelta(days=10)), readback],
    )
    client = _ApplyClient()
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(expected):
        await storage.apply_worm_protection(
            LOCATOR, WormRequirement(retain_until=required, legal_hold=True)
        )


async def test_apply_maps_put_denial_without_provider_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = _s3_error("AccessDenied", "PutObjectRetention", status=403)
    _read_sequence(monkeypatch, [_state(retain_until=NOW + datetime.timedelta(days=10))])
    client = _ApplyClient(retention_error=provider_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormCapabilityDenied) as caught:
        await storage.apply_worm_protection(
            LOCATOR,
            WormRequirement(retain_until=NOW + datetime.timedelta(days=30), legal_hold=False),
        )

    assert caught.value.__cause__ is provider_error
    assert "provider-secret" not in str(caught.value)


async def test_apply_redacts_client_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_error = RuntimeError("provider-secret-client-bootstrap")
    _read_sequence(monkeypatch, [_state()])

    def fail_client() -> Any:
        raise bootstrap_error

    monkeypatch.setattr(storage, "_client", fail_client)

    with pytest.raises(WormStorageError) as caught:
        await storage.apply_worm_protection(
            LOCATOR,
            WormRequirement(retain_until=None, legal_hold=False),
        )

    assert caught.value.__cause__ is bootstrap_error
    assert "provider-secret" not in str(caught.value)


def test_ordinary_protection_signature_has_no_release_off_or_bypass_capability() -> None:
    signature = inspect.signature(storage.apply_worm_protection)

    assert list(signature.parameters) == ["locator", "requirement"]
    assert {field.name for field in dataclasses.fields(WormRequirement)} == {
        "retain_until",
        "legal_hold",
    }
    assert not {"release_hold", "hold_status", "bypass_governance"}.intersection(
        signature.parameters
    )


class _ObjectClient:
    def __init__(
        self,
        body: _Body,
        *,
        version: str = LOCATOR.object_version_id,
        get_error: BaseException | None = None,
    ) -> None:
        self.body = body
        self.version = version
        self.get_error = get_error
        self.calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        return {"Body": self.body, "VersionId": self.version}


async def test_stream_hash_exact_reads_one_mib_chunks_counts_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"ab" * (3 * 1024 * 1024 // 2) + b"tail"
    body = _Body(data)
    client = _ObjectClient(body)
    monkeypatch.setattr(storage, "_client", lambda: client)

    digest, size = await storage.stream_hash_exact(LOCATOR)

    assert digest == hashlib.sha256(data).hexdigest()
    assert size == len(data)
    assert client.calls == [EXACT_KWARGS]
    assert body.reads == [1 << 20] * 5
    assert body.closed is True


@pytest.mark.parametrize("error", [RuntimeError("read failed"), _FatalRead("cancelled")])
async def test_stream_hash_exact_closes_on_read_exception_or_base_exception(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    body = _Body(b"bytes", error=error)
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))

    if isinstance(error, Exception):
        with pytest.raises(WormStorageError) as caught:
            await storage.stream_hash_exact(LOCATOR)
        assert caught.value.__cause__ is error
    else:
        with pytest.raises(type(error)):
            await storage.stream_hash_exact(LOCATOR)

    assert body.closed is True


async def test_stream_hash_exact_rejects_wrong_returned_version_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"bytes")
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body, version="wrong-v2"))

    with pytest.raises(WormReadbackMismatch):
        await storage.stream_hash_exact(LOCATOR)

    assert body.closed is True


async def test_stream_hash_exact_maps_get_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = _s3_error("NoSuchVersion", "GetObject", status=404)
    client = _ObjectClient(_Body(b"unused"), get_error=provider_error)
    monkeypatch.setattr(storage, "_client", lambda: client)

    with pytest.raises(WormVersionMissing) as caught:
        await storage.stream_hash_exact(LOCATOR)

    assert caught.value.__cause__ is provider_error


async def test_stream_hash_exact_redacts_body_close_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_error = RuntimeError("provider-secret-close-detail")
    body = _Body(b"hashed", close_error=close_error)
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))

    with pytest.raises(WormStorageError) as caught:
        await storage.stream_hash_exact(LOCATOR)

    assert caught.value.__cause__ is close_error
    assert "provider-secret" not in str(caught.value)
    assert body.closed is True


async def test_stream_worm_exact_is_exact_and_closes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"streamed")
    client = _ObjectClient(body)
    monkeypatch.setattr(storage, "_client", lambda: client)

    chunks = [chunk async for chunk in storage.stream_worm_exact(LOCATOR)]

    assert chunks == [b"streamed"]
    assert client.calls == [EXACT_KWARGS]
    assert body.closed is True


async def test_stream_worm_exact_closes_on_early_aclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"x" * (2 * (1 << 20)))
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))
    stream = storage.stream_worm_exact(LOCATOR)

    assert await anext(stream) == b"x" * (1 << 20)
    await stream.aclose()

    assert body.closed is True


async def test_stream_worm_exact_closes_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"unused", error=asyncio.CancelledError())
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))

    with pytest.raises(asyncio.CancelledError):
        async for _chunk in storage.stream_worm_exact(LOCATOR):
            pass

    assert body.closed is True


async def test_stream_worm_exact_closes_and_redacts_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("provider-secret-stream-detail")
    body = _Body(b"unused", error=error)
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))

    with pytest.raises(WormStorageError) as caught:
        async for _chunk in storage.stream_worm_exact(LOCATOR):
            pass

    assert caught.value.__cause__ is error
    assert "provider-secret" not in str(caught.value)
    assert body.closed is True


async def test_stream_worm_exact_rejects_wrong_returned_version_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"unused")
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body, version="wrong-v2"))

    with pytest.raises(WormReadbackMismatch):
        async for _chunk in storage.stream_worm_exact(LOCATOR):
            pass

    assert body.closed is True


async def test_stream_worm_exact_redacts_body_close_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_error = RuntimeError("provider-secret-close-detail")
    body = _Body(b"streamed", close_error=close_error)
    monkeypatch.setattr(storage, "_client", lambda: _ObjectClient(body))

    with pytest.raises(WormStorageError) as caught:
        async for _chunk in storage.stream_worm_exact(LOCATOR):
            pass

    assert caught.value.__cause__ is close_error
    assert "provider-secret" not in str(caught.value)
    assert body.closed is True


async def test_presign_worm_get_includes_exact_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str, dict[str, Any]]] = []

    def fake_presign(method: str, key: str, bucket: str, params: dict[str, Any]) -> str:
        calls.append((method, key, bucket, params))
        return "https://object.test/exact"

    monkeypatch.setattr(storage, "_presign", fake_presign)

    url = await storage.presign_worm_get(LOCATOR)

    assert url == "https://object.test/exact"
    assert calls == [
        (
            "get_object",
            LOCATOR.object_key,
            LOCATOR.bucket,
            {"VersionId": LOCATOR.object_version_id},
        )
    ]


async def test_presign_worm_get_redacts_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = EndpointConnectionError(endpoint_url="http://provider-secret.invalid")

    def fail_presign(*_args: Any, **_kwargs: Any) -> str:
        raise provider_error

    monkeypatch.setattr(storage, "_presign", fail_presign)

    with pytest.raises(WormStorageError) as caught:
        await storage.presign_worm_get(LOCATOR)

    assert caught.value.__cause__ is provider_error
    assert "provider-secret" not in str(caught.value)


_DEFAULT_PROBE = object()


@pytest.fixture
def worm_delete() -> Any:
    return importlib.import_module("easysynq_api.services.vault._worm_delete")


class _DeleteClient:
    def __init__(
        self,
        *,
        hold_status: str = "OFF",
        hold_put_error: BaseException | None = None,
        hold_get_error: BaseException | None = None,
        delete_error: BaseException | None = None,
        probe_error: BaseException | object | None = _DEFAULT_PROBE,
    ) -> None:
        self.hold_status = hold_status
        self.hold_put_error = hold_put_error
        self.hold_get_error = hold_get_error
        self.delete_error = delete_error
        self.probe_error = (
            _s3_error("NoSuchVersion", "GetObject", status=404)
            if probe_error is _DEFAULT_PROBE
            else probe_error
        )
        self.probe_body = _Body(b"still present")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def put_object_legal_hold(self, **kwargs: Any) -> None:
        self.events.append(("put_hold", kwargs))
        if self.hold_put_error is not None:
            raise self.hold_put_error

    def get_object_legal_hold(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("get_hold", kwargs))
        if self.hold_get_error is not None:
            raise self.hold_get_error
        return {"LegalHold": {"Status": self.hold_status}}

    def delete_object(self, **kwargs: Any) -> None:
        self.events.append(("delete", kwargs))
        if self.delete_error is not None:
            raise self.delete_error

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("get", kwargs))
        if isinstance(self.probe_error, BaseException):
            raise self.probe_error
        return {
            "VersionId": LOCATOR.object_version_id,
            "ContentLength": 1,
            "Body": self.probe_body,
        }


async def test_internal_delete_redacts_client_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    bootstrap_error = RuntimeError("provider-secret-client-bootstrap")

    def fail_client() -> Any:
        raise bootstrap_error

    monkeypatch.setattr(worm_delete, "_client", fail_client)

    with pytest.raises(WormStorageError) as caught:
        await worm_delete.delete_worm_version(
            LOCATOR,
            release_hold=True,
            bypass_governance=True,
        )

    assert caught.value.__cause__ is bootstrap_error
    assert "provider-secret" not in str(caught.value)


async def test_internal_delete_releases_hold_then_bypasses_one_exact_version(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    client = _DeleteClient()
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    await worm_delete.delete_worm_version(LOCATOR, release_hold=True, bypass_governance=True)

    assert client.events == [
        (
            "put_hold",
            {
                **EXACT_KWARGS,
                "LegalHold": {"Status": "OFF"},
                "ContentMD5": _legal_hold_md5("OFF"),
            },
        ),
        ("get_hold", EXACT_KWARGS),
        ("delete", {**EXACT_KWARGS, "BypassGovernanceRetention": True}),
        ("get", EXACT_KWARGS),
    ]


async def test_internal_delete_without_bypass_omits_bypass_header(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    client = _DeleteClient()
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=False)

    assert client.events == [("delete", EXACT_KWARGS), ("get", EXACT_KWARGS)]


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (_DeleteClient(hold_status="ON"), WormReadbackMismatch),
        (
            _DeleteClient(
                hold_put_error=_s3_error("AccessDenied", "PutObjectLegalHold", status=403)
            ),
            WormCapabilityDenied,
        ),
    ],
)
async def test_internal_delete_never_deletes_when_hold_release_or_readback_fails(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    client: _DeleteClient,
    expected: type[WormStorageError],
) -> None:
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(expected):
        await worm_delete.delete_worm_version(LOCATOR, release_hold=True, bypass_governance=True)

    assert all(event != "delete" for event, _kwargs in client.events)
    assert all(event != "get" for event, _kwargs in client.events)


@pytest.mark.parametrize("stage", ["put", "get"])
async def test_internal_delete_confirms_absence_after_hold_stage_nosuchversion(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    stage: str,
) -> None:
    error = _s3_error(
        "NoSuchVersion",
        "PutObjectLegalHold" if stage == "put" else "GetObjectLegalHold",
        status=404,
    )
    client = _DeleteClient(
        hold_put_error=error if stage == "put" else None,
        hold_get_error=error if stage == "get" else None,
    )
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    await worm_delete.delete_worm_version(
        LOCATOR,
        release_hold=True,
        bypass_governance=True,
    )

    expected = ["put_hold", "get"] if stage == "put" else ["put_hold", "get_hold", "get"]
    assert [event for event, _kwargs in client.events] == expected


@pytest.mark.parametrize("stage", ["put", "get"])
async def test_internal_delete_refuses_hold_stage_absence_when_exact_get_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    stage: str,
) -> None:
    error = _s3_error(
        "NoSuchVersion",
        "PutObjectLegalHold" if stage == "put" else "GetObjectLegalHold",
        status=404,
    )
    client = _DeleteClient(
        hold_put_error=error if stage == "put" else None,
        hold_get_error=error if stage == "get" else None,
        probe_error=None,
    )
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(WormStorageError):
        await worm_delete.delete_worm_version(
            LOCATOR,
            release_hold=True,
            bypass_governance=True,
        )

    assert client.probe_body.closed is True
    assert all(event != "delete" for event, _kwargs in client.events)


@pytest.mark.parametrize("stage", ["put", "get"])
async def test_internal_delete_maps_probe_error_after_hold_stage_nosuchversion(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    stage: str,
) -> None:
    hold_error = _s3_error(
        "NoSuchVersion",
        "PutObjectLegalHold" if stage == "put" else "GetObjectLegalHold",
        status=404,
    )
    probe_error = EndpointConnectionError(endpoint_url="http://provider-secret.invalid")
    client = _DeleteClient(
        hold_put_error=hold_error if stage == "put" else None,
        hold_get_error=hold_error if stage == "get" else None,
        probe_error=probe_error,
    )
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(WormStorageError) as caught:
        await worm_delete.delete_worm_version(
            LOCATOR,
            release_hold=True,
            bypass_governance=True,
        )

    assert caught.value.__cause__ is probe_error
    assert "provider-secret" not in str(caught.value)
    assert all(event != "delete" for event, _kwargs in client.events)


async def test_internal_delete_confirms_delete_reported_absence_with_exact_probe(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    client = _DeleteClient(delete_error=_s3_error("NoSuchVersion", "DeleteObject", status=404))
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=True)

    assert client.events == [
        ("delete", {**EXACT_KWARGS, "BypassGovernanceRetention": True}),
        ("get", EXACT_KWARGS),
    ]


async def test_internal_delete_does_not_trust_delete_absence_without_probe_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    client = _DeleteClient(
        delete_error=_s3_error("NoSuchVersion", "DeleteObject", status=404),
        probe_error=RuntimeError("object still reachable"),
    )
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(WormStorageError):
        await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=True)

    assert [event for event, _kwargs in client.events] == ["delete", "get"]


@pytest.mark.parametrize(
    ("provider_error", "expected"),
    [
        (_s3_error("AccessDenied", "DeleteObject", status=403), WormCapabilityDenied),
        (_s3_error("NoSuchBucket", "DeleteObject", status=404), WormStorageError),
        (_s3_error("InternalError", "DeleteObject", status=500), WormStorageError),
        (EndpointConnectionError(endpoint_url="http://provider-secret.invalid"), WormStorageError),
    ],
)
async def test_internal_delete_fails_without_probe_for_nonabsence_delete_errors(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    provider_error: BaseException,
    expected: type[WormStorageError],
) -> None:
    client = _DeleteClient(delete_error=provider_error)
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(expected) as caught:
        await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=True)

    assert [event for event, _kwargs in client.events] == ["delete"]
    assert caught.value.__cause__ is provider_error
    assert "provider-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("probe_error", "expected"),
    [
        (_s3_error("NoSuchBucket", "GetObject", status=404), WormStorageError),
        (_s3_error("AccessDenied", "GetObject", status=403), WormCapabilityDenied),
        (_s3_error("InternalError", "GetObject", status=500), WormStorageError),
        (EndpointConnectionError(endpoint_url="http://provider-secret.invalid"), WormStorageError),
    ],
)
async def test_internal_delete_requires_exact_nosuchversion_probe(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
    probe_error: BaseException,
    expected: type[WormStorageError],
) -> None:
    client = _DeleteClient(probe_error=probe_error)
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(expected):
        await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=True)

    assert [event for event, _kwargs in client.events] == ["delete", "get"]


async def test_internal_delete_refuses_probe_that_still_returns_a_version(
    monkeypatch: pytest.MonkeyPatch,
    worm_delete: Any,
) -> None:
    client = _DeleteClient(probe_error=None)
    monkeypatch.setattr(worm_delete, "_client", lambda: client)

    with pytest.raises(WormStorageError):
        await worm_delete.delete_worm_version(LOCATOR, release_hold=False, bypass_governance=True)

    assert client.probe_body.closed is True


def test_exact_delete_is_unexported_and_has_no_production_import_or_wiring() -> None:
    assert not hasattr(vault, "delete_worm_version")
    source_root = Path(__file__).parents[2] / "src" / "easysynq_api"
    internal_module = source_root / "services" / "vault" / "_worm_delete.py"
    offenders = []
    for path in source_root.rglob("*.py"):
        if path == internal_module:
            continue
        text = path.read_text(encoding="utf-8")
        if "_worm_delete" in text or "delete_worm_version" in text:
            offenders.append(path.relative_to(source_root).as_posix())

    assert offenders == []
