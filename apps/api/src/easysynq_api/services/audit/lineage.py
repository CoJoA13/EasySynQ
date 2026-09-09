"""Bounded, pure evaluation of supplied v2 history against explicit external pins.

Graph consistency does not establish collection, custody, audit-row agreement,
freshness, bootstrap contents or operational key activation. No current consumer
is integrated with this evaluator.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import re
from collections import deque
from typing import Literal, NoReturn, cast
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from easysynq_api.services.audit import checkpoint_v2
from easysynq_api.services.audit.checkpoint_v2 import VerifiedEnvelope

_MAX_BIGINT = 9_223_372_036_854_775_807
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"ed25519-sha256:[0-9a-f]{64}\Z")
_UNPROVED = (
    "bootstrap-contents",
    "legacy-bridge-coverage",
    "witness-collection-completeness",
    "witness-custody",
    "audit-chain-comparison",
    "freshness",
    "operational-key-activation",
)

type _Severity = Literal["failed", "incomplete"]
type _Relation = Literal["not-provided", "unassessed", "included", "missing", "conflicting"]
type _Order = tuple[str, str, str, str, int]


@dataclasses.dataclass(frozen=True, slots=True)
class AuditHead:
    latest_id: int
    latest_row_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class BootstrapPin:
    commitment_hash: str
    initial_key_id: str
    initial_public_key: bytes
    initial_key_epoch: int
    audit_boundary: AuditHead | None


@dataclasses.dataclass(frozen=True, slots=True)
class RequiredCheckpointPin:
    anchor_hash: str
    sequence: int


@dataclasses.dataclass(frozen=True, slots=True)
class StreamEnrollment:
    org_id: UUID
    stream_id: UUID
    bootstrap: BootstrapPin
    required_checkpoint: RequiredCheckpointPin | None


@dataclasses.dataclass(frozen=True, slots=True)
class EnvelopeObservation:
    source_id: str
    object_key: str
    version_id: str
    body: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class LineageLimits:
    maximum_observations: int
    maximum_total_bytes: int
    maximum_issues: int


@dataclasses.dataclass(frozen=True, slots=True)
class LineageIssue:
    code: str
    severity: Literal["failed", "incomplete"]
    observation_indexes: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizedKeyEpoch:
    key_epoch: int
    key_id: str
    public_key: bytes
    introduced_by: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class LineageEvaluation:
    status: Literal["consistent", "failed", "incomplete"]
    scope: Literal["supplied-v2-graph"]
    bootstrap_assurance: Literal["external-pin-only"]
    unproved_checks: tuple[str, ...]
    ordered_envelopes: tuple[VerifiedEnvelope, ...]
    key_history: tuple[AuthorizedKeyEpoch, ...]
    tip_hash: str | None
    tip_sequence: int | None
    issues: tuple[LineageIssue, ...]
    issues_omitted: int
    failed_issues: int
    incomplete_issues: int
    duplicate_observations: int
    required_checkpoint_relation: Literal[
        "not-provided", "unassessed", "included", "missing", "conflicting"
    ]


class LineageInputError(ValueError):
    """Fixed-text rejection of caller structure or external enrollment."""


def _input_error() -> NoReturn:
    raise LineageInputError("invalid lineage input") from None


def _integer(value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _input_error()


def _hash(value: str) -> None:
    if type(value) is not str or len(value) != 64 or _HEX.fullmatch(value) is None:
        _input_error()


def _preflight_shapes(
    enrollment: StreamEnrollment,
    observations: tuple[EnvelopeObservation, ...],
    limits: LineageLimits,
) -> None:
    if (
        type(enrollment) is not StreamEnrollment
        or type(limits) is not LineageLimits
        or type(observations) is not tuple
    ):
        _input_error()
    _integer(limits.maximum_observations, 1, 4096)
    _integer(limits.maximum_total_bytes, 1, 16 * 1024 * 1024)
    _integer(limits.maximum_issues, 1, 32)
    if type(enrollment.org_id) is not UUID or type(enrollment.stream_id) is not UUID:
        _input_error()
    pin = enrollment.bootstrap
    if type(pin) is not BootstrapPin:
        _input_error()
    _hash(pin.commitment_hash)
    if (
        type(pin.initial_key_id) is not str
        or len(pin.initial_key_id) != 79
        or _KEY_ID.fullmatch(pin.initial_key_id) is None
        or type(pin.initial_public_key) is not bytes
        or len(pin.initial_public_key) != 32
    ):
        _input_error()
    _integer(pin.initial_key_epoch, 0, _MAX_BIGINT)
    boundary = pin.audit_boundary
    if boundary is not None:
        if type(boundary) is not AuditHead:
            _input_error()
        _integer(boundary.latest_id, 1, _MAX_BIGINT)
        _hash(boundary.latest_row_hash)
    required = enrollment.required_checkpoint
    if required is not None:
        if type(required) is not RequiredCheckpointPin:
            _input_error()
        _hash(required.anchor_hash)
        _integer(required.sequence, 1, _MAX_BIGINT)


def _label(value: str, *, source: bool = False) -> None:
    if type(value) is not str or not value or len(value) > (128 if source else 1024):
        _input_error()
    if source:
        if any(not 0x20 <= ord(character) <= 0x7E for character in value):
            _input_error()
    else:
        if any(
            ord(character) < 0x20
            or 0x7F <= ord(character) <= 0x9F
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ):
            _input_error()
        if len(value.encode("utf-8")) > 1024:
            _input_error()


def _preflight_observations(observations: tuple[EnvelopeObservation, ...]) -> int:
    total = 0
    for observation in observations:
        if type(observation) is not EnvelopeObservation or type(observation.body) is not bytes:
            _input_error()
        _label(observation.source_id, source=True)
        _label(observation.object_key)
        _label(observation.version_id)
        total += len(observation.body)
    return total


@dataclasses.dataclass(slots=True)
class _Raw:
    body: bytes
    digest: str
    index: int
    count: int = 1


@dataclasses.dataclass(slots=True)
class _Node:
    envelope: VerifiedEnvelope
    index: int


@dataclasses.dataclass(slots=True)
class _Conflict:
    # Only the best representative of the best two distinct sides is retained.
    sides: list[tuple[bytes | str, int]] = dataclasses.field(default_factory=list)

    def offer(self, side: bytes | str, index: int, orders: list[_Order]) -> None:
        for position, (existing, previous) in enumerate(self.sides):
            if existing == side:
                if orders[index] < orders[previous]:
                    self.sides[position] = (side, index)
                break
        else:
            self.sides.append((side, index))
        self.sides.sort(key=lambda item: orders[item[1]])
        del self.sides[2:]


class _Diagnostics:
    def __init__(self, orders: list[_Order]) -> None:
        self.orders = orders
        self.groups: dict[tuple[_Severity, str, tuple[str, ...]], tuple[int, ...]] = {}

    def add(
        self,
        code: str,
        severity: _Severity,
        subject: tuple[str, ...] = (),
        indexes: tuple[int, ...] = (),
    ) -> None:
        key = (severity, code, subject)
        previous = self.groups.get(key)
        if previous is not None and len(indexes) <= 1:
            indexes = tuple(sorted(set(previous + indexes), key=self.orders.__getitem__)[:1])
        self.groups[key] = indexes

    def conflicts(self, code: str, conflicts: dict[tuple[str, ...], _Conflict]) -> None:
        for subject, conflict in conflicts.items():
            if len(conflict.sides) == 2:
                self.add(code, "failed", subject, tuple(index for _, index in conflict.sides))


def _edge_fault(
    child: VerifiedEnvelope,
    *,
    expected_key_id: str,
    expected_epoch: int,
    expected_sequence: int,
    previous_head: AuditHead | None,
) -> str | None:
    if (child.key_id, child.key_epoch) != (expected_key_id, expected_epoch):
        return "KEY_EPOCH_VIOLATION"
    if child.sequence != expected_sequence:
        return "SEQUENCE_DISCONTINUITY"
    if previous_head is not None:
        if child.latest_id < previous_head.latest_id:
            return "AUDIT_HEAD_REGRESSION"
        if (
            child.latest_id == previous_head.latest_id
            and child.latest_row_hash != previous_head.latest_row_hash
        ):
            return "AUDIT_HEAD_CONFLICT"
    return None


class _Graph:
    """Material and admitted-parent events have separate, one-shot waiting indexes."""

    def __init__(
        self, enrollment: StreamEnrollment, observations: tuple[EnvelopeObservation, ...]
    ) -> None:
        self.enrollment = enrollment
        self.orders: list[_Order] = []
        self.raws: dict[bytes, _Raw] = {}
        self.locators: dict[tuple[str, ...], _Conflict] = {}
        for index, observation in enumerate(observations):
            raw = self.raws.get(observation.body)
            digest = raw.digest if raw is not None else hashlib.sha256(observation.body).hexdigest()
            locator = (observation.source_id, observation.object_key, observation.version_id)
            self.orders.append((*locator, digest, index))
            if raw is None:
                self.raws[observation.body] = _Raw(observation.body, digest, index)
            else:
                raw.count += 1
                if self.orders[index] < self.orders[raw.index]:
                    raw.index = index
            self.locators.setdefault(locator, _Conflict()).offer(
                observation.body, index, self.orders
            )
        self.diagnostics = _Diagnostics(self.orders)
        self.material: dict[str, bytes] = {
            enrollment.bootstrap.initial_key_id: enrollment.bootstrap.initial_public_key
        }
        self.key_events = deque([enrollment.bootstrap.initial_key_id])
        self.parent_events: deque[str] = deque()
        self.waiting_keys: dict[str, list[_Raw]] = {}
        self.waiting_parents: dict[str, list[_Node]] = {}
        self.nodes: dict[str, _Node] = {}
        self.admitted: dict[str, _Node] = {}
        self.rejected: dict[str, str] = {}
        self.authenticated_observations = 0

    def inspect(self) -> None:
        self.diagnostics.conflicts("IMMUTABLE_LOCATOR_CONFLICT", self.locators)
        for raw in self.raws.values():
            try:
                route = checkpoint_v2.inspect_envelope_route(raw.body)
            except checkpoint_v2.CheckpointV2Error:
                self.diagnostics.add("ENVELOPE_INVALID", "failed", (raw.digest,), (raw.index,))
                continue
            if (route.org_id, route.stream_id) != (
                self.enrollment.org_id,
                self.enrollment.stream_id,
            ):
                self.diagnostics.add("IDENTITY_MISMATCH", "failed", (raw.digest,), (raw.index,))
                continue
            self.waiting_keys.setdefault(route.key_id, []).append(raw)

    def authenticate(self, key_id: str) -> None:
        key = Ed25519PublicKey.from_public_bytes(self.material[key_id])
        for raw in self.waiting_keys.pop(key_id, ()):
            try:
                envelope = checkpoint_v2.verify_envelope(
                    raw.body,
                    public_key=key,
                    org_id=self.enrollment.org_id,
                    stream_id=self.enrollment.stream_id,
                )
            except checkpoint_v2.CheckpointV2Error:
                self.diagnostics.add("ENVELOPE_INVALID", "failed", (raw.digest,), (raw.index,))
                continue
            self.authenticated_observations += raw.count
            existing = self.nodes.get(envelope.anchor_hash)
            if existing is not None:
                if self.orders[raw.index] < self.orders[existing.index]:
                    existing.index = raw.index
                continue
            node = _Node(envelope, raw.index)
            self.nodes[envelope.anchor_hash] = node
            if (
                envelope.previous_anchor_hash == self.enrollment.bootstrap.commitment_hash
                or envelope.previous_anchor_hash in self.admitted
            ):
                self.assess(node)
            else:
                self.waiting_parents.setdefault(envelope.previous_anchor_hash, []).append(node)

    def assess(self, node: _Node) -> None:
        child = node.envelope
        bootstrap = self.enrollment.bootstrap
        if child.previous_anchor_hash == bootstrap.commitment_hash:
            key_id = bootstrap.initial_key_id
            epoch = bootstrap.initial_key_epoch
            sequence = 1
            head = bootstrap.audit_boundary
        else:
            parent = self.admitted[child.previous_anchor_hash].envelope
            if parent.kind == "key_transition":
                # These fields are guaranteed by the full codec, never by the route.
                key_id = cast(str, parent.next_key_id)
                epoch = cast(int, parent.next_key_epoch)
            else:
                key_id = parent.key_id
                epoch = parent.key_epoch
            sequence = parent.sequence + 1
            head = AuditHead(parent.latest_id, parent.latest_row_hash)
        fault = _edge_fault(
            child,
            expected_key_id=key_id,
            expected_epoch=epoch,
            expected_sequence=sequence,
            previous_head=head,
        )
        if fault is not None:
            self.rejected[child.anchor_hash] = fault
            # Emit after closure so canonical duplicate transports can improve representatives.
            return
        self.admitted[child.anchor_hash] = node
        self.parent_events.append(child.anchor_hash)
        if child.kind == "key_transition":
            next_key_id = cast(str, child.next_key_id)
            if next_key_id not in self.material:
                self.material[next_key_id] = cast(bytes, child.next_public_key)
                self.key_events.append(next_key_id)

    def close(self) -> None:
        while self.key_events or self.parent_events:
            if self.key_events:
                self.authenticate(self.key_events.popleft())
            else:
                for node in self.waiting_parents.pop(self.parent_events.popleft(), ()):
                    self.assess(node)

    def diagnose(self) -> None:
        for raws in self.waiting_keys.values():
            for raw in raws:
                self.diagnostics.add("UNKNOWN_KEY", "incomplete", (raw.digest,), (raw.index,))
        anchors: dict[tuple[str, ...], _Conflict] = {}
        forks: dict[tuple[str, ...], _Conflict] = {}
        for anchor_hash, node in self.nodes.items():
            envelope = node.envelope
            anchors.setdefault((str(envelope.anchor_id),), _Conflict()).offer(
                anchor_hash, node.index, self.orders
            )
            if anchor_hash in self.admitted:
                forks.setdefault((envelope.previous_anchor_hash,), _Conflict()).offer(
                    anchor_hash, node.index, self.orders
                )
            elif anchor_hash in self.rejected:
                self.diagnostics.add(
                    self.rejected[anchor_hash], "failed", (anchor_hash,), (node.index,)
                )
            else:
                self.diagnostics.add(
                    "DISCONNECTED_GRAPH", "incomplete", (anchor_hash,), (node.index,)
                )
        self.diagnostics.conflicts("ANCHOR_ID_CONFLICT", anchors)
        self.diagnostics.conflicts("LINEAGE_FORK", forks)

    def required_relation(self) -> _Relation:
        pin = self.enrollment.required_checkpoint
        if pin is None:
            return "not-provided"
        included = False
        offending: int | None = None
        for node in self.admitted.values():
            envelope = node.envelope
            same_hash = envelope.anchor_hash == pin.anchor_hash
            same_sequence = envelope.sequence == pin.sequence
            if same_hash and same_sequence:
                included = True
            elif same_hash or same_sequence:
                if offending is None or self.orders[node.index] < self.orders[offending]:
                    offending = node.index
        if offending is not None:
            self.diagnostics.add("REQUIRED_CHECKPOINT_CONFLICT", "failed", indexes=(offending,))
            return "conflicting"
        if included:
            return "included"
        self.diagnostics.add("REQUIRED_CHECKPOINT_MISSING", "incomplete")
        return "missing"

    def history(self) -> tuple[tuple[VerifiedEnvelope, ...], tuple[AuthorizedKeyEpoch, ...]]:
        ordered = tuple(
            sorted((node.envelope for node in self.admitted.values()), key=lambda e: e.sequence)
        )
        bootstrap = self.enrollment.bootstrap
        history = [
            AuthorizedKeyEpoch(
                bootstrap.initial_key_epoch,
                bootstrap.initial_key_id,
                bootstrap.initial_public_key,
                None,
            )
        ]
        for previous, envelope in itertools.pairwise(ordered):
            if envelope.key_epoch != previous.key_epoch:
                history.append(
                    AuthorizedKeyEpoch(
                        envelope.key_epoch,
                        envelope.key_id,
                        self.material[envelope.key_id],
                        previous.anchor_hash,
                    )
                )
        return ordered, tuple(history)


def _result(
    diagnostics: _Diagnostics,
    maximum_issues: int,
    relation: _Relation,
    *,
    duplicates: int = 0,
    ordered: tuple[VerifiedEnvelope, ...] = (),
    history: tuple[AuthorizedKeyEpoch, ...] = (),
) -> LineageEvaluation:
    failed = sum(severity == "failed" for severity, _, _ in diagnostics.groups)
    incomplete = len(diagnostics.groups) - failed
    status: Literal["consistent", "failed", "incomplete"] = (
        "failed" if failed else "incomplete" if incomplete else "consistent"
    )
    if status != "consistent":
        ordered, history = (), ()
    displayed = sorted(diagnostics.groups)[:maximum_issues]
    issues = tuple(
        LineageIssue(code, severity, diagnostics.groups[(severity, code, subject)])
        for severity, code, subject in displayed
    )
    return LineageEvaluation(
        status=status,
        scope="supplied-v2-graph",
        bootstrap_assurance="external-pin-only",
        unproved_checks=_UNPROVED,
        ordered_envelopes=ordered,
        key_history=history,
        tip_hash=ordered[-1].anchor_hash if ordered else None,
        tip_sequence=ordered[-1].sequence if ordered else None,
        issues=issues,
        issues_omitted=failed + incomplete - len(issues),
        failed_issues=failed,
        incomplete_issues=incomplete,
        duplicate_observations=duplicates,
        required_checkpoint_relation=relation,
    )


def evaluate_lineage(
    enrollment: StreamEnrollment,
    observations: tuple[EnvelopeObservation, ...],
    *,
    limits: LineageLimits,
) -> LineageEvaluation:
    """Evaluate every bounded supplied observation relative to external enrollment."""

    _preflight_shapes(enrollment, observations, limits)
    if len(observations) > limits.maximum_observations or (
        _preflight_observations(observations) > limits.maximum_total_bytes
    ):
        diagnostics = _Diagnostics([])
        diagnostics.add("RESOURCE_LIMIT", "incomplete")
        return _result(
            diagnostics,
            limits.maximum_issues,
            "unassessed" if enrollment.required_checkpoint is not None else "not-provided",
        )
    try:
        key = Ed25519PublicKey.from_public_bytes(enrollment.bootstrap.initial_public_key)
        actual_key_id = checkpoint_v2.public_key_id(key)
    except ValueError:
        _input_error()
    if actual_key_id != enrollment.bootstrap.initial_key_id:
        _input_error()

    graph = _Graph(enrollment, observations)
    if not observations:
        graph.diagnostics.add("EMPTY_GRAPH", "incomplete")
    graph.inspect()
    graph.close()
    graph.diagnose()
    relation = graph.required_relation()
    ordered: tuple[VerifiedEnvelope, ...] = ()
    history: tuple[AuthorizedKeyEpoch, ...] = ()
    if not graph.diagnostics.groups:
        ordered, history = graph.history()
    return _result(
        graph.diagnostics,
        limits.maximum_issues,
        relation,
        duplicates=graph.authenticated_observations - len(graph.nodes),
        ordered=ordered,
        history=history,
    )
