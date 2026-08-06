"""Immutable identities and typed failures for exact-version staging promotion."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import re
from typing import Literal


class StagingDomain(enum.StrEnum):
    STAGING = "staging"
    IMPORT_STAGING = "import-staging"


class PromotionOutcome(enum.StrEnum):
    COPIED = "copied"
    ADOPTED_EXISTING = "adopted_existing"


class StorageStage(enum.StrEnum):
    VERSIONING = "versioning"
    STAGING_PUT = "staging_put"
    SOURCE_GET = "source_get"
    SOURCE_READ = "source_read"
    TARGET_HEAD = "target_head"
    TARGET_GET = "target_get"
    TARGET_READ = "target_read"
    COPY = "copy"
    RETENTION = "retention"
    OWNER_ROLLBACK = "owner_rollback"
    AUDIT = "audit"
    CLEANUP = "cleanup"


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_VERSION_ID = 1024


class StagedPromotionError(Exception):
    """Base class for failures at the exact-version promotion boundary."""


class IdentityRefusal(StagedPromotionError):
    """A failure that proves a particular staged source must not be promoted."""


class StagingVersionRequired(StagedPromotionError):
    """No deletion-safe exact source identity was supplied."""


def _validate_version_id(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_VERSION_ID or value == "null":
        raise StagingVersionRequired


@dataclasses.dataclass(frozen=True, slots=True)
class StagedVersionLocator:
    domain: StagingDomain
    object_key: str
    version_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.domain, StagingDomain):
            raise ValueError("staging domain must be a StagingDomain member")
        if not isinstance(self.object_key, str) or not self.object_key:
            raise ValueError("staged object key must be non-empty")
        _validate_version_id(self.version_id)


@dataclasses.dataclass(frozen=True, slots=True)
class StagedObjectRef:
    locator: StagedVersionLocator
    expected_sha256: str
    content_type: str
    expected_size: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.locator, StagedVersionLocator):
            raise ValueError("locator must be a StagedVersionLocator")
        if not isinstance(self.expected_sha256, str) or not _SHA256_RE.fullmatch(
            self.expected_sha256
        ):
            raise ValueError("expected sha256 must be canonical lowercase hex")
        if self.locator.object_key != self.expected_sha256:
            raise ValueError("staged object key must equal expected sha256")
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content type must be non-empty")
        if self.expected_size is not None and (
            isinstance(self.expected_size, bool)
            or not isinstance(self.expected_size, int)
            or self.expected_size < 0
        ):
            raise ValueError("expected size must be a non-negative integer")


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedStagedObject:
    source: StagedObjectRef
    verified_sha256: str
    size: int
    content_type: str | None
    etag: str


@dataclasses.dataclass(frozen=True, slots=True)
class PromotionResult:
    outcome: PromotionOutcome
    verified_sha256: str
    size: int
    content_type: str | None
    retain_until: datetime.datetime
    source: StagedObjectRef
    source_etag: str
    target_bucket: str
    target_key: str
    target_version_id: str


class StagedSourceUnavailable(IdentityRefusal):
    def __init__(self, source: StagedObjectRef | StagedVersionLocator) -> None:
        super().__init__("the exact staged source is unavailable")
        self.source = source


class UploadIdentityMismatch(IdentityRefusal):
    def __init__(
        self,
        *,
        source: StagedObjectRef,
        expected_sha256: str,
        observed_sha256: str,
        expected_size: int | None,
        observed_size: int,
        etag: str | None,
        classification: Literal["digest_mismatch", "size_mismatch"],
    ) -> None:
        super().__init__(classification)
        self.source = source
        self.expected_sha256 = expected_sha256
        self.observed_sha256 = observed_sha256
        self.expected_size = expected_size
        self.observed_size = observed_size
        self.etag = etag
        self.classification = classification


class StagedSourceChanged(IdentityRefusal):
    def __init__(self, source: StagedObjectRef) -> None:
        super().__init__("the exact staged source changed during copy")
        self.source = source


class StorageUnavailable(StagedPromotionError):
    def __init__(self, stage: StorageStage, cause: BaseException | None = None) -> None:
        super().__init__(f"storage unavailable during {stage.value}")
        self.stage = stage
        self._cause = cause
        if cause is not None:
            self.__cause__ = cause


class WormNotApplied(StagedPromotionError):
    def __init__(self, *, target_bucket: str, target_key: str, target_version_id: str) -> None:
        super().__init__("active WORM retention was not applied")
        self.target_bucket = target_bucket
        self.target_key = target_key
        self.target_version_id = target_version_id


class TargetIdentityConflict(StagedPromotionError):
    def __init__(
        self,
        *,
        source: StagedObjectRef,
        target_bucket: str,
        target_key: str,
        target_version_id: str,
        observed_sha256: str | None,
        observed_size: int,
    ) -> None:
        super().__init__("target identity conflicts with the verified source")
        self.source = source
        self.target_bucket = target_bucket
        self.target_key = target_key
        self.target_version_id = target_version_id
        self.observed_sha256 = observed_sha256
        self.observed_size = observed_size
