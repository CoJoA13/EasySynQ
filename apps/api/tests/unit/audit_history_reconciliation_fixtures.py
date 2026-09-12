"""Typed, capacity-limited differential fixtures in fresh owned kernel processes.

This test-only protocol is deliberately absent from the packaged executable.
It carries original domain observations, never SQL or authenticated assertions.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
from easysynq_api.services.audit import bootstrap_bridge as bridge
from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness
from easysynq_api.services.audit.history_reconciliation import (
    HistoryReconciliationError,
    HistoryReconciliationLimits,
)
from easysynq_api.services.audit.lineage import AuditHead
from tests.unit.test_audit_bootstrap_bridge import _ReferenceCase

_INPUT_MAX = 64 * 1024 * 1024
_OUTPUT_MAX = 16 * 1024 * 1024
_WORKER = Path(__file__).with_name("audit_history_reconciliation_kernel_worker.py")
_SOURCE = Path(__file__).resolve().parents[2] / "src"


def _exchange(payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if not 0 < len(raw) <= _INPUT_MAX:
        raise ValueError("kernel fixture input exceeds shared capacity")
    outgoing = memoryview(len(raw).to_bytes(4, "big") + raw)
    output, errors = bytearray(), bytearray()
    directory = tempfile.mkdtemp(prefix="easysynq-kernel-fixture-")
    process = None
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed test-only executable and source path
            [sys.executable, "-I", "-B", "-u", str(_WORKER), str(_SOURCE)],
            cwd=directory,
            env={"LANG": "C.UTF-8", "TZ": "UTC"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        assert process.stderr is not None
        deadline = time.monotonic() + 90
        with selectors.DefaultSelector() as selector:
            for pipe, mode in (
                (process.stdin, selectors.EVENT_WRITE),
                (process.stdout, selectors.EVENT_READ),
                (process.stderr, selectors.EVENT_READ),
            ):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, mode)
            offset = 0
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError("kernel fixture deadline")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    pipe = key.fileobj
                    if pipe is process.stdin:
                        try:
                            offset += os.write(pipe.fileno(), outgoing[offset : offset + 65_536])
                        except BrokenPipeError:
                            offset = len(outgoing)
                        if offset == len(outgoing):
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        part = os.read(key.fd, 65_536)
                        if not part:
                            selector.unregister(pipe)
                            pipe.close()
                        else:
                            target = output if pipe is process.stdout else errors
                            cap = _OUTPUT_MAX + 4 if target is output else 65_536
                            if len(target) + len(part) > cap:
                                raise ValueError("kernel fixture output exceeds capacity")
                            target.extend(part)
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        assert code == 0 and not errors, (code, errors.decode(errors="replace"))
        assert len(output) >= 4
        length = int.from_bytes(output[:4], "big")
        assert 0 < length <= _OUTPUT_MAX and len(output) == length + 4
    finally:
        # Do not remove a database until the sole process is proved dead/reaped.
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        shutil.rmtree(directory)
        assert not os.path.exists(directory)
    result = json.loads(output[4:])
    assert type(result) is dict
    return result


def _bridge_payload(case: _ReferenceCase, maximum_issues: int) -> dict[str, Any]:
    limits = bridge.BridgeLimits(4096, 16 * 1024 * 1024, maximum_issues)
    if not bridge._preflight(
        case.enrollment, case.root_body, case.pages, case.observations, limits
    ):
        raise ValueError("kernel fixture exceeds shared capacity")
    scope = protocol._PublicScope(
        case.enrollment,
        tuple(
            _SpoolWitness(w.witness_id, w.namespace_hash, "fixture")
            for w in sorted(case.enrollment.witnesses, key=lambda item: item.witness_id.bytes)
        ),
        HistoryReconciliationLimits(
            maximum_pages=4096,
            maximum_observations=4096,
            maximum_bridge_pages=8,
            maximum_manifest_entries=4096,
            maximum_total_bytes=16 * 1024 * 1024,
            maximum_spool_bytes=256 * 1024 * 1024,
            maximum_wall_seconds=90,
            maximum_issue_groups=100_000,
            maximum_issues=maximum_issues,
        ),
        None if case.root_body is None else len(case.root_body),
        tuple(len(page.body) for page in case.pages),
    )
    observations = []
    for item in case.observations:
        record: dict[str, Any] = {"witness": str(item.witness_id)}
        if isinstance(item, bridge.WitnessCollectionGap):
            record.update(kind="gap", reason=item.reason)
        else:
            record.update(key=item.object_key, version=item.version_id)
            if isinstance(item, bridge.LegacyBodyObservation):
                record.update(kind="body", body=item.body.hex())
            else:
                record["kind"] = (
                    "delete" if isinstance(item, bridge.LegacyDeleteObservation) else "unavailable"
                )
        observations.append(record)
    return {
        "kind": "bridge",
        "scope": protocol.scope_payload(scope),
        "root": None if case.root_body is None else case.root_body.hex(),
        "pages": [page.body.hex() for page in case.pages],
        "observations": observations,
    }


def kernel_bridge(case: _ReferenceCase, *, maximum_issues: int = 32) -> bridge.BridgeEvaluation:
    value = _exchange(_bridge_payload(case, maximum_issues))
    return _bridge_result(case, value)


def _mixed_originals() -> tuple[tuple[Any, ...], tuple[tuple[UUID, str, str, bytes], ...]]:
    """Independently rebind retained R78 vectors to admissible reader namespaces."""
    import base64
    import dataclasses
    import hashlib

    import rfc8785
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from easysynq_api.services.audit.history_collection import RequiredHistoryWitness
    from easysynq_api.services.audit.sink import ExplicitHistoryReader
    from tests.unit.test_audit_bootstrap_bridge import _reference, _reference_case, _repin

    reference = _reference()
    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    pins, readers = [], []
    # R78's frozen namespaces have an empty region, which R84 correctly rejects.
    # Change only public fixture enrollment/root bindings and resign the linked
    # v2 chain with new disposable material; keep every original legacy locator.
    for vector in reference["namespaces"]:
        namespace = {**vector["namespace"], "region": "fixture-region"}
        digest = hashlib.sha256(
            b"EasySynQ/AuditLegacyBridge/v1/namespace\0" + rfc8785.dumps(namespace)
        ).hexdigest()
        witness = UUID(vector["witness_id"])
        pins.append(bridge.BridgeWitnessPin(witness, digest))
        readers.append(
            RequiredHistoryWitness(
                witness,
                ExplicitHistoryReader(
                    namespace["endpoint"],
                    namespace["bucket"],
                    namespace["region"],
                    "fixture-access",
                    "fixture-secret",
                ),
            )
        )
        next(w for w in root["witnesses"] if w["witness_id"] == str(witness))["namespace_hash"] = (
            digest
        )
    keys = tuple(Ed25519PrivateKey.from_private_bytes(bytes([n]) * 32) for n in (113, 114))
    public = tuple(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) for k in keys)
    ids = tuple("ed25519-sha256:" + hashlib.sha256(k).hexdigest() for k in public)
    pin = dataclasses.replace(
        case.enrollment.stream.bootstrap, initial_key_id=ids[0], initial_public_key=public[0]
    )
    case = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(
            case.enrollment,
            witnesses=tuple(pins),
            stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin),
        ),
    )
    root.update(initial_key_id=ids[0], initial_public_key=base64.b64encode(public[0]).decode())
    case = _repin(case, root=root)
    observations = [
        (o.witness_id, o.object_key, o.version_id, o.body)
        for o in case.observations
        if isinstance(o, bridge.LegacyBodyObservation)
    ]
    assert len(observations) == len(case.observations)
    previous = case.enrollment.stream.bootstrap.commitment_hash
    for i, vector in enumerate(reference["v2_composition"]):
        checkpoint = json.loads(bytes.fromhex(vector["body_hex"]))["checkpoint"]
        material = 1 if i == 2 else 0
        checkpoint.update(previous_anchor_hash=previous, key_id=ids[material])
        if checkpoint["kind"] == "key_transition":
            checkpoint.update(
                next_key_id=ids[1], next_public_key=base64.b64encode(public[1]).decode()
            )
            proof = {k: v for k, v in checkpoint.items() if k != "next_key_signature"}
            checkpoint["next_key_signature"] = base64.b64encode(
                keys[1].sign(
                    b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0" + rfc8785.dumps(proof)
                )
            ).decode()
        canonical = rfc8785.dumps(checkpoint)
        signature = keys[material].sign(b"EasySynQ/AuditCheckpoint/v2/signature\0" + canonical)
        previous = hashlib.sha256(
            b"EasySynQ/AuditCheckpoint/v2/hash\0" + canonical + signature
        ).hexdigest()
        raw = rfc8785.dumps(
            {
                "checkpoint": checkpoint,
                "signature": base64.b64encode(signature).decode(),
                "anchor_hash": previous,
            }
        )
        for witness in pins:
            observations.append(
                (
                    witness.witness_id,
                    f"checkpoints/{case.enrollment.stream.org_id}/v2-node-{i}",
                    "v2-version",
                    raw,
                )
            )
    limits = HistoryReconciliationLimits(8, 4096, 8, 4096, 16_777_216, 67_108_864, 90, 100_000, 32)
    return (
        (case.enrollment, case.root_body, case.pages, tuple(readers), limits),
        tuple(observations),
    )


def mixed_case() -> tuple[Any, ...]:
    return _mixed_originals()[0]


def _bridge_result(case: _ReferenceCase, value: dict[str, Any]) -> bridge.BridgeEvaluation:
    if set(value) == {"error"}:
        raise HistoryReconciliationError(value["error"])
    # Only map typed output fields and original ordinal indexes; no verdict is repaired.
    return bridge.BridgeEvaluation(
        value["status"],
        value["scope"],
        case.enrollment.stream.bootstrap if value["usable"] else None,
        tuple(
            bridge.BridgeWitnessSummary(
                UUID(w["witness_id"]),
                w["committed_locators"],
                AuditHead(**w["lowest_head"]),
                AuditHead(**w["highest_head"]),
            )
            for w in value["witness_summaries"]
        ),
        tuple(
            bridge.BridgeIssue(
                i["code"], i["severity"], tuple(i["observation_indexes"]), tuple(i["page_indexes"])
            )
            for i in value["issues"]
        ),
        value["failed_issues"],
        value["incomplete_issues"],
        value["issues_omitted"],
        value["duplicate_body_observations"],
        value["duplicate_page_observations"],
        tuple(value["established_checks"]),
        tuple(value["unproved_checks"]),
    )


def _lineage_payload(enrollment: Any, observations: Any, maximum_issues: int) -> dict[str, Any]:
    import dataclasses

    from easysynq_api.services.audit import lineage

    limits = lineage.LineageLimits(4096, 16_777_216, maximum_issues)
    lineage._preflight_shapes(enrollment, observations, limits)
    if len(observations) > 4096 or lineage._preflight_observations(observations) > 16_777_216:
        raise ValueError("kernel fixture exceeds shared capacity")
    stream = dataclasses.asdict(enrollment)
    stream["org_id"], stream["stream_id"] = str(stream["org_id"]), str(stream["stream_id"])
    stream["bootstrap"]["initial_public_key"] = stream["bootstrap"]["initial_public_key"].hex()
    return {
        "kind": "lineage",
        "enrollment": stream,
        "maximum_issues": maximum_issues,
        "observations": [
            {
                "source_id": o.source_id,
                "object_key": o.object_key,
                "version_id": o.version_id,
                "body": o.body.hex(),
            }
            for o in observations
        ],
    }


def _project_envelope(raw: bytes) -> Any:
    """Restore fields of an already kernel-verified path node, without reauthentication."""
    import dataclasses

    import rfc8785

    from easysynq_api.services.audit import checkpoint_v2 as codec

    checkpoint, signature, anchor_hash = codec._envelope_parts(raw)
    fields = codec._validate_checkpoint(checkpoint, current_public_key=None, proof_required=True)
    return codec.VerifiedEnvelope(
        format_version=2,
        **dataclasses.asdict(fields),
        signature=signature,
        anchor_hash=anchor_hash,
        canonical_checkpoint=rfc8785.dumps(checkpoint),
        canonical_envelope=raw,
    )


def kernel_lineage(enrollment: Any, observations: Any, *, maximum_issues: int = 32) -> Any:
    return _lineage_result(_exchange(_lineage_payload(enrollment, observations, maximum_issues)))


def _lineage_result(value: dict[str, Any]) -> Any:
    from easysynq_api.services.audit import lineage

    if set(value) == {"error"}:
        raise HistoryReconciliationError(value["error"])
    return lineage.LineageEvaluation(
        value["status"],
        value["scope"],
        value["bootstrap_assurance"],
        tuple(value["unproved_checks"]),
        tuple(_project_envelope(bytes.fromhex(raw)) for raw in value["path"]),
        tuple(
            lineage.AuthorizedKeyEpoch(
                k["key_epoch"], k["key_id"], bytes.fromhex(k["public_key"]), k["introduced_by"]
            )
            for k in value["key_history"]
        ),
        value["tip_hash"],
        value["tip_sequence"],
        tuple(
            lineage.LineageIssue(i["code"], i["severity"], tuple(i["observation_indexes"]))
            for i in value["issues"]
        ),
        value["issues_omitted"],
        value["failed_issues"],
        value["incomplete_issues"],
        value["duplicate_observations"],
        value["required_checkpoint_relation"],
    )
