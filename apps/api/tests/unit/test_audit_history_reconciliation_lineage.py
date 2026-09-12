"""Independent full-result R77 differential contracts for the indexed kernel."""

import inspect
import itertools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.audit import lineage
from tests.unit import test_audit_lineage as reference
from tests.unit.test_audit_lineage import (
    _SCENARIO_NAMES,
    _enrollment_from_fixture,
    _observations_from_fixture,
    _reference,
)

pytestmark = pytest.mark.unit


def test_small_lineage_matches_unchanged_reference() -> None:
    from tests.unit.audit_history_reconciliation_fixtures import kernel_lineage

    vectors = _reference()
    enrollment = _enrollment_from_fixture(vectors, "valid_rotation")
    observations = _observations_from_fixture(vectors, "valid_rotation")
    expected = lineage.evaluate_lineage(
        enrollment, observations, limits=lineage.LineageLimits(4096, 16_777_216, 32)
    )
    assert kernel_lineage(enrollment, observations) == expected


def test_repeated_locator_batches_do_not_rescan_prior_deliveries(tmp_path: Path) -> None:
    from tests.unit.test_audit_history_reconciliation_indexes import _seeded_store_process
    from tests.unit.test_audit_history_reconciliation_worker import _init

    init = _init()
    init["limits"]["maximum_observations"] = 4096
    init["limits"]["maximum_spool_bytes"] = 64 * 1024 * 1024
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_lineage import _LineageKernel
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
try:
    seed([('same-locator',b'body')]*4096)
    while not store.index_bodies_step(): pass
    db=store._connection()
    db.execute("UPDATE raw_bodies SET format='v2'")
    kernel=_LineageKernel(store,scope.enrollment.stream,_IssueIndex(store))
    batches=[]
    while kernel._route_stage=='locators':
        before=kernel.work
        calls=[0]
        def progress():
            calls[0]+=1
            return 0
        db.set_progress_handler(progress,1)
        kernel.step('v2-route')
        db.set_progress_handler(None,0)
        work=kernel.work-before
        assert 0 <= work <= 64
        if work:
            batches.append((work,calls[0]))
        assert len(batches)<=64
    result={'batches':batches,'work':kernel.work}
finally: store.close()
print(json.dumps(result))
""",
        init,
    )
    assert result["work"] == 4096
    assert len(result["batches"]) == 64
    assert all(work == 64 for work, _ in result["batches"])
    # Compare actual VM instructions, not just returned rows or a query-plan label.
    # A keyset that seeks only the locator prefix grows with prior duplicate rows.
    instructions = [count for _, count in result["batches"]]
    assert max(instructions) <= 2 * min(instructions)


@pytest.mark.parametrize("name", _SCENARIO_NAMES)
def test_frozen_lineage_vectors_match_complete_reference(name: str) -> None:
    from tests.unit.audit_history_reconciliation_fixtures import kernel_lineage

    vectors = _reference()
    enrollment = _enrollment_from_fixture(vectors, name)
    observations = _observations_from_fixture(vectors, name)
    for ordering in itertools.permutations(observations):
        expected = lineage.evaluate_lineage(
            enrollment, ordering, limits=lineage.LineageLimits(4096, 16_777_216, 32)
        )
        assert kernel_lineage(enrollment, ordering) == expected


_PROPERTIES = (
    "terminal_transition_and_explicit_material_reuse_only_expose_used_epochs",
    "declared_key_authentication_is_distinct_from_edge_authority",
    "rejected_transition_reports_first_fault_without_releasing_next_material",
    "locator_and_anchor_conflicts_keep_two_distinct_stable_sides",
    "a_late_failure_dominates_exhaustive_counts_at_the_display_cap",
    "independent_categories_are_sorted_by_code_then_stable_subject",
    "maximum_width_inputs_bound_authentication_edges_and_representatives",
    "4096_transitions_allow_terminal_4097th_material_and_only_4096_used_epochs",
    "valid_locator_boundaries_are_correlation_metadata",
    "well_typed_bounded_bad_bodies_are_report_issues",
    "unverified_route_never_substitutes_for_crypto_or_enrolls_bad_transition",
    "authenticated_duplicate_count_excludes_invalid_foreign_and_unknown_bodies",
    "wrong_declared_key_transition_cannot_release_material_despite_other_edge_faults",
    "terminal_transition_material_can_authenticate_a_detached_node_only_diagnostically",
    "required_pin_same_hash_different_sequence_conflicts_and_uses_one_real_observation",
    "required_pin_on_rejected_edge_is_missing_even_with_an_authenticated_matching_body",
    "locator_conflicts_and_canonical_representative_updates",
    "anchor_id_conflict_includes_rejected_edges_without_manufacturing_a_fork",
    "unknown_structural_cycle_hints_remain_unresolved_without_guessed_key_verification",
    "enrolled_maximum_epoch_and_positive_head_are_exact_valid_integer_boundaries",
    "multiple_forks_count_per_parent_with_stable_subject_sort_and_bounded_representatives",
)


def _property_cases() -> list[Any]:
    cases = []
    for name in _PROPERTIES:
        function = getattr(reference, "test_" + name)
        dimensions = []
        for mark in getattr(function, "pytestmark", ()):
            assert mark.name == "parametrize"
            names = (mark.args[0],) if type(mark.args[0]) is str else mark.args[0]
            dimensions.append(
                [
                    dict(zip(names, (v,) if len(names) == 1 else v, strict=True))
                    for v in mark.args[1]
                ]
            )
        for index, parts in enumerate(itertools.product(*dimensions)):
            arguments = {key: value for part in parts for key, value in part.items()}
            cases.append(pytest.param(function, arguments, id=f"{name}-{index}"))
    return cases


@pytest.mark.parametrize("function,arguments", _property_cases())
def test_existing_semantics_and_actual_crypto_work_have_full_kernel_parity(
    function: Callable[..., None],
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit import audit_history_reconciliation_fixtures as fixture

    oracle, original_track = lineage.evaluate_lineage, reference._track_real_work
    tracked: list[dict[str, Any]] = []
    calls = 0

    def track(patch: pytest.MonkeyPatch) -> dict[str, Any]:
        counts = original_track(patch)
        tracked.append(counts)
        return counts

    def compared(enrollment: Any, observations: Any, *, limits: Any) -> Any:
        nonlocal calls
        before = (
            {key: tracked[-1][key] for key in ("inspect", "verify", "edge", "key_events")}
            if tracked
            else {}
        )
        before_keys = len(tracked[-1]["keys"]) if tracked else 0
        expected = oracle(enrollment, observations, limits=limits)
        value = fixture._exchange(
            fixture._lineage_payload(enrollment, observations, limits.maximum_issues)
        )
        assert fixture._lineage_result(value) == expected
        proof = value["inspection"]
        if tracked:
            counts = tracked[-1]
            for key in ("inspect", "verify", "edge", "key_events"):
                assert proof[key] == counts[key] - before[key], key
            assert proof["materials"] == counts["materials"]
            assert sorted(proof["verify_keys"]) == sorted(
                k.hex() for k in counts["keys"][before_keys:]
            )
        assert 0 <= proof["maximum_step_work"] <= 64
        assert len(proof["plans"]) >= 20
        calls += 1
        return expected

    monkeypatch.setattr(reference, "_track_real_work", track)
    monkeypatch.setattr(lineage, "evaluate_lineage", compared)
    kwargs = dict(arguments)
    if "monkeypatch" in inspect.signature(function).parameters:
        kwargs["monkeypatch"] = monkeypatch
    function(**kwargs)
    assert calls > 0


def test_late_key_resumes_a_completed_parent_without_reassessing_old_edges() -> None:
    import base64
    import json
    from uuid import UUID

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from tests.unit import audit_history_reconciliation_fixtures as fixture

    vectors = _reference()
    initial = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    next_key = reference._test_key(921)
    roots = sorted(
        (
            reference._signed_body(
                reference._anchor_payload(vectors, anchor_id=str(UUID(int=n))), initial
            )
            for n in (1000, 1001)
        ),
        key=lambda raw: json.loads(raw)["anchor_hash"],
    )
    early, later = (json.loads(raw)["anchor_hash"] for raw in roots)
    payload = dict(vectors["vectors"]["transition"]["checkpoint"])
    payload.update(
        anchor_id=str(UUID(int=1002)),
        previous_anchor_hash=later,
        next_key_id=reference._key_id(next_key),
        next_public_key=base64.b64encode(reference._key_bytes(next_key)).decode(),
        next_key_epoch="1",
    )
    transition = reference._signed_body(payload, initial, next_key)
    late = reference._signed_body(
        reference._anchor_payload(
            vectors,
            anchor_id=str(UUID(int=1003)),
            previous_anchor_hash=early,
            sequence="2",
            key_id=reference._key_id(next_key),
            key_epoch="1",
        ),
        next_key,
    )
    observations = tuple(
        reference._observation(raw, i) for i, raw in enumerate((*roots, transition, late))
    )
    enrollment = _enrollment_from_fixture(vectors)
    for ordering in (observations, observations[::-1]):
        value = fixture._exchange(fixture._lineage_payload(enrollment, ordering, 32))
        expected = lineage.evaluate_lineage(
            enrollment, ordering, limits=lineage.LineageLimits(4096, 16_777_216, 32)
        )
        assert fixture._lineage_result(value) == expected
        # The completed parent's permission is checked even though authentication
        # became possible only after another branch admitted a transition.
        assert {i.code for i in expected.issues} == {"KEY_EPOCH_VIOLATION", "LINEAGE_FORK"}
        proof = value["inspection"]
        assert proof["reopened_parents"] == 1
        assert proof["inspect"] == proof["verify"] == proof["edge"] == 4


def test_exact_shared_aggregate_is_processed_without_exporting_padded_raws() -> None:
    from tests.unit import audit_history_reconciliation_fixtures as fixture

    vectors = _reference()
    raw = bytes.fromhex(vectors["vectors"]["anchor"]["body_hex"])
    padded = raw + b" " * (65_536 - len(raw))
    observations = tuple(reference._observation(padded, i) for i in range(256))
    enrollment = _enrollment_from_fixture(vectors, "old_prefix_without_memory")
    value = fixture._exchange(fixture._lineage_payload(enrollment, observations, 32))
    expected = lineage.evaluate_lineage(
        enrollment, observations, limits=lineage.LineageLimits(4096, 16_777_216, 32)
    )
    assert fixture._lineage_result(value) == expected
    assert expected.duplicate_observations == 255
    assert value["inspection"]["inspect"] == value["inspection"]["verify"] == 1
    assert len(bytes.fromhex(value["path"][0])) < 65_536
    excess = (*observations, reference._observation(b"x", 256))
    with pytest.raises(ValueError, match="shared capacity"):
        fixture.kernel_lineage(enrollment, excess)
    assert lineage.evaluate_lineage(
        enrollment, excess, limits=lineage.LineageLimits(4096, 16_777_216, 32)
    ).issues == (lineage.LineageIssue("RESOURCE_LIMIT", "incomplete", ()),)
