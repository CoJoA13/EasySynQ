"""Immutable exact-version WORM identities, state, and typed storage failures."""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
from typing import Literal, NoReturn

from .staged_identity import PromotionResult

_MAX_VERSION_ID_BYTES = 1024


class WormStorageError(Exception):
    """A bounded, provider-detail-free failure at the exact WORM storage boundary."""

    def __init__(self, message: str = "exact WORM storage operation failed") -> None:
        super().__init__(message)


class WormVersionMissing(WormStorageError):
    def __init__(self) -> None:
        super().__init__("the exact WORM version is unavailable")


class WormModeMismatch(WormStorageError):
    def __init__(self) -> None:
        super().__init__("the exact WORM version is not in GOVERNANCE mode")


class WormReadbackMismatch(WormStorageError):
    def __init__(self) -> None:
        super().__init__("exact WORM state read-back did not match")


class WormProtectionWouldWeaken(WormStorageError):
    def __init__(self) -> None:
        super().__init__("exact WORM protection would be weaker than required")


class WormIdentityMismatch(WormStorageError):
    def __init__(self) -> None:
        super().__init__("exact WORM identity did not match")


class WormCapabilityDenied(WormStorageError):
    def __init__(self) -> None:
        super().__init__("exact WORM storage capability was denied")


def _provider_error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) else None


def _legal_hold_content_md5(status: Literal["ON", "OFF"]) -> str:
    """Return S3's required Content-MD5 for the canonical one-field legal-hold XML body."""
    payload = (
        '<LegalHold xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Status>{status}</Status></LegalHold>"
    ).encode()
    digest = hashlib.md5(payload, usedforsecurity=False).digest()
    return base64.b64encode(digest).decode("ascii")


def _retention_content_md5(retain_until: datetime.datetime) -> str:
    """Return S3's required Content-MD5 for boto3's canonical GOVERNANCE XML body."""
    normalized = retain_until.astimezone(datetime.UTC)
    timestamp_format = "%Y-%m-%dT%H:%M:%S.%fZ" if normalized.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    timestamp = normalized.strftime(timestamp_format)
    payload = (
        '<Retention xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Mode>GOVERNANCE</Mode>"
        f"<RetainUntilDate>{timestamp}</RetainUntilDate>"
        "</Retention>"
    ).encode()
    digest = hashlib.md5(payload, usedforsecurity=False).digest()
    return base64.b64encode(digest).decode("ascii")


def _raise_provider_failure(exc: BaseException) -> NoReturn:
    code = _provider_error_code(exc)
    if code == "NoSuchVersion":
        raise WormVersionMissing from exc
    if code == "AccessDenied":
        raise WormCapabilityDenied from exc
    raise WormStorageError from exc


def _utc_timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, datetime.datetime) or value.tzinfo is None:
        return None
    try:
        return value.astimezone(datetime.UTC)
    except (OverflowError, ValueError):
        return None


@dataclasses.dataclass(frozen=True, slots=True)
class WormObjectLocator:
    bucket: str
    object_key: str
    object_version_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.bucket, str) or not self.bucket.strip():
            raise WormIdentityMismatch
        if not isinstance(self.object_key, str) or not self.object_key.strip():
            raise WormIdentityMismatch
        if (
            not isinstance(self.object_version_id, str)
            or not self.object_version_id.strip()
            or self.object_version_id == "null"
        ):
            raise WormIdentityMismatch
        try:
            encoded = self.object_version_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise WormIdentityMismatch from exc
        if len(encoded) > _MAX_VERSION_ID_BYTES:
            raise WormIdentityMismatch


@dataclasses.dataclass(frozen=True, slots=True)
class WormRequirement:
    retain_until: datetime.datetime | None
    legal_hold: bool

    def __post_init__(self) -> None:
        if not isinstance(self.legal_hold, bool):
            raise ValueError("legal_hold must be a boolean")
        if self.retain_until is None:
            return
        normalized = _utc_timestamp(self.retain_until)
        if normalized is None:
            raise ValueError("retain_until must be timezone-aware")
        object.__setattr__(self, "retain_until", normalized)


@dataclasses.dataclass(frozen=True, slots=True)
class WormObjectState:
    locator: WormObjectLocator
    mode: Literal["GOVERNANCE"]
    retain_until: datetime.datetime
    legal_hold: bool
    read_at: datetime.datetime

    def __post_init__(self) -> None:
        if not isinstance(self.locator, WormObjectLocator):
            raise WormIdentityMismatch
        if self.mode != "GOVERNANCE":
            raise WormModeMismatch
        retain_until = _utc_timestamp(self.retain_until)
        read_at = _utc_timestamp(self.read_at)
        if retain_until is None or read_at is None:
            raise WormReadbackMismatch
        if not isinstance(self.legal_hold, bool):
            raise WormReadbackMismatch
        object.__setattr__(self, "retain_until", retain_until)
        object.__setattr__(self, "read_at", read_at)


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedWormAssertion:
    locator: WormObjectLocator
    asserted_retain_until: datetime.datetime
    asserted_at: datetime.datetime
    verified: WormObjectState

    def __post_init__(self) -> None:
        if not isinstance(self.locator, WormObjectLocator) or self.verified.locator != self.locator:
            raise WormIdentityMismatch
        asserted_retain_until = _utc_timestamp(self.asserted_retain_until)
        asserted_at = _utc_timestamp(self.asserted_at)
        if asserted_retain_until is None or asserted_at is None:
            raise WormReadbackMismatch
        if self.verified.retain_until < asserted_retain_until:
            raise WormProtectionWouldWeaken
        object.__setattr__(self, "asserted_retain_until", asserted_retain_until)
        object.__setattr__(self, "asserted_at", asserted_at)


def worm_locator_from_promotion(result: PromotionResult) -> WormObjectLocator:
    """Bind a completed staged promotion to its exact immutable target version."""
    return WormObjectLocator(
        bucket=result.target_bucket,
        object_key=result.target_key,
        object_version_id=result.target_version_id,
    )
