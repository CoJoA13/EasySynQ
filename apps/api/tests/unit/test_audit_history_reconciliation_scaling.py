"""Independent original histories that distinguish global closure from a prefix."""

from pathlib import Path

import pytest

from easysynq_api.services.audit import history_reconciliation as public
from tests.unit.audit_history_reconciliation_vectors import large_case, synthetic_transport

pytestmark = pytest.mark.unit


def test_independent_small_history_closes_through_public_owned_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = large_case(3, 5, 2)
    with synthetic_transport(case, monkeypatch, tmp_path) as receipt:
        report = public.collect_and_reconcile_checkpoint_history(*case.args)
    assert report.status == "consistent"
    assert report.usable is not None
    assert report.usable.tip.anchor_hash == case.expected_path[-1]
    assert report.usable.path_length == 3
    assert report.usable.used_epoch_count == len(case.expected_used_epochs) == 2
    assert report.counts.legacy_body_deliveries == case.legacy_deliveries
    assert report.counts.v2_body_deliveries == case.v2_deliveries
    assert report.counts.admitted_total_bytes == case.total_bytes
    assert receipt == {"list": case.list_deliveries, "get": case.body_deliveries}
    assert list(tmp_path.iterdir()) == []


def test_frozen_vectors_match_primitives_and_unchanged_reference_evaluators() -> None:
    import base64
    import hashlib
    import json

    import rfc8785
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import checkpoint_v2 as codec
    from easysynq_api.services.audit import lineage
    from tests.unit.audit_history_reconciliation_vectors import (
        ANCHOR_HASH,
        ORG,
        SIGNATURE,
        STREAM,
        TRANSITION_PROOF,
        frozen_vector,
        private,
    )

    frozen = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/audit_history_reconciliation_vectors.json"
        ).read_bytes()
    )
    assert frozen == frozen_vector()
    case = large_case(3, 5, 2)
    legacy, v2 = [], []
    for d in case.deliveries:
        parsed = json.loads(d.body)
        payload = parsed["checkpoint"]
        signature = base64.b64decode(parsed["signature"])
        canonical = rfc8785.dumps(payload)
        if "format_version" in payload:
            key = private(f"synthetic v2 key {payload['key_epoch']}").public_key()
            key.verify(signature, SIGNATURE + canonical)
            assert (
                hashlib.sha256(ANCHOR_HASH + canonical + signature).hexdigest()
                == parsed["anchor_hash"]
            )
            if payload["kind"] == "key_transition":
                next_key = Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(payload["next_public_key"])
                )
                proof = {k: v for k, v in payload.items() if k != "next_key_signature"}
                next_key.verify(
                    base64.b64decode(payload["next_key_signature"]),
                    TRANSITION_PROOF + rfc8785.dumps(proof),
                )
            verified = codec.verify_envelope(d.body, public_key=key, org_id=ORG, stream_id=STREAM)
            assert verified.anchor_hash == parsed["anchor_hash"]
            v2.append(lineage.EnvelopeObservation(str(d.witness), d.key, d.version, d.body))
        else:
            private("synthetic legacy signing fixture").public_key().verify(signature, canonical)
            legacy.append(bridge.LegacyBodyObservation(d.witness, d.key, d.version, d.body))
    result = bridge.evaluate_bootstrap_bridge(
        case.args[0],
        case.args[1],
        case.args[2],
        tuple(legacy),
        limits=bridge.BridgeLimits(4096, 16_777_216, 32),
    )
    assert result.status == "consistent"
    graph = lineage.evaluate_lineage(
        case.args[0].stream, tuple(v2), limits=lineage.LineageLimits(4096, 16_777_216, 32)
    )
    assert graph.status == "consistent"
    assert tuple(node.anchor_hash for node in graph.ordered_envelopes) == case.expected_path
    assert tuple(epoch.key_epoch for epoch in graph.key_history) == case.expected_used_epochs
    for item in frozen["negative"]:
        with pytest.raises(codec.CheckpointV2Error):
            codec.verify_envelope(
                bytes.fromhex(item["body_hex"]),
                public_key=private("synthetic v2 key 1").public_key(),
                org_id=ORG,
                stream_id=STREAM,
            )


def _assert_kernel_oracle(result: dict, case: object) -> None:
    assert result["report"]["status"] == "consistent"
    assert tuple(result["path"]) == case.expected_path
    assert tuple(result["epochs"]) == case.expected_used_epochs
    assert result["counts"] == dict(
        inspect=case.inputs["v2_nodes"],
        verify=case.inputs["v2_nodes"],
        edge=case.inputs["v2_nodes"],
        legacy_decode=case.inputs["legacy_bodies"],
        legacy_auth=case.inputs["legacy_bodies"],
    )
    assert result["measurements"]["maximum_step_seconds"] < 10
    assert result["measurements"]["maximum_step_work"] <= 512
    assert result["measurements"]["peak_rss_kib"] < 512 * 1024
    assert result["measurements"]["database_bytes"] <= case.args[4].maximum_spool_bytes
    assert len(result["plans"]) >= 30


def test_small_full_engine_adapter_compares_every_path_node_and_epoch() -> None:
    from tests.unit.audit_history_reconciliation_fixtures import _exchange
    from tests.unit.audit_history_reconciliation_vectors import kernel_payload

    case = large_case(3, 5, 2)
    _assert_kernel_oracle(_exchange(kernel_payload(case)), case)


@pytest.mark.parametrize("nodes,legacy", [(257, 513), (4097, 4609), (8193, 4609)])
def test_bounded_scaling_measures_full_public_and_indexed_closure(
    nodes: int, legacy: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import time

    from tests.unit.audit_history_reconciliation_fixtures import _exchange
    from tests.unit.audit_history_reconciliation_vectors import kernel_payload

    case = large_case(nodes, legacy, 2)
    began = time.monotonic()
    with synthetic_transport(case, monkeypatch, tmp_path) as receipt:
        report = public.collect_and_reconcile_checkpoint_history(*case.args)
    public_wall = time.monotonic() - began
    assert report.status == "consistent" and report.usable is not None
    assert report.usable.tip.anchor_hash == case.expected_path[-1]
    assert report.usable.path_length == len(case.expected_path) == nodes
    assert report.usable.used_epoch_count == len(case.expected_used_epochs)
    assert report.counts.supplied_pages == case.page_deliveries
    assert report.counts.legacy_body_deliveries == case.legacy_deliveries
    assert report.counts.v2_body_deliveries == case.v2_deliveries
    assert report.counts.admitted_total_bytes == case.total_bytes
    assert report.counts.legacy_duplicate_deliveries == 2
    assert report.counts.v2_duplicate_deliveries == nodes + 4
    assert receipt == dict(list=case.list_deliveries, get=case.body_deliveries)
    assert list(tmp_path.iterdir()) == []
    if nodes >= 4097:
        assert case.page_deliveries == 10 and case.body_deliveries > 5002
    result = _exchange(kernel_payload(case))
    _assert_kernel_oracle(result, case)
    print(
        "RECONCILIATION_SCALE "
        + json.dumps(
            dict(
                inputs=case.inputs,
                public_wall_seconds=public_wall,
                measurements=result["measurements"],
                counts=result["counts"],
                original_deliveries=receipt,
                plans=result["plans"],
            ),
            sort_keys=True,
        )
    )


@pytest.fixture(scope="module")
def large_originals():
    return large_case(4097, 4609, 2)


@pytest.mark.parametrize(
    "mutation,component,code",
    [
        ("fork", "lineage", "LINEAGE_FORK"),
        ("anchor-id-reuse", "lineage", "ANCHOR_ID_CONFLICT"),
        ("raw-locator-variant", "collection", "LOCATOR_CONFLICT"),
        ("legacy-head-conflict", "bridge", "SIGNED_HEAD_CONFLICT"),
        ("cross-format-conflict", "composition", "GLOBAL_SIGNED_HEAD_CONFLICT"),
        ("page-order", "bridge", "MANIFEST_ORDER_INVALID"),
        ("manifest-duplicate", "bridge", "MANIFEST_LOCATOR_DUPLICATE"),
        ("missing-witness-copy", "composition", "V2_WITNESS_COVERAGE_MISSING"),
    ],
)
def test_late_contradiction_overrides_already_included_required_pin(
    large_originals,
    mutation: str,
    component: str,
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.unit.audit_history_reconciliation_vectors import late_case

    case = late_case(large_originals, mutation)
    with synthetic_transport(case, monkeypatch, tmp_path) as receipt:
        result = public.collect_and_reconcile_checkpoint_history(*case.args)
    assert (component, code) in {(i.component, i.code) for i in result.issues}
    assert result.status == ("incomplete" if mutation == "missing-witness-copy" else "failed")
    assert result.required_checkpoint_relation == "included"
    assert result.usable is None and result.established_checks == ()
    assert (
        result.failed_issues + result.incomplete_issues
        == len(result.issues) + result.issues_omitted
    )
    assert receipt == dict(list=case.list_deliveries, get=case.body_deliveries)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "maximum_pages",
        "maximum_observations",
        "maximum_bridge_pages",
        "maximum_total_bytes",
        "maximum_manifest_entries",
    ],
)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_each_original_evidence_capacity_has_an_inclusive_boundary(
    field: str,
    offset: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import dataclasses

    case = large_case(3, 513, 2)
    required = dict(
        maximum_pages=case.list_deliveries,
        maximum_observations=case.body_deliveries,
        maximum_bridge_pages=case.page_deliveries,
        maximum_total_bytes=case.total_bytes,
        maximum_manifest_entries=513,
    )
    limits = dataclasses.replace(case.args[4], **{field: required[field] + offset})
    case = dataclasses.replace(case, args=(*case.args[:4], limits))
    with synthetic_transport(case, monkeypatch, tmp_path):
        if offset < 0:
            with pytest.raises(public.HistoryReconciliationError) as caught:
                public.collect_and_reconcile_checkpoint_history(*case.args)
            assert caught.value.code == "RESOURCE_LIMIT"
        else:
            report = public.collect_and_reconcile_checkpoint_history(*case.args)
            assert report.status == "consistent" and report.usable is not None
    assert list(tmp_path.iterdir()) == []


@pytest.fixture(scope="module")
def small_measured_spool_size():
    from tests.unit.audit_history_reconciliation_fixtures import _exchange
    from tests.unit.audit_history_reconciliation_vectors import kernel_payload

    result = _exchange(kernel_payload(large_case(3, 5, 2)))
    size = result["measurements"]["database_bytes"]
    assert size % 4096 == 0 and size > 65536
    return size


@pytest.mark.parametrize("pages_offset", [-1, 0, 1])
def test_actual_file_capacity_has_an_inclusive_page_boundary(
    small_measured_spool_size,
    pages_offset: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import dataclasses

    case = large_case(3, 5, 2)
    limits = dataclasses.replace(
        case.args[4], maximum_spool_bytes=small_measured_spool_size + 4096 * pages_offset
    )
    case = dataclasses.replace(case, args=(*case.args[:4], limits))
    with synthetic_transport(case, monkeypatch, tmp_path):
        if pages_offset < 0:
            with pytest.raises(public.HistoryReconciliationError) as caught:
                public.collect_and_reconcile_checkpoint_history(*case.args)
            assert caught.value.code == "RESOURCE_LIMIT"
        else:
            assert (
                public.collect_and_reconcile_checkpoint_history(*case.args).status == "consistent"
            )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("capacity", [36, 37, 38])
@pytest.mark.parametrize("display", [1, 32])
def test_complete_issue_capacity_and_display_truncation_never_publish_success(
    capacity: int,
    display: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import dataclasses

    from tests.unit.audit_history_reconciliation_vectors import PREFIX, Delivery, replace_deliveries

    case = large_case(3, 5, 2)
    witness = case.args[0].witnesses[-1].witness_id
    extra = tuple(
        Delivery(witness, f"{PREFIX}invalid-{i}", "v1", f"bad-json-{i}".encode()) for i in range(37)
    )
    case = replace_deliveries(case, (*case.deliveries, *extra))
    limits = dataclasses.replace(
        case.args[4], maximum_issue_groups=capacity, maximum_issues=display
    )
    case = dataclasses.replace(case, args=(*case.args[:4], limits))
    with synthetic_transport(case, monkeypatch, tmp_path):
        if capacity < 37:
            with pytest.raises(public.HistoryReconciliationError) as caught:
                public.collect_and_reconcile_checkpoint_history(*case.args)
            assert caught.value.code == "RESOURCE_LIMIT"
        else:
            report = public.collect_and_reconcile_checkpoint_history(*case.args)
            assert report.failed_issues == 37 and report.incomplete_issues == 0
            assert len(report.issues) == display and report.issues_omitted == 37 - display
            assert (
                report.status == "failed"
                and report.usable is None
                and report.established_checks == ()
            )
            assert report.counts.invalid_shape_deliveries == 37
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "binding,code",
    [
        ("bound", "RESOURCE_LIMIT"),
        ("unbound", "ROOT_COMMITMENT_MISMATCH"),
        ("enrollment-mismatch", "ROOT_ENROLLMENT_MISMATCH"),
        ("malformed", "ROOT_INVALID"),
    ],
)
def test_authentic_root_binding_precedes_excess_manifest_count(
    binding: str,
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import dataclasses
    import json

    import rfc8785

    from tests.unit.audit_history_reconciliation_vectors import commitment

    case = large_case(3, 5, 2)
    root = json.loads(case.args[1])
    root["entry_count"] = "100001"
    if binding == "enrollment-mismatch":
        root["witnesses"][0]["namespace_hash"] = "cd" * 32
    if binding == "malformed":
        root["unexpected"] = True
    raw = rfc8785.dumps(root)
    enrollment = case.args[0]
    if binding != "unbound":
        pin = dataclasses.replace(
            enrollment.stream.bootstrap, commitment_hash=commitment("root", raw)
        )
        enrollment = dataclasses.replace(
            enrollment, stream=dataclasses.replace(enrollment.stream, bootstrap=pin)
        )
    case = dataclasses.replace(case, args=(enrollment, raw, *case.args[2:]))
    with synthetic_transport(case, monkeypatch, tmp_path):
        if binding == "bound":
            with pytest.raises(public.HistoryReconciliationError) as caught:
                public.collect_and_reconcile_checkpoint_history(*case.args)
            assert caught.value.code == code
        else:
            report = public.collect_and_reconcile_checkpoint_history(*case.args)
            assert ("bridge", code) in {(i.component, i.code) for i in report.issues}
            assert report.usable is None and report.established_checks == ()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "mutant,code",
    [
        ("identity-prefix", "LEGACY_BODY_MISSING"),
        ("first-event-batch", "DISCONNECTED_GRAPH"),
        ("last-manifest-page", "PAGE_MISSING"),
    ],
)
def test_real_internal_truncation_mutants_fail_the_complete_oracle(
    large_originals,
    mutant: str,
    code: str,
) -> None:
    from tests.unit.audit_history_reconciliation_fixtures import _exchange
    from tests.unit.audit_history_reconciliation_vectors import kernel_payload

    result = _exchange(kernel_payload(large_originals, mutant=mutant))
    assert len(result["mutant_reached"]) == 1
    assert code in {issue["code"] for issue in result["report"]["issues"]}
    assert result["report"]["status"] != "consistent"
    assert result["report"]["usable"] is None and not result["report"]["established_checks"]
    with pytest.raises(AssertionError):
        _assert_kernel_oracle(result, large_originals)


@pytest.mark.parametrize(
    "kind,ceiling,code", [("root", 262144, "ROOT_INVALID"), ("page", 2097152, "PAGE_INVALID")]
)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_exact_wire_objects_keep_original_bytes_and_canonical_commitments(
    kind: str,
    ceiling: int,
    code: str,
    offset: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import dataclasses

    from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation

    case = large_case(3, 5, 2)
    original = case.args[1] if kind == "root" else case.args[2][0].body
    padded = original + b" " * (ceiling + offset - len(original))
    if kind == "root":
        args = case.args[0], padded, *case.args[2:]
    else:
        args = *case.args[:2], (BridgePageObservation(padded), *case.args[2][1:]), *case.args[3:]
    case = dataclasses.replace(case, args=args)
    with synthetic_transport(case, monkeypatch, tmp_path):
        report = public.collect_and_reconcile_checkpoint_history(*case.args)
    assert report.counts.admitted_total_bytes == case.total_bytes
    if offset > 0:
        assert ("bridge", code) in {(i.component, i.code) for i in report.issues}
        assert report.status == "failed" and report.usable is None
    else:
        assert report.status == "consistent" and report.usable is not None
        assert report.usable.tip.anchor_hash == case.expected_path[-1]
    assert list(tmp_path.iterdir()) == []
