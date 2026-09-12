"""Version-two public scope. No reader, credentials, SQL, path or caller cursor."""

from __future__ import annotations

import dataclasses
import re
from typing import Any
from uuid import UUID

from . import _history_spool_protocol as wire
from . import bootstrap_bridge as bridge
from . import history_reconciliation as public
from . import lineage
from ._history_spool_protocol import _SpoolWitness
from .history_collection import HistoryCollectionLimits

VERSION = 2
RESULT_MAX = 65_536
ROOT_MAX = 262_144
BRIDGE_PAGE_MAX = 2_097_152
PHASES = (
    "package-root",
    "package-pages",
    "manifest-partition",
    "manifest-duplicates",
    "manifest-order",
    "manifest-counts",
    "body-index",
    "legacy-auth",
    "legacy-membership",
    "legacy-heads",
    "legacy-summaries",
    "v2-route",
    "v2-events",
    "v2-diagnostics",
    "v2-required",
    "v2-path",
    "witness-coverage",
    "global-heads",
    "issues",
    "ready-to-finish",
)


@dataclasses.dataclass(frozen=True, slots=True)
class _PublicScope:
    enrollment: bridge.BridgeEnrollment
    witnesses: tuple[_SpoolWitness, ...]
    limits: public.HistoryReconciliationLimits
    root_length: int | None
    page_lengths: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Progress:
    phase: str
    completed_work: int
    done: bool


type _RawResult = public.HistoryReconciliationReport


def collection_limits(limits: public.HistoryReconciliationLimits) -> HistoryCollectionLimits:
    return HistoryCollectionLimits(
        limits.maximum_pages,
        limits.maximum_observations,
        limits.maximum_total_bytes,
        limits.maximum_spool_bytes,
        limits.maximum_wall_seconds,
        limits.maximum_issues,
    )


def _uuid(value: Any) -> UUID:
    if type(value) is not str:
        wire.invalid()
    try:
        result = UUID(value)
    except ValueError:
        wire.invalid()
    if str(result) != value:
        wire.invalid()
    return result


def _key_bytes(value: Any) -> bytes:
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        wire.invalid()
    return bytes.fromhex(value)


def _enrollment(value: Any) -> bridge.BridgeEnrollment:
    wire.fields(value, {"stream", "witnesses", "legacy_keys"})
    stream = wire.fields(
        value["stream"], {"org_id", "stream_id", "bootstrap", "required_checkpoint"}
    )
    pin = wire.fields(
        stream["bootstrap"],
        {
            "commitment_hash",
            "initial_key_id",
            "initial_public_key",
            "initial_key_epoch",
            "audit_boundary",
        },
    )
    boundary = wire.fields(pin["audit_boundary"], {"latest_id", "latest_row_hash"})
    required = stream["required_checkpoint"]
    if required is not None:
        wire.fields(required, {"anchor_hash", "sequence"})
        required = lineage.RequiredCheckpointPin(**required)
    witnesses, keys = value["witnesses"], value["legacy_keys"]
    if (
        type(witnesses) is not list
        or not 1 <= len(witnesses) <= 4
        or type(keys) is not list
        or len(keys) > 8
    ):
        wire.invalid()
    pins = []
    for item in witnesses:
        wire.fields(item, {"witness_id", "namespace_hash"})
        pins.append(bridge.BridgeWitnessPin(_uuid(item["witness_id"]), item["namespace_hash"]))
    materials = []
    for item in keys:
        wire.fields(item, {"key_id", "public_key"})
        materials.append(
            bridge.LegacyPublicMaterial(item["key_id"], _key_bytes(item["public_key"]))
        )
    enrollment = bridge.BridgeEnrollment(
        lineage.StreamEnrollment(
            _uuid(stream["org_id"]),
            _uuid(stream["stream_id"]),
            lineage.BootstrapPin(
                pin["commitment_hash"],
                pin["initial_key_id"],
                _key_bytes(pin["initial_public_key"]),
                pin["initial_key_epoch"],
                lineage.AuditHead(**boundary),
            ),
            required,
        ),
        tuple(pins),
        tuple(materials),
    )
    try:
        public._enrollment_shapes(enrollment)
        bridge._admit(enrollment)
    except (public.HistoryReconciliationInputError, bridge.BridgeInputError) as error:
        if type(error) not in (public.HistoryReconciliationInputError, bridge.BridgeInputError):
            raise
        wire.invalid()
    return enrollment


def decode_init(value: dict[str, Any]) -> _PublicScope:
    wire.fields(
        value,
        {"op", "id", "version", "enrollment", "witnesses", "limits", "root_length", "page_lengths"},
    )
    if (
        value["op"] != "INIT"
        or wire.integer(value["id"], 1, 1) != 1
        or wire.integer(value["version"], VERSION, VERSION) != VERSION
    ):
        wire.invalid()
    raw_limits = wire.fields(
        value["limits"],
        {field.name for field in dataclasses.fields(public.HistoryReconciliationLimits)},
    )
    limits = public.HistoryReconciliationLimits(**raw_limits)
    try:
        public._validate_limits(limits)
    except public.HistoryReconciliationInputError as error:
        if type(error) is not public.HistoryReconciliationInputError:
            raise
        wire.invalid()
    root_length = value["root_length"]
    if root_length is not None:
        wire.integer(root_length, 0, limits.maximum_total_bytes)
    pages = value["page_lengths"]
    if type(pages) is not list or len(pages) > limits.maximum_bridge_pages:
        wire.invalid()
    for length in pages:
        wire.integer(length, 0, limits.maximum_total_bytes)
    if (root_length or 0) + sum(pages) > limits.maximum_total_bytes:
        wire.invalid()
    enrollment = _enrollment(value["enrollment"])
    _, witnesses, _ = wire.decode_init(
        {
            "op": "INIT",
            "id": 1,
            "version": 1,
            "org_id": str(enrollment.stream.org_id),
            "witnesses": value["witnesses"],
            "limits": dataclasses.asdict(collection_limits(limits)),
        }
    )
    if tuple((w.witness_id, w.namespace_hash) for w in witnesses) != tuple(
        (w.witness_id, w.namespace_hash)
        for w in sorted(enrollment.witnesses, key=lambda w: w.witness_id.bytes)
    ):
        wire.invalid()
    return _PublicScope(enrollment, witnesses, limits, root_length, tuple(pages))


def scope_payload(scope: _PublicScope) -> dict[str, Any]:
    enrollment = dataclasses.asdict(scope.enrollment)
    stream = enrollment["stream"]
    stream["org_id"], stream["stream_id"] = str(stream["org_id"]), str(stream["stream_id"])
    stream["bootstrap"]["initial_public_key"] = stream["bootstrap"]["initial_public_key"].hex()
    for witness in enrollment["witnesses"]:
        witness["witness_id"] = str(witness["witness_id"])
    for material in enrollment["legacy_keys"]:
        material["public_key"] = material["public_key"].hex()
    return {
        "version": VERSION,
        "enrollment": enrollment,
        "witnesses": [
            {
                "witness_id": str(w.witness_id),
                "namespace_hash": w.namespace_hash,
                "bucket": w.bucket,
            }
            for w in scope.witnesses
        ],
        "limits": dataclasses.asdict(scope.limits),
        "root_length": scope.root_length,
        "page_lengths": list(scope.page_lengths),
    }


def decode_progress(value: Any) -> _Progress:
    wire.fields(value, {"phase", "completed_work", "done"})
    if (
        type(value["phase"]) is not str
        or value["phase"] not in PHASES
        or type(value["done"]) is not bool
    ):
        wire.invalid()
    if value["done"] != (value["phase"] == "ready-to-finish"):
        wire.invalid()
    return _Progress(
        value["phase"], wire.integer(value["completed_work"], 0, 2_147_483_647), value["done"]
    )
