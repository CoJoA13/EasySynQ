"""Inactive, bounded traversal of externally required audit-history witnesses.

Pure admission precedes all ownership. Diagnostics are published only after the
private spool and watchdog are gone; traversal authenticates no retained body.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import threading
import time
from typing import TYPE_CHECKING, Literal, NoReturn
from uuid import UUID

import rfc8785

from . import isolated_raw, isolated_version_page, raw_transport, version_page_transport
from .bootstrap_bridge import BridgeWitnessPin
from .sink import ExplicitHistoryReader

if TYPE_CHECKING:
    from ._history_spool import _SpoolSession
    from ._history_spool_protocol import _SpoolSummary

_monotonic = time.monotonic
_WATCH_POLL_SECONDS = 0.05
_WATCH_JOIN_SECONDS = 2.0

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


def _raise_collection_faults(faults: list[BaseException]) -> NoReturn:
    if len(faults) == 1:
        raise faults[0] from None
    raise BaseExceptionGroup("checkpoint history collection operation and cleanup failed", faults)


def _contains_fault(container: BaseException, fault: BaseException) -> bool:
    pending = [container]
    while pending:
        current = pending.pop()
        if current is fault:
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return False


class _CollectionOwner:
    """One interrupt signal with a separately observed cancellation/deadline reason."""

    def __init__(self, cancel: threading.Event | None, deadline: float) -> None:
        self.cancel = threading.Event()
        self.deadline = deadline
        self._caller = cancel
        self._stop = threading.Event()
        self._stopping = False
        self._faults: list[BaseException] = []
        self._thread = threading.Thread(target=self._watch, name="audit-history-watchdog")

    def check(self) -> None:
        if self._faults:
            _raise_collection_faults(list(self._faults))
        if self._caller is not None and self._caller.is_set():
            raise HistoryCollectionCancelled()
        if _monotonic() >= self.deadline:
            raise HistoryCollectionError("DEADLINE_EXCEEDED")

    def _watch(self) -> None:
        try:
            while not self._stopping:
                if (
                    self._caller is not None and self._caller.is_set()
                ) or _monotonic() >= self.deadline:
                    self.cancel.set()
                    return
                if self._stop.wait(_WATCH_POLL_SECONDS):
                    return
        except BaseException as error:  # noqa: BLE001 - target faults belong to the owner
            self._faults.append(error)
            try:
                self.cancel.set()
            except BaseException as interrupt_error:  # noqa: BLE001 - retain both failures
                self._faults.append(interrupt_error)

    def start(self) -> None:
        self.check()
        self._thread.start()
        self.check()

    def close(self) -> list[BaseException]:
        faults: list[BaseException] = []
        # The flag also stops the bounded loop if waking its Event fails.
        self._stopping = True
        try:
            self._stop.set()
        except BaseException as error:  # noqa: BLE001 - joining remains independently required
            faults.append(error)
        try:
            if self._thread.ident is not None:
                self._thread.join(_WATCH_JOIN_SECONDS)
        except BaseException as error:  # noqa: BLE001 - retain unexpected join identities
            faults.append(error)
        try:
            if self._thread.is_alive():
                faults.append(HistoryCollectionError("CLEANUP_FAILED"))
        except BaseException as error:  # noqa: BLE001 - uncertain liveness forbids publication
            faults.extend((error, HistoryCollectionError("CLEANUP_FAILED")))
        faults.extend(self._faults)
        return faults


def _list_failure_code(error: BaseException, owner: _CollectionOwner) -> str:
    if type(error) is version_page_transport.VersionPageReadCancelled:
        owner.check()
        raise error
    if type(error) not in (
        isolated_version_page.IsolatedVersionPageError,
        version_page_transport.VersionPageReadError,
    ):
        raise error
    if isinstance(
        error,
        (
            isolated_version_page.IsolatedVersionPageError,
            version_page_transport.VersionPageReadError,
        ),
    ):
        if error.code == "CLEANUP_FAILED":
            raise error
        owner.check()
        return error.code
    raise error


def _version_failure_code(error: BaseException, owner: _CollectionOwner) -> str:
    if type(error) is raw_transport.RawVersionReadCancelled:
        owner.check()
        raise error
    if type(error) not in (isolated_raw.IsolatedRawReadError, raw_transport.RawVersionReadError):
        raise error
    if isinstance(error, (isolated_raw.IsolatedRawReadError, raw_transport.RawVersionReadError)):
        if error.code == "CLEANUP_FAILED":
            raise error
        owner.check()
        return error.code
    raise error


def _collect_into_spool(
    org_id: UUID,
    readers: tuple[RequiredHistoryWitness, ...],
    spool: _SpoolSession,
    owner: _CollectionOwner,
) -> int:
    from ._history_spool_protocol import _PageAdmission

    observation_count = 0
    for witness_index, witness in enumerate(readers):
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            owner.check()
            ticket = spool.reserve_page(witness_index, key_marker, version_marker)
            owner.check()
            if ticket is None:
                spool.record_cycle(witness_index)
                break
            spool._guard_external_io()
            try:
                raw_page = isolated_version_page.read_raw_checkpoint_version_page_isolated(
                    witness.reader,
                    org_id,
                    key_marker=key_marker,
                    version_id_marker=version_marker,
                    cancel=owner.cancel,
                )
            except BaseException as error:  # noqa: BLE001 - only exact plain errors are gaps
                code = _list_failure_code(error, owner)
                spool.record_list_failure(ticket, code)
                break
            owner.check()
            if type(raw_page) is not version_page_transport.RawCheckpointVersionPage:
                raise HistoryCollectionError("PROTOCOL_INVALID")
            admitted = spool.admit_page(ticket, raw_page.body)
            owner.check()
            page = raw_page.page
            versions, deletes = len(page.versions), len(page.delete_markers)
            first = observation_count + 1 if versions + deletes else 0
            if type(admitted) is not _PageAdmission or (
                admitted.first_ordinal,
                admitted.version_count,
                admitted.delete_count,
            ) != (first, versions, deletes):
                raise HistoryCollectionError("PROTOCOL_INVALID")
            observation_count += versions + deletes
            for ordinal, ref in enumerate(page.versions, start=first):
                owner.check()
                try:
                    raw_transport._validate_inputs(witness.reader, ref, owner.cancel)
                except raw_transport.RawVersionInputError:
                    spool.record_version_failure(ordinal, "INELIGIBLE_LOCATOR", ineligible=True)
                    continue
                spool._guard_external_io()
                try:
                    raw = isolated_raw.read_raw_checkpoint_version_isolated(
                        witness.reader,
                        ref,
                        cancel=owner.cancel,
                    )
                except BaseException as error:  # noqa: BLE001 - cleanup/fatal groups abort intact
                    code = _version_failure_code(error, owner)
                    spool.record_version_failure(
                        ordinal, code, delete_marker=code == "DELETE_MARKER"
                    )
                    continue
                owner.check()
                if (
                    type(raw) is not raw_transport.RawCheckpointVersion
                    or type(raw.key) is not str
                    or type(raw.version_id) is not str
                    or (raw.key, raw.version_id) != (ref.key, ref.version_id)
                ):
                    raise HistoryCollectionError("PROTOCOL_INVALID")
                spool.record_body(ordinal, raw.body)
                owner.check()
            if not page.truncated:
                break
            key_marker, version_marker = page.next_key_marker, page.next_version_id_marker
    owner.check()
    return observation_count


def _check_traversal_summary(
    summary: _SpoolSummary,
    readers: tuple[RequiredHistoryWitness, ...],
    observation_count: int,
) -> None:
    if (
        tuple(w.witness_id for w in summary.witnesses) != tuple(w.witness_id for w in readers)
        or sum(w.version_observations + w.delete_observations for w in summary.witnesses)
        != observation_count
        or any(
            w.version_observations != w.successful_reads + w.unavailable_reads
            for w in summary.witnesses
        )
    ):
        raise HistoryCollectionError("PROTOCOL_INVALID")


def _traverse_required(
    org_id: UUID,
    readers: tuple[RequiredHistoryWitness, ...],
    spool: _SpoolSession,
    owner: _CollectionOwner,
) -> _SpoolSummary:
    observation_count = _collect_into_spool(org_id, readers, spool, owner)
    owner.check()
    summary = spool.finish()
    _check_traversal_summary(summary, readers, observation_count)
    return summary


def collect_required_checkpoint_history(
    org_id: UUID,
    required_witnesses: tuple[BridgeWitnessPin, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryCollectionLimits,
    *,
    cancel: threading.Event | None = None,
) -> HistoryCollectionReport:
    admitted = _validate_collection_inputs(org_id, required_witnesses, readers, limits, cancel)
    deadline = _monotonic() + limits.maximum_wall_seconds
    # Runtime imports follow pure admission and avoid a spool/public-types cycle.
    from ._history_spool import _SpoolSession
    from ._history_spool_protocol import _SpoolWitness

    pins = {pin.witness_id: pin.namespace_hash for pin in required_witnesses}
    scopes = tuple(
        _SpoolWitness(w.witness_id, pins[w.witness_id], w.reader.bucket) for w in admitted
    )
    owner = _CollectionOwner(cancel, deadline)
    faults: list[BaseException] = []
    summary = None
    try:
        owner.start()
        with _SpoolSession(org_id, scopes, limits, cancel=owner.cancel, deadline=deadline) as spool:
            summary = _traverse_required(org_id, admitted, spool, owner)
    except BaseException as error:  # noqa: BLE001 - every exit must stop/join the watchdog
        faults.append(error)
    for fault in owner.close():
        if not any(_contains_fault(existing, fault) for existing in faults):
            faults.append(fault)
    try:
        owner.check()
    except BaseException as error:  # noqa: BLE001 - final checks cannot erase fatal identities
        if len(faults) == 1 and (
            type(faults[0]) is HistoryCollectionCancelled
            or (
                type(faults[0]) is HistoryCollectionError
                and isinstance(faults[0], HistoryCollectionError)
                and faults[0].code != "CLEANUP_FAILED"
            )
        ):
            faults = [error]
        elif not any(_contains_fault(existing, error) for existing in faults):
            faults.append(error)
    if faults:
        _raise_collection_faults(faults)
    if summary is None:
        raise HistoryCollectionError("PROTOCOL_INVALID")
    status: _CollectionStatus = (
        "failed"
        if summary.failed_issues
        else "incomplete"
        if summary.incomplete_issues
        else "traversed"
    )
    if status == "traversed" and any(not witness.terminal_reached for witness in summary.witnesses):
        raise HistoryCollectionError("PROTOCOL_INVALID")
    report = HistoryCollectionReport(
        status,
        "required-witness-provider-traversal",
        summary.witnesses,
        summary.issues,
        summary.failed_issues,
        summary.incomplete_issues,
        summary.issues_omitted,
        summary.admitted_total_bytes,
        _UNPROVED,
    )
    owner.check()
    return report
