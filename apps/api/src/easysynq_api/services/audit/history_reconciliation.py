"""Inactive bounded reconciliation of externally required checkpoint history.

Complete caller admission precedes owned resources. The worker implementation is
not yet wired; an admitted request cannot produce a consistency claim here.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Literal, NoReturn
from uuid import UUID

from . import bootstrap_bridge as _bridge
from . import history_collection as _collection
from .bootstrap_bridge import (
    BridgeEnrollment,
    BridgePageObservation,
    BridgeWitnessPin,
    BridgeWitnessSummary,
    LegacyPublicMaterial,
)
from .history_collection import (
    HistoryCollectionLimits,
    HistoryWitnessSummary,
    RequiredHistoryWitness,
)
from .lineage import AuditHead, BootstrapPin, RequiredCheckpointPin, StreamEnrollment

_MAX_BIGINT = 9_223_372_036_854_775_807
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

type _Status = Literal["consistent", "failed", "incomplete"]
type _Relation = Literal["not-provided", "unassessed", "included", "missing", "conflicting"]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationLimits:
    maximum_pages: int
    maximum_observations: int
    maximum_bridge_pages: int
    maximum_manifest_entries: int
    maximum_total_bytes: int
    maximum_spool_bytes: int
    maximum_wall_seconds: int
    maximum_issue_groups: int
    maximum_issues: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReference:
    kind: Literal["observation", "page"]
    index: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationIssue:
    component: Literal["collection", "bridge", "lineage", "composition"]
    code: str
    severity: Literal["failed", "incomplete"]
    witness_id: UUID | None
    count: int
    references: tuple[HistoryReference, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationCounts:
    supplied_pages: int
    package_bytes: int
    admitted_total_bytes: int
    legacy_body_deliveries: int
    v2_body_deliveries: int
    invalid_shape_deliveries: int
    legacy_duplicate_deliveries: int
    v2_duplicate_deliveries: int
    canonical_page_duplicates: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationTip:
    anchor_hash: str
    sequence: int
    audit_head: AuditHead
    key_id: str
    key_epoch: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationUsable:
    bootstrap_pin: BootstrapPin
    tip: HistoryReconciliationTip
    path_length: int
    used_epoch_count: int
    witness_summaries: tuple[BridgeWitnessSummary, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryReconciliationReport:
    status: _Status
    scope: Literal["collected-required-witness-history"]
    witnesses: tuple[HistoryWitnessSummary, ...]
    counts: HistoryReconciliationCounts
    issues: tuple[HistoryReconciliationIssue, ...]
    failed_issues: int
    incomplete_issues: int
    issues_omitted: int
    required_checkpoint_relation: _Relation
    established_checks: tuple[str, ...]
    unproved_checks: tuple[str, ...]
    usable: HistoryReconciliationUsable | None


class HistoryReconciliationInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid history reconciliation input")


class HistoryReconciliationError(Exception):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("invalid history reconciliation error code") from None
        self.code = code
        super().__init__(f"history reconciliation failed: {code}")


class HistoryReconciliationCancelled(BaseException):
    def __init__(self) -> None:
        super().__init__("history reconciliation cancelled")


def _input_invalid() -> NoReturn:
    raise HistoryReconciliationInputError() from None


def _integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _input_invalid()


def _validate_limits(limits: HistoryReconciliationLimits) -> None:
    if type(limits) is not HistoryReconciliationLimits:
        _input_invalid()
    _integer(limits.maximum_pages, 1, 4096)
    _integer(limits.maximum_observations, 1, 100_000)
    _integer(limits.maximum_bridge_pages, 1, 1024)
    _integer(limits.maximum_manifest_entries, 1, 100_000)
    _integer(limits.maximum_total_bytes, 1, 1_073_741_824)
    _integer(limits.maximum_spool_bytes, 65_536, 1_073_741_824)
    if limits.maximum_spool_bytes % 4096:
        _input_invalid()
    _integer(limits.maximum_wall_seconds, 1, 86_400)
    _integer(limits.maximum_issue_groups, 1, 1_000_000)
    _integer(limits.maximum_issues, 1, 32)


def _enrollment_shapes(enrollment: BridgeEnrollment) -> None:
    """Keep R78's external authority shapes without its supplied-history ceilings."""
    stream = enrollment.stream
    if (
        type(stream) is not StreamEnrollment
        or type(stream.org_id) is not UUID
        or type(stream.stream_id) is not UUID
    ):
        _input_invalid()
    pin = stream.bootstrap
    if type(pin) is not BootstrapPin:
        _input_invalid()
    _bridge._digest(pin.commitment_hash)
    _bridge._digest(pin.initial_key_id, key=True)
    if type(pin.initial_public_key) is not bytes or len(pin.initial_public_key) != 32:
        _input_invalid()
    _integer(pin.initial_key_epoch, 0, _MAX_BIGINT)
    _bridge._head(pin.audit_boundary)
    required = stream.required_checkpoint
    if required is not None:
        if type(required) is not RequiredCheckpointPin:
            _input_invalid()
        _bridge._digest(required.anchor_hash)
        _integer(required.sequence, 1, _MAX_BIGINT)
    if type(enrollment.witnesses) is not tuple or not 1 <= len(enrollment.witnesses) <= 4:
        _input_invalid()
    if type(enrollment.legacy_keys) is not tuple or len(enrollment.legacy_keys) > 8:
        _input_invalid()
    for witness in enrollment.witnesses:
        if type(witness) is not BridgeWitnessPin or type(witness.witness_id) is not UUID:
            _input_invalid()
        _bridge._digest(witness.namespace_hash)
    if len({w.witness_id for w in enrollment.witnesses}) != len(enrollment.witnesses):
        _input_invalid()
    for material in enrollment.legacy_keys:
        if (
            type(material) is not LegacyPublicMaterial
            or type(material.public_key) is not bytes
            or len(material.public_key) != 32
        ):
            _input_invalid()
        _bridge._digest(material.key_id, key=True)
    if len({m.key_id for m in enrollment.legacy_keys}) != len(enrollment.legacy_keys):
        _input_invalid()


def _admit_reconciliation(
    enrollment: BridgeEnrollment,
    root_body: bytes | None,
    pages: tuple[BridgePageObservation, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryReconciliationLimits,
    cancel: threading.Event | None,
) -> tuple[RequiredHistoryWitness, ...]:
    _validate_limits(limits)
    if (
        type(enrollment) is not BridgeEnrollment
        or type(pages) is not tuple
        or type(readers) is not tuple
        or (root_body is not None and type(root_body) is not bytes)
        or (cancel is not None and type(cancel) is not threading.Event)
    ):
        _input_invalid()
    try:
        _enrollment_shapes(enrollment)
        # Over-limit tuples do not authorize inspecting their elements or doing crypto.
        if len(pages) > limits.maximum_bridge_pages:
            raise HistoryReconciliationError("RESOURCE_LIMIT") from None
        for page in pages:
            if type(page) is not BridgePageObservation or type(page.body) is not bytes:
                _input_invalid()
        # Preserve retained legacy material admission, including historical weak material;
        # only the initial v2 key receives R76 public-material admission.
        _bridge._admit(enrollment)
        admitted = _collection._validate_collection_inputs(
            enrollment.stream.org_id,
            enrollment.witnesses,
            readers,
            HistoryCollectionLimits(
                limits.maximum_pages,
                limits.maximum_observations,
                limits.maximum_total_bytes,
                limits.maximum_spool_bytes,
                limits.maximum_wall_seconds,
                limits.maximum_issues,
            ),
            cancel,
        )
    except _bridge.BridgeInputError as error:
        if type(error) is not _bridge.BridgeInputError:
            raise
        _input_invalid()
    except _collection.HistoryCollectionInputError as error:
        if type(error) is not _collection.HistoryCollectionInputError:
            raise
        _input_invalid()
    package_bytes = (0 if root_body is None else len(root_body)) + sum(len(p.body) for p in pages)
    if package_bytes > limits.maximum_total_bytes:
        raise HistoryReconciliationError("RESOURCE_LIMIT") from None
    return admitted


def collect_and_reconcile_checkpoint_history(
    enrollment: BridgeEnrollment,
    root_body: bytes | None,
    pages: tuple[BridgePageObservation, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryReconciliationLimits,
    *,
    cancel: threading.Event | None = None,
) -> HistoryReconciliationReport:
    _admit_reconciliation(enrollment, root_body, pages, readers, limits, cancel)
    # Replaced only when the complete owned worker and final-report boundary are wired.
    raise HistoryReconciliationError("RUNTIME_UNSUPPORTED") from None
