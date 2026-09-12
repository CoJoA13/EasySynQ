"""Full-result differential checks against the unchanged supplied-package oracle."""

import dataclasses
import hashlib
import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.audit import bootstrap_bridge as bridge
from tests.unit import test_audit_bootstrap_bridge as reference
from tests.unit.test_audit_bootstrap_bridge import _evaluate, _reference, _reference_case

pytestmark = pytest.mark.unit


def test_small_bridge_matches_unchanged_reference() -> None:
    from tests.unit.audit_history_reconciliation_fixtures import kernel_bridge

    case = _reference_case("consistent", bridge)
    assert kernel_bridge(case) == _evaluate(case)


@pytest.mark.parametrize("name", [item["name"] for item in _reference()["scenarios"]])
def test_all_frozen_bridge_vectors_match_complete_reference(name: str) -> None:
    from tests.unit.audit_history_reconciliation_fixtures import kernel_bridge

    case = _reference_case(name, bridge)
    if name == "declared_large":
        from easysynq_api.services.audit.history_reconciliation import HistoryReconciliationError

        # Outside the shared semantic capacity: the new owner terminates instead
        # of publishing the old pure evaluator's RESOURCE_LIMIT evidence result.
        expected = _evaluate(case)
        assert expected.status == "incomplete"
        assert [i.code for i in expected.issues] == ["RESOURCE_LIMIT"]
        with pytest.raises(HistoryReconciliationError, match="RESOURCE_LIMIT"):
            kernel_bridge(case)
    else:
        assert kernel_bridge(case) == _evaluate(case)


# Reuse the independent input constructions and their original assertions. The
# wrapper compares the whole new result before returning the unchanged oracle's
# value. It does not patch either kernel, wire producer, codec or authenticator.
_PROPERTIES = (
    "conflicting_raw_transports_at_one_locator_keep_both_issues",
    "cross_page_duplicate_is_first_manifest_fault",
    "missing_page_does_not_invent_unlisted_body",
    "invalid_root_does_not_claim_keyset_or_membership",
    "missing_retained_key_and_exact_body_remain_incomplete",
    "root_namespace_binding_cannot_be_replaced",
    "root_external_bindings_are_not_self_enrolled",
    "legacy_offset_transport_matches_unchanged_verifier",
    "legacy_malformed_evidence_is_not_authentication_uncertainty",
    "maximum_legacy_supported_integer_is_authenticated_before_boundary_comparison",
    "collection_failures_are_sticky_beside_successful_delivery",
    "every_required_witness_needs_its_exact_body",
    "same_id_conflicts_survive_newer_boundary_and_duplicate_deliveries",
    "out_of_scope_bodies_are_not_authenticated_or_matched",
    "input_permutations_preserve_semantic_diagnostics",
    "equivalent_wire_json_is_canonical_duplicate_transport",
    "new_wire_rejection_is_bounded_and_has_first_category",
    "same_index_competing_page_does_not_replace_committed_page",
    "page_identity_failure_is_independent_of_root_availability",
    "extra_retained_key_cannot_silently_change_authoritative_inventory",
    "late_failure_survives_display_cap_and_counts_every_group",
    "retained_legacy_key_admission_is_not_retroactively_v2_admission",
    "legacy_timestamp_order_never_invents_freshness_or_predecessor_rules",
    "lagging_witness_cannot_borrow_boundary_from_sibling",
    "present_invalid_body_is_not_also_missing",
    "unlisted_boundary_at_new_version_is_a_discrepancy",
    "declared_resource_count_has_no_authority_before_root_bindings",
    "zero_keys_mean_missing_material_not_empty_history",
    "exact_empty_history_is_not_an_enrollable_package",
    "raw_body_commitment_is_sensitive_to_domain_and_single_byte",
    "signed_head_conflicts_across_committed_pages_are_not_hidden_by_summaries",
    "new_wire_decimal_spellings_are_not_legacy_numeric_coercions",
    "positive_external_bigint_endpoint_is_structurally_valid_without_claiming_history",
    "new_wire_root_page_and_legacy_body_exact_transport_ceilings",
    "all_duplicate_manifest_groups_are_counted_before_later_order_faults",
    "four_required_witnesses_each_need_boundary_evidence",
    "deleted_or_unavailable_literal_null_version_cannot_be_normalized_away",
    "single_required_witness_still_needs_its_complete_nonempty_package",
    "skipped_page_index_is_not_a_new_root_or_partial_window",
)


def _property_cases() -> list[Any]:
    cases = []
    for name in _PROPERTIES:
        function = getattr(reference, "test_" + name)
        dimensions = []
        for mark in getattr(function, "pytestmark", ()):
            assert mark.name == "parametrize" and type(mark.args[0]) is str
            dimensions.append([(mark.args[0], value) for value in mark.args[1]])
        for pairs in itertools.product(*dimensions):
            cases.append(
                pytest.param(
                    function, dict(pairs), id=name + "-" + "-".join(str(v) for _, v in pairs)
                )
            )
    return cases


@pytest.mark.parametrize("function,arguments", _property_cases())
def test_existing_semantic_properties_have_full_kernel_parity(
    function: Callable[..., None],
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.audit_history_reconciliation_fixtures import kernel_bridge

    oracle = bridge.evaluate_bootstrap_bridge
    calls = 0

    def compared(
        enrollment: Any, root_body: Any, pages: Any, observations: Any, *, limits: Any
    ) -> Any:
        nonlocal calls
        expected = oracle(enrollment, root_body, pages, observations, limits=limits)
        case = reference._ReferenceCase(enrollment, root_body, pages, observations)
        actual = kernel_bridge(case, maximum_issues=limits.maximum_issues)
        assert actual == expected
        calls += 1
        return expected

    monkeypatch.setattr(bridge, "evaluate_bootstrap_bridge", compared)
    function(**arguments)
    assert calls > 0


def test_shared_capacity_adapter_rejects_excess_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit import audit_history_reconciliation_fixtures as fixture

    def forbidden(_: Any) -> Any:
        raise AssertionError("excess fixture launched a worker")

    case = _reference_case("consistent", bridge)
    monkeypatch.setattr(fixture, "_exchange", forbidden)
    with pytest.raises(ValueError, match="shared capacity"):
        fixture.kernel_bridge(
            dataclasses.replace(case, observations=(case.observations[0],) * 4097)
        )


def test_shared_maximum_package_is_indexed_and_authenticates_each_raw_once() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from tests.unit import audit_history_reconciliation_fixtures as fixture

    case = reference._sized_package(4096)
    keys = list(case.enrollment.legacy_keys)
    for seed in range(100, 106):
        raw = (
            Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        keys.append(
            bridge.LegacyPublicMaterial("ed25519-sha256:" + hashlib.sha256(raw).hexdigest(), raw)
        )
    root = json.loads(case.root_body)
    root["legacy_key_ids"] = sorted(k.key_id for k in keys)
    case = reference._repin(
        dataclasses.replace(
            case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=tuple(keys))
        ),
        root=root,
    )
    remaining = (
        16 * 1024 * 1024
        - len(case.root_body)
        - sum(len(p.body) for p in case.pages + case.observations)
    )
    pages = []
    for page in case.pages:
        padding = min(2_097_152 - len(page.body), remaining)
        pages.append(dataclasses.replace(page, body=page.body + b" " * padding))
        remaining -= padding
    assert remaining == 0
    case = dataclasses.replace(case, pages=tuple(pages))
    value = fixture._exchange(fixture._bridge_payload(case, 32))
    assert fixture._bridge_result(case, value) == _evaluate(case)
    proof = value["inspection"]
    assert proof["decode_calls"] == proof["decoded_raws"] == 2
    assert proof["authentication_calls"] == proof["authenticated_raws"] == 2
    assert proof["maximum_step_work"] == 64
    assert proof["manifest_complete"] is True
    assert len(proof["plans"]) >= 20
    assert not any("TEMP B-TREE" in row for plan in proof["plans"] for row in plan)


@pytest.mark.parametrize("fault", ["sql", "kind", "preauthenticated"])
def test_fixture_worker_rejects_caller_code_or_preverified_state(fault: str) -> None:
    from tests.unit import audit_history_reconciliation_fixtures as fixture

    payload = fixture._bridge_payload(_reference_case("consistent", bridge), 32)
    if fault == "sql":
        payload["sql"] = "SELECT 1"
    elif fault == "kind":
        payload["kind"] = "eval"
    else:
        payload["observations"][0]["authenticated"] = True
    with pytest.raises(AssertionError):
        fixture._exchange(payload)


@pytest.mark.parametrize("fault", ["truncated", "extra", "nonzero", "stderr", "oversize"])
def test_fixture_parent_requires_exact_eof_zero_exit_and_owned_cleanup(
    fault: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit import audit_history_reconciliation_fixtures as fixture

    worker = tmp_path / "invalid_fixture_reply.py"
    worker.write_text(
        "import sys\nsys.stdin.buffer.read()\nbody=b'{}'\n"
        + {
            "truncated": "sys.stdout.buffer.write((3).to_bytes(4,'big')+body)\n",
            "extra": "sys.stdout.buffer.write((2).to_bytes(4,'big')+body+b'x')\n",
            "nonzero": "sys.stdout.buffer.write((2).to_bytes(4,'big')+body)\nsys.exit(3)\n",
            "stderr": (
                "sys.stdout.buffer.write((2).to_bytes(4,'big')+body)\nsys.stderr.write('fault')\n"
            ),
            "oversize": "sys.stdout.buffer.write((16777217).to_bytes(4,'big'))\n",
        }[fault]
    )
    directories = []
    original = fixture.tempfile.mkdtemp

    def recorded(**kwargs: Any) -> str:
        directory = original(**kwargs)
        directories.append(Path(directory))
        return directory

    monkeypatch.setattr(fixture, "_WORKER", worker)
    monkeypatch.setattr(fixture.tempfile, "mkdtemp", recorded)
    with pytest.raises(AssertionError):
        fixture._exchange({"fixture": "original"})
    assert len(directories) == 1 and not directories[0].exists()
