"""Public mixed evidence, real owned worker, and deterministic original transport."""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from easysynq_api.services.audit import history_reconciliation as public
from tests.unit.audit_history_reconciliation_fixtures import _mixed_originals, mixed_case

pytestmark = pytest.mark.unit

_ESTABLISHED = (
    "required-witness-provider-traversal",
    "external-root-content-binding",
    "committed-page-and-locator-closure",
    "retained-legacy-signature-authentication",
    "per-witness-signed-boundary-binding",
    "collected-observation-reconciliation",
    "v2-material-and-edge-consistency",
    "per-witness-v2-path-coverage",
    "cross-format-signed-head-consistency",
)
_UNPROVED = (
    "provider-non-omission",
    "atomic-snapshot",
    "historical-deletion-absence",
    "witness-custody",
    "database-chain-agreement",
    "freshness",
    "rollback-memory-continuity",
    "durable-delivery",
    "operational-key-activation",
    "source-independent-recovery",
)


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"not-json", "invalid"),
        (b"[]", "invalid"),
        (b'{"checkpoint":[]}', "invalid"),
        (b'{"checkpoint":{"format_version":null}}', "v2"),
        (b'{"checkpoint":{"format_version":"2"}}', "v2"),
        (b'{"checkpoint":{"format_version":true}}', "v2"),
        (b'{"checkpoint":{"format_version":3},"extra":[]}', "v2"),
        (b'{"checkpoint":{"format_version":2,"format_version":2}}', "invalid"),
        (
            b'{"checkpoint":{"org_id":"x","latest_id":42,"latest_row_hash":"x","timestamp":"x"},"signature":"x"}',
            "legacy",
        ),
        (
            b'{"checkpoint":{"org_id":"x","latest_id":42.0,"latest_row_hash":"x","timestamp":"x"},"signature":"x"}',
            "legacy",
        ),
        (
            b'{"checkpoint":{"org_id":"x","latest_id":"42","latest_row_hash":"x","timestamp":"x"},"signature":"x"}',
            "legacy",
        ),
    ],
)
def test_shape_routing_does_not_reinterpret_legacy_numbers_or_fall_back_from_v2(
    body: bytes, expected: str
) -> None:
    from easysynq_api.services.audit._history_reconciliation_store import _classify_body

    assert _classify_body(body) == expected


def test_rebound_transport_fixture_retains_independent_bridge_and_lineage_closure() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    args, observations = _mixed_originals()
    enrollment, root, pages, _, _ = args
    legacy = tuple(
        bridge.LegacyBodyObservation(*o) for o in observations if "/v2-node-" not in o[1]
    )
    v2 = tuple(
        lineage.EnvelopeObservation(str(w), k, v, body)
        for w, k, v, body in observations
        if "/v2-node-" in k
    )
    assert (
        bridge.evaluate_bootstrap_bridge(
            enrollment, root, pages, legacy, limits=bridge.BridgeLimits(4096, 16_777_216, 32)
        ).status
        == "consistent"
    )
    result = lineage.evaluate_lineage(
        enrollment.stream, v2, limits=lineage.LineageLimits(4096, 16_777_216, 32)
    )
    assert result.status == "consistent"
    assert result.tip_sequence == 3 and len(result.key_history) == 2


@dataclasses.dataclass
class _MixedTransport:
    args: tuple[Any, ...]
    deliveries: dict[str, list[tuple[str, str, bytes | BaseException]]]
    directory: Path
    list_failures: dict[str, BaseException] = dataclasses.field(default_factory=dict)
    markers: dict[str, list[tuple[str, str]]] = dataclasses.field(default_factory=dict)
    cycles: set[str] = dataclasses.field(default_factory=set)
    admitted_xml: list[bytes] = dataclasses.field(default_factory=list)


@pytest.fixture
def mixed_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _MixedTransport:
    from easysynq_api.services.audit import isolated_raw, isolated_version_page
    from easysynq_api.services.audit.raw_transport import RawCheckpointVersion
    from easysynq_api.services.audit.sink import CheckpointVersionRef, CheckpointVersionsPage
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage
    from tests.unit.test_audit_history_collection import _original_page

    args = mixed_case()
    original_args, observations = _mixed_originals()
    assert args == original_args
    buckets = {w.witness_id: w.reader.bucket for w in args[3]}
    deliveries: dict[str, list[tuple[str, str, bytes | BaseException]]] = {
        b: [] for b in buckets.values()
    }
    for witness, key, version, body in observations:
        deliveries[buckets[witness]].append((key, version, body))
    case = _MixedTransport(args, deliveries, tmp_path)
    pending: dict[str, list[tuple[str, str, bytes | BaseException]]] = {}

    def list_page(
        reader: Any,
        org: Any,
        *,
        key_marker: Any = None,
        version_id_marker: Any = None,
        cancel: Any = None,
    ) -> Any:
        assert org == args[0].stream.org_id
        if reader.bucket in case.list_failures:
            raise case.list_failures[reader.bucket]
        cycling = reader.bucket in case.cycles
        next_key = f"checkpoints/{org}/cycle" if cycling else None
        next_version = "cycle-version" if cycling and version_id_marker is None else None
        if key_marker is not None:
            assert (
                cycling and key_marker == next_key and version_id_marker in {None, "cycle-version"}
            )
        records = case.deliveries[reader.bucket]
        markers = case.markers.get(reader.bucket, []) if key_marker is None else []
        pending[reader.bucket] = list(records)
        entries = b"".join(
            (
                "<Version><Key>"
                + quote(key, safe="")
                + "</Key><VersionId>"
                + quote(version, safe="")
                + "</VersionId><IsLatest>false</IsLatest></Version>"
            ).encode()
            for key, version, _ in records
        )
        entries += b"".join(
            (
                "<DeleteMarker><Key>"
                + quote(key, safe="")
                + "</Key><VersionId>"
                + quote(version, safe="")
                + "</VersionId><IsLatest>false</IsLatest></DeleteMarker>"
            ).encode()
            for key, version in markers
        )
        xml = _original_page(
            entries,
            bucket=reader.bucket,
            marker=quote(key_marker or "", safe=""),
            version_marker=quote(version_id_marker or "", safe=""),
            truncated=cycling,
            next_marker=quote(next_key or "", safe=""),
            next_version_marker=quote(next_version or "", safe=""),
        )
        case.admitted_xml.append(xml)
        return RawCheckpointVersionPage(
            xml,
            CheckpointVersionsPage(
                tuple(CheckpointVersionRef(k, v) for k, v, _ in records),
                tuple(CheckpointVersionRef(k, v) for k, v in markers),
                cycling,
                next_key,
                next_version,
            ),
        )

    def read_body(reader: Any, ref: Any, *, cancel: Any = None) -> Any:
        key, version, body = pending[reader.bucket].pop(0)
        assert (key, version) == (ref.key, ref.version_id)
        if isinstance(body, BaseException):
            raise body
        return RawCheckpointVersion(key, version, body)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        isolated_version_page, "read_raw_checkpoint_version_page_isolated", list_page
    )
    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", read_body)
    return case


def test_complete_mixed_history_returns_compact_result(mixed_transport: _MixedTransport) -> None:
    report = public.collect_and_reconcile_checkpoint_history(*mixed_transport.args)
    assert report.status == "consistent"
    assert report.usable is not None
    assert report.usable.tip.sequence == report.usable.path_length == 3
    assert report.usable.used_epoch_count == 2
    assert report.failed_issues == report.incomplete_issues == report.issues_omitted == 0
    assert not hasattr(report.usable, "ordered_envelopes")
    assert report.counts.legacy_body_deliveries == 514
    assert report.counts.v2_body_deliveries == 6
    assert report.counts.v2_duplicate_deliveries == 3
    assert report.counts.invalid_shape_deliveries == 0
    package_bytes = len(mixed_transport.args[1]) + sum(len(p.body) for p in mixed_transport.args[2])
    assert report.counts.package_bytes == package_bytes
    assert report.counts.admitted_total_bytes == package_bytes + sum(
        map(len, mixed_transport.admitted_xml)
    ) + sum(len(raw) for copies in mixed_transport.deliveries.values() for _, _, raw in copies)
    assert report.established_checks == _ESTABLISHED
    assert report.unproved_checks == _UNPROVED
    assert list(mixed_transport.directory.iterdir()) == []


def _codes(report: public.HistoryReconciliationReport) -> set[tuple[str, str]]:
    return {(i.component, i.code) for i in report.issues}


def test_deep_fatal_groups_keep_their_identity_during_error_translation() -> None:
    fatal: BaseException = KeyboardInterrupt()
    for _ in range(2000):
        fatal = BaseExceptionGroup("synthetic fatal", [fatal])
    assert public._translate_fault(fatal) is fatal


@pytest.mark.parametrize(
    "change,component,code",
    [
        ("middle", "composition", "V2_WITNESS_COVERAGE_MISSING"),
        ("tip", "composition", "V2_WITNESS_COVERAGE_MISSING"),
        ("empty", "composition", "V2_WITNESS_COVERAGE_MISSING"),
        ("denied", "collection", "LIST_UNAVAILABLE"),
        ("cycle", "collection", "CURSOR_CYCLE"),
        ("marker", "collection", "DELETE_OBSERVATION"),
        ("unreadable", "collection", "VERSION_UNAVAILABLE"),
        ("unavailable-copy", "collection", "VERSION_UNAVAILABLE"),
        ("discriminator", "lineage", "ENVELOPE_INVALID"),
        ("signature", "lineage", "ENVELOPE_INVALID"),
        ("duplicate-fields", "composition", "CHECKPOINT_BODY_INVALID"),
        ("bridge-object", "composition", "CHECKPOINT_BODY_INVALID"),
        ("wrong-committed-format", "bridge", "LEGACY_BODY_COMMITMENT_MISMATCH"),
        ("unlisted-legacy", "bridge", "UNLISTED_AUTHENTIC_LEGACY"),
        ("no-v2", "lineage", "EMPTY_GRAPH"),
    ],
)
def test_original_evidence_faults_suppress_all_usable_claims(
    mixed_transport: _MixedTransport, change: str, component: str, code: str
) -> None:
    from easysynq_api.services.audit.raw_transport import RawVersionReadError
    from easysynq_api.services.audit.version_page_transport import VersionPageReadError

    case = mixed_transport
    bucket = case.args[3][1].reader.bucket
    records = case.deliveries[bucket]
    middle = next(i for i, (k, _, _) in enumerate(records) if k.endswith("v2-node-1"))
    key, version, raw = records[middle]
    assert type(raw) is bytes
    if change in {"middle", "tip"}:
        suffix = "v2-node-1" if change == "middle" else "v2-node-2"
        records[:] = [o for o in records if not o[0].endswith(suffix)]
    elif change == "empty":
        records.clear()
    elif change == "denied":
        case.list_failures[bucket] = VersionPageReadError("PROVIDER_FAILURE")
    elif change == "cycle":
        case.cycles.add(bucket)
    elif change == "marker":
        case.markers[bucket] = [(key, version)]
    elif change in {"unreadable", "unavailable-copy"}:
        bad = (key, version, RawVersionReadError("PROVIDER_FAILURE"))
        if change == "unreadable":
            records[middle] = bad
        else:
            records.append(bad)
    elif change in {"discriminator", "signature", "duplicate-fields"}:
        value = json.loads(raw)
        if change == "discriminator":
            value["checkpoint"]["format_version"] = 3
        else:
            value["signature"] = "A" * 86 + "=="
        body = (
            b'{"checkpoint":{},' + raw[1:]
            if change == "duplicate-fields"
            else json.dumps(value).encode()
        )
        records[middle] = key, version, body
    elif change == "bridge-object":
        records.append((key + "-bridge", version, case.args[1]))
    elif change == "wrong-committed-format":
        old_key, old_version, _ = records[0]
        records[0] = old_key, old_version, raw
    elif change == "unlisted-legacy":
        old_key, _, old_raw = records[0]
        records.append((old_key.rsplit("/", 1)[0] + "/9999999999-unlisted", version, old_raw))
    elif change == "no-v2":
        for copies in case.deliveries.values():
            copies[:] = [o for o in copies if "/v2-node-" not in o[0]]
    report = public.collect_and_reconcile_checkpoint_history(*case.args)
    assert (component, code) in _codes(report)
    assert report.usable is None and report.established_checks == ()
    assert report.unproved_checks == _UNPROVED
    assert report.status == ("failed" if report.failed_issues else "incomplete")
    assert (
        report.failed_issues + report.incomplete_issues
        == len(report.issues) + report.issues_omitted
    )
    if change in {"middle", "tip", "unreadable"}:
        missing = [i for i in report.issues if i.code == "V2_WITNESS_COVERAGE_MISSING"]
        assert len(missing) == 1 and missing[0].witness_id == case.args[3][1].witness_id
    if change in {"denied", "cycle"}:
        assert ("bridge", "WITNESS_COLLECTION_GAP") in _codes(report)
    if change in {"cycle", "marker", "unavailable-copy"}:
        assert ("composition", "V2_WITNESS_COVERAGE_MISSING") not in _codes(report)
    if change == "wrong-committed-format":
        assert ("bridge", "LEGACY_BODY_INVALID") in _codes(report)
        assert ("bridge", "LEGACY_BODY_MISSING") not in _codes(report)
    assert list(case.directory.iterdir()) == []


def test_canonical_variants_cover_every_witness_without_inventing_nodes(
    mixed_transport: _MixedTransport,
) -> None:
    for records in (mixed_transport.deliveries[mixed_transport.args[3][1].reader.bucket],):
        for i, (key, version, body) in enumerate(records):
            if "/v2-node-" in key:
                records[i] = key, version, json.dumps(json.loads(body), indent=2).encode()
    report = public.collect_and_reconcile_checkpoint_history(*mixed_transport.args)
    assert report.status == "consistent" and report.usable is not None
    assert report.usable.path_length == 3 and report.counts.v2_duplicate_deliveries == 3


@pytest.mark.parametrize("display_limit", [1, 32])
def test_detached_signed_head_conflict_dominates_an_included_required_pin(
    mixed_transport: _MixedTransport,
    display_limit: int,
) -> None:
    import base64
    import hashlib

    import rfc8785
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from easysynq_api.services.audit.lineage import RequiredCheckpointPin

    case = mixed_transport
    args = list(case.args)
    args[4] = dataclasses.replace(args[4], maximum_issues=display_limit)
    records = case.deliveries[args[3][0].reader.bucket]
    tip = json.loads(next(raw for key, _, raw in records if key.endswith("v2-node-2")))
    stream = dataclasses.replace(
        args[0].stream, required_checkpoint=RequiredCheckpointPin(tip["anchor_hash"], 3)
    )
    args[0] = dataclasses.replace(args[0], stream=stream)
    payload = json.loads(next(raw for key, _, raw in records if key.endswith("v2-node-0")))[
        "checkpoint"
    ]
    payload.update(
        anchor_id="00000000-0000-4000-8000-000000000099",
        previous_anchor_hash="e" * 64,
        latest_row_hash="f" * 64,
    )
    canonical = rfc8785.dumps(payload)
    signature = Ed25519PrivateKey.from_private_bytes(bytes([113]) * 32).sign(
        b"EasySynQ/AuditCheckpoint/v2/signature\0" + canonical
    )
    raw = rfc8785.dumps(
        {
            "checkpoint": payload,
            "signature": base64.b64encode(signature).decode(),
            "anchor_hash": hashlib.sha256(
                b"EasySynQ/AuditCheckpoint/v2/hash\0" + canonical + signature
            ).hexdigest(),
        }
    )
    records.append((f"checkpoints/{stream.org_id}/detached", "detached-version", raw))
    report = public.collect_and_reconcile_checkpoint_history(*args)
    expected = {("composition", "GLOBAL_SIGNED_HEAD_CONFLICT")}
    if display_limit > 1:
        expected.add(("lineage", "DISCONNECTED_GRAPH"))
    assert _codes(report) == expected
    assert (report.failed_issues, report.incomplete_issues) == (1, 1)
    assert report.issues_omitted == (1 if display_limit == 1 else 0)
    assert report.required_checkpoint_relation == "included"
    assert report.status == "failed" and report.usable is None and report.established_checks == ()
    assert (
        len(next(i for i in report.issues if i.code == "GLOBAL_SIGNED_HEAD_CONFLICT").references)
        == 2
    )


def _public_scope(args: tuple[Any, ...]) -> Any:
    from easysynq_api.services.audit._history_reconciliation_protocol import _PublicScope
    from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness

    enrollment, root, pages, readers, limits = args
    hashes = {w.witness_id: w.namespace_hash for w in enrollment.witnesses}
    return _PublicScope(
        enrollment,
        tuple(_SpoolWitness(w.witness_id, hashes[w.witness_id], w.reader.bucket) for w in readers),
        limits,
        None if root is None else len(root),
        tuple(len(p.body) for p in pages),
    )


def test_complete_report_byte_ceiling_and_duplicate_json_fields(
    mixed_transport: _MixedTransport,
) -> None:
    from easysynq_api.services.audit import _history_reconciliation_report as boundary
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit.history_collection import HistoryCollectionError

    report = public.collect_and_reconcile_checkpoint_history(*mixed_transport.args)
    scope = _public_scope(mixed_transport.args)
    raw = wire.encode(boundary._report_payload(report))
    exact = raw + b" " * (65536 - len(raw))
    assert boundary._decode_report(exact, scope) == report
    for invalid in (exact + b" ", b'J{"status":"consistent",' + raw[2:]):
        with pytest.raises(HistoryCollectionError) as caught:
            boundary._decode_report(invalid, scope)
        assert caught.value.code == "PROTOCOL_INVALID"


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("extra",), "unexpected"),
        (("scope",), "supplied-v2-graph"),
        (("status",), "failed"),
        (("failed_issues",), True),
        (("incomplete_issues",), 1),
        (("issues_omitted",), 1),
        (("required_checkpoint_relation",), "unassessed"),
        (("unproved_checks",), []),
        (("established_checks",), ["recovery-ready"]),
        (("usable",), None),
        (("witnesses", 0, "witness_id"), "00000000-0000-4000-8000-000000000099"),
        (("witnesses", 0, "terminal_reached"), False),
        (("witnesses", 0, "page_attempts"), 0),
        (("witnesses", 0, "successful_reads"), True),
        (("witnesses", 0, "conflicting_locators"), 999),
        (("counts", "supplied_pages"), 3),
        (("counts", "package_bytes"), 0),
        (("counts", "admitted_total_bytes"), 0),
        (("counts", "v2_body_deliveries"), 7),
        (("counts", "v2_duplicate_deliveries"), 6),
        (("counts", "canonical_page_duplicates"), 2),
        (("usable", "ordered_envelopes"), []),
        (("usable", "path_length"), 2),
        (("usable", "used_epoch_count"), 4),
        (("usable", "tip", "sequence"), True),
        (("usable", "tip", "key_epoch"), 0),
        (("usable", "tip", "audit_head", "latest_id"), 0),
        (("usable", "bootstrap_pin", "initial_public_key"), "00" * 32),
        (("usable", "witness_summaries", 0, "committed_locators"), 0),
        (("usable", "witness_summaries", 0, "highest_head", "latest_row_hash"), "f" * 64),
        (("issues", 0, "component"), "recovery"),
        (("issues", 0, "code"), "RESOURCE_LIMIT"),
        (("issues", 0, "severity"), "failed"),
        (("issues", 0, "count"), 2),
        (("issues", 0, "witness_id"), None),
        (("issues", 0, "references"), [{"kind": "observation", "index": 999999}]),
        (("issues", 0, "references"), [{"kind": "page", "index": 0}]),
    ],
)
def test_parent_rejects_malformed_or_impossible_compact_results(
    mixed_transport: _MixedTransport, path: tuple[Any, ...], replacement: Any
) -> None:
    from easysynq_api.services.audit import _history_reconciliation_report as boundary
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit.history_collection import HistoryCollectionError

    if path[0] == "issues":
        records = mixed_transport.deliveries[mixed_transport.args[3][1].reader.bucket]
        records[:] = [o for o in records if not o[0].endswith("v2-node-1")]
    report = public.collect_and_reconcile_checkpoint_history(*mixed_transport.args)
    scope = _public_scope(mixed_transport.args)
    value = wire.metadata(wire.encode(boundary._report_payload(report)))
    assert boundary._decode_report(wire.encode(value), scope) == report
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    with pytest.raises(HistoryCollectionError) as caught:
        boundary._decode_report(wire.encode(value), scope)
    assert caught.value.code == "PROTOCOL_INVALID"


def test_final_report_is_decoded_only_after_worker_removal_and_owner_join(
    mixed_transport: _MixedTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit import _history_reconciliation_report as boundary
    from easysynq_api.services.audit import history_collection as collection

    original_init = collection._CollectionOwner.__init__
    original_decode = boundary._decode_report
    owners = []
    decoded = []

    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        owners.append(self)

    def decode(*args: Any) -> Any:
        assert len(owners) == 1 and not owners[0]._thread.is_alive()
        assert list(mixed_transport.directory.iterdir()) == []
        decoded.append(True)
        return original_decode(*args)

    monkeypatch.setattr(collection._CollectionOwner, "__init__", initialize)
    monkeypatch.setattr(boundary, "_decode_report", decode)
    assert (
        public.collect_and_reconcile_checkpoint_history(*mixed_transport.args).status
        == "consistent"
    )
    assert decoded == [True]


def test_collection_group_counts_must_match_reported_original_outcomes(
    mixed_transport: _MixedTransport,
) -> None:
    from easysynq_api.services.audit import _history_reconciliation_report as boundary
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit.history_collection import HistoryCollectionError
    from easysynq_api.services.audit.raw_transport import RawVersionReadError

    records = mixed_transport.deliveries[mixed_transport.args[3][1].reader.bucket]
    key, version, _ = records[-1]
    records.append((key, version, RawVersionReadError("PROVIDER_FAILURE")))
    report = public.collect_and_reconcile_checkpoint_history(*mixed_transport.args)
    assert _codes(report) == {("collection", "VERSION_UNAVAILABLE")}
    payload = wire.metadata(wire.encode(boundary._report_payload(report)))
    payload["issues"][0]["count"] = 2
    with pytest.raises(HistoryCollectionError):
        boundary._decode_report(wire.encode(payload), _public_scope(mixed_transport.args))


@pytest.mark.parametrize("fault", ["trailing-byte", "nonzero-exit", "cleanup-fatal", "late-cancel"])
def test_provisional_success_cannot_escape_failed_owned_publication(
    mixed_transport: _MixedTransport, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import threading

    from easysynq_api.services.audit import _history_reconciliation_report as boundary
    from easysynq_api.services.audit._history_reconciliation_session import _ReconciliationSession

    cancel = threading.Event()
    original_rpc = _ReconciliationSession._rpc
    original_read = _ReconciliationSession._read
    original_cleanup = _ReconciliationSession._cleanup
    original_decode = boundary._decode_report
    final = []
    fatal = KeyboardInterrupt()

    def rpc(self: Any, op: str, *args: Any, **kwargs: Any) -> Any:
        result = original_rpc(self, op, *args, **kwargs)
        if op == "FINISH_RECONCILIATION":
            final.append(True)
            if fault == "nonzero-exit":
                wait = self._process.wait

                def bad_exit(*args: Any, **kwargs: Any) -> int:
                    wait(*args, **kwargs)
                    return 1

                self._process.wait = bad_exit
        return result

    def read(self: Any, size: int, deadline: float) -> bytes:
        raw = original_read(self, size, deadline)
        return b"x" if final and fault == "trailing-byte" and size == 1 else raw

    def cleanup(self: Any) -> list[BaseException]:
        failures = original_cleanup(self)
        return [*failures, fatal] if final and fault == "cleanup-fatal" else failures

    def decode(*args: Any) -> Any:
        assert fault == "late-cancel", "a failed lifetime reached public decoding"
        result = original_decode(*args)
        cancel.set()
        return result

    monkeypatch.setattr(_ReconciliationSession, "_rpc", rpc)
    monkeypatch.setattr(_ReconciliationSession, "_read", read)
    monkeypatch.setattr(_ReconciliationSession, "_cleanup", cleanup)
    monkeypatch.setattr(boundary, "_decode_report", decode)
    with pytest.raises(BaseException) as caught:
        public.collect_and_reconcile_checkpoint_history(*mixed_transport.args, cancel=cancel)
    assert final == [True]
    if fault == "late-cancel":
        assert type(caught.value) is public.HistoryReconciliationCancelled
    elif fault == "cleanup-fatal":
        from easysynq_api.services.audit.history_collection import _contains_fault

        assert _contains_fault(caught.value, fatal)
    else:
        assert type(caught.value) is public.HistoryReconciliationError
        assert caught.value.code == (
            "PROTOCOL_INVALID" if fault == "trailing-byte" else "WORKER_FAILED"
        )
    assert list(mixed_transport.directory.iterdir()) == []
