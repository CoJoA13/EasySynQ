"""Pure scope admission for externally required audit-history witnesses.

This module binds caller-supplied readers to protected namespace commitments. It
does not discover providers, start workers, create storage, or perform network I/O.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import threading
from typing import Literal, NoReturn
from uuid import UUID

import rfc8785

from . import version_page_transport
from .bootstrap_bridge import BridgeWitnessPin
from .sink import ExplicitHistoryReader

_NAMESPACE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/namespace\0"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODES = frozenset(
    {
        "RESOURCE_LIMIT",
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "STORAGE_FAILED",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    }
)
_UNPROVED = (
    "body-format-and-signatures",
    "global-history-consistency",
    "provider-non-omission",
    "atomic-snapshot",
    "historical-deletion-absence",
    "witness-custody",
    "database-chain-agreement",
    "freshness",
    "rollback-memory-continuity",
    "key-activation",
)

type _CollectionStatus = Literal["traversed", "failed", "incomplete"]
type _CollectionScope = Literal["required-witness-provider-traversal"]
type _IssueSeverity = Literal["failed", "incomplete"]
type _IssueCode = Literal[
    "DELETE_OBSERVATION",
    "LOCATOR_CONFLICT",
    "LIST_UNAVAILABLE",
    "VERSION_UNAVAILABLE",
    "INELIGIBLE_LOCATOR",
    "CURSOR_CYCLE",
]


@dataclasses.dataclass(frozen=True, slots=True)
class RequiredHistoryWitness:
    witness_id: UUID
    reader: ExplicitHistoryReader = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryCollectionLimits:
    maximum_pages: int
    maximum_observations: int
    maximum_total_bytes: int
    maximum_spool_bytes: int
    maximum_wall_seconds: int
    maximum_issues: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryWitnessSummary:
    witness_id: UUID
    namespace_hash: str
    terminal_reached: bool
    page_attempts: int
    admitted_pages: int
    version_observations: int
    delete_observations: int
    successful_reads: int
    unavailable_reads: int
    duplicate_body_deliveries: int
    conflicting_locators: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryCollectionIssue:
    code: _IssueCode
    severity: _IssueSeverity
    witness_id: UUID
    count: int
    observation_ordinals: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryCollectionReport:
    status: _CollectionStatus
    scope: _CollectionScope
    witnesses: tuple[HistoryWitnessSummary, ...]
    issues: tuple[HistoryCollectionIssue, ...]
    failed_issues: int
    incomplete_issues: int
    issues_omitted: int
    admitted_total_bytes: int
    unproved_checks: tuple[str, ...]


class HistoryCollectionInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid checkpoint history collection input")


class HistoryCollectionError(Exception):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("invalid checkpoint history collection error code")
        self.code = code
        super().__init__("checkpoint history collection failed")


class HistoryCollectionCancelled(BaseException):
    def __init__(self) -> None:
        super().__init__("checkpoint history collection cancelled")


def _input_invalid() -> NoReturn:
    raise HistoryCollectionInputError() from None


def _integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _input_invalid()


def _namespace_hash(org_id: UUID, reader: ExplicitHistoryReader) -> str:
    namespace = {
        "kind": "worm_bucket",
        "endpoint": reader.endpoint,
        "bucket": reader.bucket,
        "region": reader.region,
        "prefix": f"checkpoints/{org_id}/",
    }
    return hashlib.sha256(_NAMESPACE_DOMAIN + rfc8785.dumps(namespace)).hexdigest()


def _validate_collection_inputs(
    org_id: UUID,
    required_witnesses: tuple[BridgeWitnessPin, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryCollectionLimits,
    cancel: threading.Event | None,
) -> tuple[RequiredHistoryWitness, ...]:
    if (
        type(org_id) is not UUID
        or type(required_witnesses) is not tuple
        or type(readers) is not tuple
        or type(limits) is not HistoryCollectionLimits
        or (cancel is not None and type(cancel) is not threading.Event)
    ):
        _input_invalid()
    if not 1 <= len(required_witnesses) <= 4 or not 1 <= len(readers) <= 4:
        _input_invalid()

    _integer(limits.maximum_pages, 1, 4_096)
    _integer(limits.maximum_observations, 1, 100_000)
    _integer(limits.maximum_total_bytes, 1, 1_073_741_824)
    _integer(limits.maximum_spool_bytes, 65_536, 1_073_741_824)
    if limits.maximum_spool_bytes % 4_096 != 0:
        _input_invalid()
    _integer(limits.maximum_wall_seconds, 1, 86_400)
    _integer(limits.maximum_issues, 1, 32)

    for witness in required_witnesses:
        if (
            type(witness) is not BridgeWitnessPin
            or type(witness.witness_id) is not UUID
            or type(witness.namespace_hash) is not str
            or _DIGEST.fullmatch(witness.namespace_hash) is None
        ):
            _input_invalid()
    for required in readers:
        if (
            type(required) is not RequiredHistoryWitness
            or type(required.witness_id) is not UUID
            or type(required.reader) is not ExplicitHistoryReader
        ):
            _input_invalid()

    witness_ids = tuple(witness.witness_id for witness in required_witnesses)
    reader_ids = tuple(required.witness_id for required in readers)
    if (
        len(set(witness_ids)) != len(witness_ids)
        or len(set(reader_ids)) != len(reader_ids)
        or set(witness_ids) != set(reader_ids)
    ):
        _input_invalid()

    for required in readers:
        try:
            version_page_transport._validate_inputs(required.reader, org_id, None, None, cancel)
        except version_page_transport.VersionPageInputError:
            _input_invalid()

    pins = {witness.witness_id: witness.namespace_hash for witness in required_witnesses}
    for required in readers:
        if pins[required.witness_id] != _namespace_hash(org_id, required.reader):
            _input_invalid()

    return tuple(sorted(readers, key=lambda required: required.witness_id.bytes))
