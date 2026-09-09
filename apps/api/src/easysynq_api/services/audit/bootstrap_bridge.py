"""Pure bounded reconciliation of an externally pinned supplied legacy package.

Consistency proves neither collection completeness nor DB agreement, custody,
freshness, rollback-memory continuity or activation. No consumer is enrolled here.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
from typing import Literal, NoReturn, cast
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from easysynq_api.services.audit import bootstrap_bridge_codec as _codec
from easysynq_api.services.audit import checkpoint_v2
from easysynq_api.services.audit import legacy_checkpoint_compat as _legacy
from easysynq_api.services.audit.lineage import (
    AuditHead,
    BootstrapPin,
    RequiredCheckpointPin,
    StreamEnrollment,
)

_BODY_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/raw-body\0"
_MAX_BIGINT = 9_223_372_036_854_775_807
_ESTABLISHED = (
    "external-root-content-binding",
    "committed-page-and-locator-closure",
    "retained-legacy-signature-authentication",
    "supplied-observation-reconciliation",
    "per-witness-signed-boundary-binding",
)
_UNPROVED = (
    "operational-legacy-history-completeness",
    "witness-collection-completeness",
    "witness-custody",
    "audit-chain-comparison",
    "v2-lineage-consistency",
    "freshness",
    "rollback-memory-continuity",
    "operational-key-activation",
)
_INCOMPLETE = frozenset(
    {
        "RESOURCE_LIMIT",
        "ROOT_MISSING",
        "LEGACY_KEY_MISSING",
        "PAGE_MISSING",
        "LEGACY_BODY_UNAVAILABLE",
        "WITNESS_COLLECTION_GAP",
        "LEGACY_AUTHENTICATION_UNESTABLISHED",
        "LEGACY_BODY_MISSING",
    }
)
type _Severity = Literal["failed", "incomplete"]
type _Locator = tuple[UUID, str, str]
type _Order = tuple[str, str, str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class LegacyPublicMaterial:
    key_id: str
    public_key: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeWitnessPin:
    witness_id: UUID
    namespace_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeEnrollment:
    stream: StreamEnrollment
    witnesses: tuple[BridgeWitnessPin, ...]
    legacy_keys: tuple[LegacyPublicMaterial, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class BridgePageObservation:
    body: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class LegacyBodyObservation:
    witness_id: UUID
    object_key: str
    version_id: str
    body: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class LegacyUnavailableObservation:
    witness_id: UUID
    object_key: str
    version_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class LegacyDeleteObservation:
    witness_id: UUID
    object_key: str
    version_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class WitnessCollectionGap:
    witness_id: UUID
    reason: Literal["listing-unavailable", "pagination-incomplete", "collection-limit"]


type LegacyObservation = (
    LegacyBodyObservation
    | LegacyUnavailableObservation
    | LegacyDeleteObservation
    | WitnessCollectionGap
)


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeLimits:
    maximum_observations: int
    maximum_total_bytes: int
    maximum_issues: int


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeWitnessSummary:
    witness_id: UUID
    committed_locators: int
    lowest_head: AuditHead
    highest_head: AuditHead


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeIssue:
    code: str
    severity: _Severity
    observation_indexes: tuple[int, ...]
    page_indexes: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeEvaluation:
    status: Literal["consistent", "failed", "incomplete"]
    scope: Literal["supplied-legacy-bootstrap-package"]
    usable_bootstrap_pin: BootstrapPin | None
    witness_summaries: tuple[BridgeWitnessSummary, ...]
    issues: tuple[BridgeIssue, ...]
    failed_issues: int
    incomplete_issues: int
    issues_omitted: int
    duplicate_body_observations: int
    duplicate_page_observations: int
    established_checks: tuple[str, ...]
    unproved_checks: tuple[str, ...]


class BridgeInputError(ValueError):
    """Fixed-text rejection of caller structure or external material."""


def _input_error() -> NoReturn:
    raise BridgeInputError("invalid bootstrap bridge input") from None


def _integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _input_error()


def _digest(value: object, *, key: bool = False) -> None:
    if type(value) is not str or (_codec._KEY_ID if key else _codec._HEX).fullmatch(value) is None:
        _input_error()


def _head(value: object) -> None:
    if type(value) is not AuditHead:
        _input_error()
    _integer(value.latest_id, 1, _MAX_BIGINT)
    _digest(value.latest_row_hash)


def _preflight(
    enrollment: BridgeEnrollment,
    root: bytes | None,
    pages: tuple[BridgePageObservation, ...],
    observations: tuple[LegacyObservation, ...],
    limits: BridgeLimits,
) -> bool:
    if (
        type(enrollment) is not BridgeEnrollment
        or type(limits) is not BridgeLimits
        or type(pages) is not tuple
        or type(observations) is not tuple
        or (root is not None and type(root) is not bytes)
    ):
        _input_error()
    _integer(limits.maximum_observations, 1, 4096)
    _integer(limits.maximum_total_bytes, 1, 16777216)
    _integer(limits.maximum_issues, 1, 32)
    stream = enrollment.stream
    if (
        type(stream) is not StreamEnrollment
        or type(stream.org_id) is not UUID
        or type(stream.stream_id) is not UUID
    ):
        _input_error()
    pin = stream.bootstrap
    if type(pin) is not BootstrapPin:
        _input_error()
    _digest(pin.commitment_hash)
    _digest(pin.initial_key_id, key=True)
    if type(pin.initial_public_key) is not bytes or len(pin.initial_public_key) != 32:
        _input_error()
    _integer(pin.initial_key_epoch, 0, _MAX_BIGINT)
    _head(pin.audit_boundary)
    required = stream.required_checkpoint
    if required is not None:
        if type(required) is not RequiredCheckpointPin:
            _input_error()
        _digest(required.anchor_hash)
        _integer(required.sequence, 1, _MAX_BIGINT)
    if type(enrollment.witnesses) is not tuple or not 1 <= len(enrollment.witnesses) <= 4:
        _input_error()
    if type(enrollment.legacy_keys) is not tuple or len(enrollment.legacy_keys) > 8:
        _input_error()
    for witness in enrollment.witnesses:
        if type(witness) is not BridgeWitnessPin or type(witness.witness_id) is not UUID:
            _input_error()
        _digest(witness.namespace_hash)
    if len({item.witness_id for item in enrollment.witnesses}) != len(enrollment.witnesses):
        _input_error()
    for material in enrollment.legacy_keys:
        if (
            type(material) is not LegacyPublicMaterial
            or type(material.public_key) is not bytes
            or len(material.public_key) != 32
        ):
            _input_error()
        _digest(material.key_id, key=True)
    if len({item.key_id for item in enrollment.legacy_keys}) != len(enrollment.legacy_keys):
        _input_error()
    # Count excess intentionally precedes tuple-element inspection and all crypto.
    if len(observations) > limits.maximum_observations or len(pages) > 8:
        return False
    total = 0 if root is None else len(root)
    for page in pages:
        if type(page) is not BridgePageObservation or type(page.body) is not bytes:
            _input_error()
        total += len(page.body)
    for observation in observations:
        if (
            type(observation)
            not in (
                LegacyBodyObservation,
                LegacyUnavailableObservation,
                LegacyDeleteObservation,
                WitnessCollectionGap,
            )
            or type(observation.witness_id) is not UUID
        ):
            _input_error()
        if isinstance(observation, WitnessCollectionGap):
            if type(observation.reason) is not str or observation.reason not in (
                "listing-unavailable",
                "pagination-incomplete",
                "collection-limit",
            ):
                _input_error()
        else:
            if not _codec._label(observation.object_key) or not _codec._label(
                observation.version_id
            ):
                _input_error()
            if isinstance(observation, LegacyBodyObservation):
                if type(observation.body) is not bytes:
                    _input_error()
                total += len(observation.body)
    return total <= limits.maximum_total_bytes


def _admit(enrollment: BridgeEnrollment) -> tuple[Ed25519PublicKey, ...]:
    pin = enrollment.stream.bootstrap
    try:
        initial = Ed25519PublicKey.from_public_bytes(pin.initial_public_key)
        if checkpoint_v2.public_key_id(initial) != pin.initial_key_id:
            _input_error()
        keys = []
        for material in sorted(enrollment.legacy_keys, key=lambda item: item.key_id):
            if (
                "ed25519-sha256:" + hashlib.sha256(material.public_key).hexdigest()
                != material.key_id
            ):
                _input_error()
            keys.append(Ed25519PublicKey.from_public_bytes(material.public_key))
    except ValueError:
        _input_error()
    return tuple(keys)


@dataclasses.dataclass(frozen=True, slots=True)
class _Ref:
    order: _Order
    index: int
    page: bool = False


class _Issues:
    def __init__(self) -> None:
        self.groups: dict[tuple[_Severity, str, tuple[str, ...]], tuple[_Ref, ...]] = {}

    def add(self, code: str, subject: tuple[str, ...] = (), refs: tuple[_Ref, ...] = ()) -> None:
        severity: _Severity = "incomplete" if code in _INCOMPLETE else "failed"
        key = (severity, code, subject)
        # Keep distinct values; repeated delivery cannot crowd out a contradiction.
        distinct: dict[tuple[bool, _Order], _Ref] = {}
        for ref in self.groups.get(key, ()) + refs:
            identity = (ref.page, ref.order)
            if identity not in distinct or ref.index < distinct[identity].index:
                distinct[identity] = ref
        maximum = (
            2
            if code
            in {
                "PAGE_CONFLICT",
                "MANIFEST_PARTITION_INVALID",
                "MANIFEST_LOCATOR_DUPLICATE",
                "MANIFEST_ORDER_INVALID",
                "MANIFEST_COUNT_MISMATCH",
                "IMMUTABLE_LOCATOR_CONFLICT",
                "SIGNED_HEAD_CONFLICT",
                "WITNESS_SUMMARY_MISMATCH",
            }
            else 1
        )
        self.groups[key] = tuple(
            sorted(distinct.values(), key=lambda ref: (ref.order, ref.index))[:maximum]
        )

    def result(
        self,
        pin: BootstrapPin,
        limits: BridgeLimits,
        summaries: tuple[BridgeWitnessSummary, ...] = (),
        duplicate_bodies: int = 0,
        duplicate_pages: int = 0,
    ) -> BridgeEvaluation:
        ordered = sorted(self.groups.items())
        failed = sum(key[0] == "failed" for key, _ in ordered)
        incomplete = len(ordered) - failed
        displayed = tuple(
            BridgeIssue(
                code,
                severity,
                tuple(ref.index for ref in refs if not ref.page),
                tuple(ref.index for ref in refs if ref.page),
            )
            for (severity, code, _), refs in ordered[: limits.maximum_issues]
        )
        status: Literal["consistent", "failed", "incomplete"] = (
            "failed" if failed else "incomplete" if incomplete else "consistent"
        )
        return BridgeEvaluation(
            status,
            "supplied-legacy-bootstrap-package",
            pin if not ordered else None,
            summaries if not ordered else (),
            displayed,
            failed,
            incomplete,
            max(0, len(ordered) - limits.maximum_issues),
            duplicate_bodies,
            duplicate_pages,
            _ESTABLISHED if not ordered else (),
            _UNPROVED,
        )


def _subject(locator: _Locator) -> tuple[str, ...]:
    return (str(locator[0]), locator[1], locator[2])


def _scoped(locator: _Locator, enrollment: BridgeEnrollment) -> bool:
    name = locator[1].rsplit("/", 1)[-1]
    prefix, separator, _ = name.partition("-")
    return (
        locator[0] in {item.witness_id for item in enrollment.witnesses}
        and locator[1].startswith(f"checkpoints/{enrollment.stream.org_id}/")
        and bool(separator and prefix and all("0" <= char <= "9" for char in prefix))
    )


def _root(raw: bytes | None, enrollment: BridgeEnrollment, issues: _Issues) -> _codec._Root | None:
    if raw is None:
        issues.add("ROOT_MISSING")
        return None
    try:
        root = _codec._decode_root(raw)
    except _codec._Invalid:
        issues.add("ROOT_INVALID")
        return None
    pin = enrollment.stream.bootstrap
    if root.commitment_hash != pin.commitment_hash:
        issues.add("ROOT_COMMITMENT_MISMATCH")
        return None
    if (
        root.org_id != enrollment.stream.org_id
        or root.stream_id != enrollment.stream.stream_id
        or root.initial_key_id != pin.initial_key_id
        or root.initial_public_key != pin.initial_public_key
        or root.initial_key_epoch != pin.initial_key_epoch
        or root.audit_boundary != pin.audit_boundary
        or {(item.witness_id, item.namespace_hash) for item in root.witnesses}
        != {(item.witness_id, item.namespace_hash) for item in enrollment.witnesses}
    ):
        issues.add("ROOT_ENROLLMENT_MISMATCH")
        return None
    return root


def _manifest(
    root: _codec._Root | None,
    pages: tuple[BridgePageObservation, ...],
    enrollment: BridgeEnrollment,
    issues: _Issues,
) -> tuple[dict[_Locator, _codec._Entry] | None, int]:
    by_hash: dict[str, tuple[_codec._Page, _Ref]] = {}
    by_index: dict[int, list[_Ref]] = {}
    duplicates = 0
    for index, observation in enumerate(pages):
        try:
            page = _codec._decode_page(observation.body)
        except _codec._Invalid:
            digest = hashlib.sha256(observation.body).hexdigest()
            issues.add("PAGE_INVALID", (digest,), (_Ref(("", digest, "", ""), index, True),))
            continue
        ref = _Ref((f"{page.page_index:04d}", page.page_hash, "", ""), index, True)
        if page.org_id != enrollment.stream.org_id or page.stream_id != enrollment.stream.stream_id:
            issues.add("PAGE_IDENTITY_MISMATCH", (page.page_hash,), (ref,))
            continue
        if page.page_hash in by_hash:
            duplicates += 1
            continue
        by_hash[page.page_hash] = (page, ref)
        by_index.setdefault(page.page_index, []).append(ref)
        if root is not None and page.page_hash not in {item.page_hash for item in root.pages}:
            issues.add("PAGE_UNLISTED", (page.page_hash,), (ref,))
    if root is None:
        return None, duplicates
    for index, refs in by_index.items():
        if len(refs) > 1:
            issues.add("PAGE_CONFLICT", (f"{index:04d}",), tuple(refs))
    missing = False
    for required in root.pages:
        if required.page_hash not in by_hash:
            missing = True
            issues.add("PAGE_MISSING", (f"{required.page_index:04d}",))
    if missing:
        return None, duplicates
    selected = [by_hash[item.page_hash] for item in root.pages]
    partition_refs = []
    for index, ((page, ref), required) in enumerate(zip(selected, root.pages, strict=True)):
        if (
            page.page_index != index
            or len(page.entries) != required.entry_count
            or (index < len(selected) - 1 and len(page.entries) != 512)
        ):
            partition_refs.append(ref)
    if partition_refs:
        issues.add("MANIFEST_PARTITION_INVALID", refs=tuple(partition_refs))
        return None, duplicates
    indexed: dict[_Locator, list[tuple[_codec._Entry, _Ref]]] = {}
    ordered_entries = []
    for page, ref in selected:
        for entry in page.entries:
            indexed.setdefault(entry.locator, []).append((entry, ref))
            ordered_entries.append((entry, ref))
    duplicate_locators = False
    for locator, values in indexed.items():
        if len(values) > 1:
            duplicate_locators = True
            issues.add(
                "MANIFEST_LOCATOR_DUPLICATE", _subject(locator), tuple(ref for _, ref in values)
            )
    if duplicate_locators:
        return None, duplicates
    order_refs: list[_Ref] = []
    for (left, left_ref), (right, right_ref) in itertools.pairwise(ordered_entries):
        if _subject(left.locator) >= _subject(right.locator):
            order_refs.extend((left_ref, right_ref))
    if order_refs:
        issues.add("MANIFEST_ORDER_INVALID", refs=tuple(order_refs))
        return None, duplicates
    counts: dict[UUID, int] = {}
    for locator in indexed:
        counts[locator[0]] = counts.get(locator[0], 0) + 1
    if len(indexed) != root.entry_count or counts != {
        item.witness_id: item.entry_count for item in root.witnesses
    }:
        issues.add("MANIFEST_COUNT_MISMATCH", refs=tuple(ref for _, ref in selected))
        return None, duplicates
    return {locator: values[0][0] for locator, values in indexed.items()}, duplicates


@dataclasses.dataclass(frozen=True, slots=True)
class _Body:
    digest: str
    head: AuditHead | None
    issue: str | None


def _body(
    raw: bytes, org_id: UUID, keys: tuple[Ed25519PublicKey, ...], keys_complete: bool
) -> _Body:
    digest = hashlib.sha256(_BODY_DOMAIN + raw).hexdigest()
    try:
        payload = _legacy._decode(raw, org_id)
    except _legacy._InvalidLegacy:
        return _Body(digest, None, "LEGACY_BODY_INVALID")
    if _legacy._authenticate(payload, keys):
        return _Body(digest, payload.head, None)
    return _Body(
        digest,
        None,
        "LEGACY_AUTHENTICATION_FAILED" if keys_complete else "LEGACY_AUTHENTICATION_UNESTABLISHED",
    )


def evaluate_bootstrap_bridge(
    enrollment: BridgeEnrollment,
    root_body: bytes | None,
    pages: tuple[BridgePageObservation, ...],
    observations: tuple[LegacyObservation, ...],
    *,
    limits: BridgeLimits,
) -> BridgeEvaluation:
    """Evaluate all supplied evidence; only zero issues releases the original external pin."""
    issues = _Issues()
    pin = (
        enrollment.stream.bootstrap
        if type(enrollment) is BridgeEnrollment and type(enrollment.stream) is StreamEnrollment
        else None
    )
    if not _preflight(enrollment, root_body, pages, observations, limits):
        issues.add("RESOURCE_LIMIT")
        return issues.result(cast(BootstrapPin, pin), limits)
    keys = _admit(enrollment)
    pin = enrollment.stream.bootstrap
    root = _root(root_body, enrollment, issues)
    if root is not None and root.entry_count > 4096:
        issues.add("RESOURCE_LIMIT")
        return issues.result(pin, limits)
    keys_complete = False
    if root is not None:
        supplied = {item.key_id for item in enrollment.legacy_keys}
        for key_id in set(root.legacy_key_ids) - supplied:
            issues.add("LEGACY_KEY_MISSING", (key_id,))
        for key_id in supplied - set(root.legacy_key_ids):
            issues.add("LEGACY_KEYSET_MISMATCH", (key_id,))
        keys_complete = set(root.legacy_key_ids) <= supplied
    manifest, duplicate_pages = _manifest(root, pages, enrollment, issues)
    cache: dict[bytes, _Body] = {}
    locators: dict[_Locator, dict[bytes, list[_Ref]]] = {}
    heads: dict[int, dict[str, list[tuple[UUID, _Ref]]]] = {}
    duplicate_bodies = 0
    for index, observation in enumerate(observations):
        if isinstance(observation, WitnessCollectionGap):
            ref = _Ref((str(observation.witness_id), "", "", ""), index)
            if observation.witness_id not in {item.witness_id for item in enrollment.witnesses}:
                issues.add("OBSERVATION_SCOPE_MISMATCH", (str(observation.witness_id),), (ref,))
            else:
                issues.add(
                    "WITNESS_COLLECTION_GAP",
                    (str(observation.witness_id), observation.reason),
                    (ref,),
                )
            continue
        locator = (observation.witness_id, observation.object_key, observation.version_id)
        order = (str(locator[0]), locator[1], locator[2], "")
        ref = _Ref(order, index)
        if not _scoped(locator, enrollment):
            issues.add("OBSERVATION_SCOPE_MISMATCH", _subject(locator), (ref,))
            continue
        if isinstance(observation, LegacyUnavailableObservation):
            issues.add("LEGACY_BODY_UNAVAILABLE", _subject(locator), (ref,))
            continue
        if isinstance(observation, LegacyDeleteObservation):
            issues.add("LEGACY_DELETE_MARKER", _subject(locator), (ref,))
            continue
        raw = observation.body
        if raw not in cache:
            cache[raw] = _body(raw, enrollment.stream.org_id, keys, keys_complete)
        body = cache[raw]
        ref = _Ref((order[0], order[1], order[2], body.digest), index)
        bodies = locators.setdefault(locator, {})
        if raw in bodies and body.head is not None:
            duplicate_bodies += 1
        bodies.setdefault(raw, []).append(ref)
        if body.issue is not None:
            issues.add(body.issue, (body.digest,), (ref,))
        if body.head is not None:
            heads.setdefault(body.head.latest_id, {}).setdefault(
                body.head.latest_row_hash, []
            ).append((locator[0], ref))
            if manifest is not None and locator not in manifest:
                issues.add("UNLISTED_AUTHENTIC_LEGACY", _subject(locator), (ref,))
    for locator, bodies in locators.items():
        if len(bodies) > 1:
            issues.add(
                "IMMUTABLE_LOCATOR_CONFLICT",
                _subject(locator),
                tuple(refs[0] for refs in bodies.values()),
            )
    if manifest is not None:
        for locator, entry in manifest.items():
            supplied_bodies = locators.get(locator)
            if not supplied_bodies:
                issues.add("LEGACY_BODY_MISSING", _subject(locator))
            else:
                for raw, refs in supplied_bodies.items():
                    if len(raw) != entry.body_bytes or cache[raw].digest != entry.body_hash:
                        issues.add("LEGACY_BODY_COMMITMENT_MISMATCH", _subject(locator), (refs[0],))
    conflicted_witnesses: set[UUID] = set()
    boundary = cast(AuditHead, pin.audit_boundary)
    for audit_id, hashes in heads.items():
        external_conflict = audit_id == boundary.latest_id and set(hashes) != {
            boundary.latest_row_hash
        }
        if len(hashes) > 1 or external_conflict:
            representatives = tuple(
                min((ref for _, ref in refs), key=lambda ref: (ref.order, ref.index))
                for refs in hashes.values()
            )
            issues.add("SIGNED_HEAD_CONFLICT", (f"{audit_id:019d}",), representatives)
            conflicted_witnesses.update(witness for refs in hashes.values() for witness, _ in refs)
        if audit_id > boundary.latest_id:
            ahead_refs = tuple(ref for values in hashes.values() for _, ref in values)
            issues.add(
                "ABOVE_BOOTSTRAP_BOUNDARY",
                (f"{audit_id:019d}",),
                (min(ahead_refs, key=lambda ref: (ref.order, ref.index)),),
            )
    summaries = []
    if root is not None and manifest is not None:
        for witness in root.witnesses:
            endpoints: list[tuple[AuditHead, _Ref]] = []
            valid = witness.witness_id not in conflicted_witnesses
            for locator, entry in manifest.items():
                if locator[0] != witness.witness_id:
                    continue
                bodies = locators.get(locator, {})
                if len(bodies) != 1:
                    valid = False
                    continue
                raw, refs = next(iter(bodies.items()))
                body = cache[raw]
                if (
                    len(raw) != entry.body_bytes
                    or body.digest != entry.body_hash
                    or body.head is None
                ):
                    valid = False
                else:
                    endpoints.append((body.head, refs[0]))
            if not valid or not endpoints:
                continue
            endpoints.sort(key=lambda item: (item[0].latest_id, item[1].order, item[1].index))
            low, high = endpoints[0][0], endpoints[-1][0]
            high_ref = min(
                (ref for head, ref in endpoints if head == high),
                key=lambda ref: (ref.order, ref.index),
            )
            if (
                len(endpoints) != witness.entry_count
                or low != witness.lowest_head
                or high != witness.highest_head
                or high != boundary
            ):
                issues.add(
                    "WITNESS_SUMMARY_MISMATCH",
                    (str(witness.witness_id),),
                    (endpoints[0][1], high_ref),
                )
            summaries.append(BridgeWitnessSummary(witness.witness_id, len(endpoints), low, high))
    return issues.result(pin, limits, tuple(summaries), duplicate_bodies, duplicate_pages)
