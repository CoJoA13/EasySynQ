"""Independent public-vector contract tests for the pure lineage evaluator."""

from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import itertools
import json
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

if TYPE_CHECKING:
    from easysynq_api.services.audit import lineage

_SIGNATURE_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/signature\0"
_HASH_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/hash\0"
_PROOF_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0"
_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "audit_lineage_vectors.json"
_FIXTURE_SHA256 = "51cd6c9814f986573024a17169261871f24818a68cc39ea65b164b245bf33edd"
_UNPROVED_CHECKS = (
    "bootstrap-contents",
    "legacy-bridge-coverage",
    "witness-collection-completeness",
    "witness-custody",
    "audit-chain-comparison",
    "freshness",
    "operational-key-activation",
)

pytestmark = pytest.mark.unit


def _reference() -> dict[str, Any]:
    raw = _FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    return json.loads(raw)


def _scenario_by_name(reference: dict[str, Any], name: str) -> dict[str, Any]:
    return next(scenario for scenario in reference["scenarios"] if scenario["name"] == name)


def _enrollment_from_fixture(
    reference: dict[str, Any], name: str = "valid_rotation"
) -> lineage.StreamEnrollment:
    from easysynq_api.services.audit import lineage

    enrollment = _scenario_by_name(reference, name)["enrollment"]
    bootstrap = enrollment["bootstrap"]
    boundary = bootstrap["audit_boundary"]
    required = enrollment["required_checkpoint"]
    return lineage.StreamEnrollment(
        org_id=UUID(enrollment["org_id"]),
        stream_id=UUID(enrollment["stream_id"]),
        bootstrap=lineage.BootstrapPin(
            commitment_hash=bootstrap["commitment_hash"],
            initial_key_id=bootstrap["initial_key_id"],
            initial_public_key=bytes.fromhex(bootstrap["initial_public_key_hex"]),
            initial_key_epoch=bootstrap["initial_key_epoch"],
            audit_boundary=(
                None
                if boundary is None
                else lineage.AuditHead(boundary["latest_id"], boundary["latest_row_hash"])
            ),
        ),
        required_checkpoint=(
            None
            if required is None
            else lineage.RequiredCheckpointPin(required["anchor_hash"], required["sequence"])
        ),
    )


def _observations_from_fixture(
    reference: dict[str, Any], name: str
) -> tuple[lineage.EnvelopeObservation, ...]:
    from easysynq_api.services.audit import lineage

    return tuple(
        lineage.EnvelopeObservation(
            source_id=observation["source_id"],
            object_key=observation["object_key"],
            version_id=observation["version_id"],
            body=bytes.fromhex(reference["vectors"][observation["vector"]]["body_hex"]),
        )
        for observation in _scenario_by_name(reference, name)["observations"]
    )


def _assert_direct_valid_vector(vector: dict[str, Any]) -> None:
    """Check frozen public bytes using libraries, without production codec imports."""
    assert vector["crypto_valid"] is True
    envelope = json.loads(bytes.fromhex(vector["body_hex"]))
    checkpoint = envelope["checkpoint"]
    canonical = rfc8785.dumps(checkpoint)
    assert checkpoint == vector["checkpoint"]
    assert canonical == bytes.fromhex(vector["canonical_checkpoint_hex"])
    assert rfc8785.dumps(envelope) == bytes.fromhex(vector["canonical_envelope_hex"])
    public_bytes = bytes.fromhex(vector["public_key_hex"])
    assert checkpoint["key_id"] == "ed25519-sha256:" + hashlib.sha256(public_bytes).hexdigest()
    signature = base64.b64decode(envelope["signature"], validate=True)
    Ed25519PublicKey.from_public_bytes(public_bytes).verify(
        signature, _SIGNATURE_DOMAIN + canonical
    )
    assert (
        hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest()
        == envelope["anchor_hash"]
        == vector["anchor_hash"]
    )
    if checkpoint["kind"] == "key_transition":
        proof_payload = {
            key: value for key, value in checkpoint.items() if key != "next_key_signature"
        }
        next_public = base64.b64decode(checkpoint["next_public_key"], validate=True)
        assert (
            checkpoint["next_key_id"] == "ed25519-sha256:" + hashlib.sha256(next_public).hexdigest()
        )
        Ed25519PublicKey.from_public_bytes(next_public).verify(
            base64.b64decode(checkpoint["next_key_signature"], validate=True),
            _PROOF_DOMAIN + rfc8785.dumps(proof_payload),
        )


def test_independent_rotation_path_has_one_authorized_history() -> None:
    reference = _reference()
    scenario = _scenario_by_name(reference, "valid_rotation")
    names = tuple(observation["vector"] for observation in scenario["observations"])
    assert names == ("anchor", "transition", "successor")
    for name in names:
        _assert_direct_valid_vector(reference["vectors"][name])

    # Root's initial RED must reach this missing-module import after the controls.
    from easysynq_api.services.audit import lineage

    result = lineage.evaluate_lineage(
        _enrollment_from_fixture(reference),
        _observations_from_fixture(reference, "valid_rotation"),
        limits=lineage.LineageLimits(4096, 16 * 1024 * 1024, 32),
    )
    assert result.status == scenario["expected_status"] == "consistent"
    assert result.tip_sequence == 3
    assert result.tip_hash == reference["vectors"]["successor"]["anchor_hash"]
    assert tuple(item.sequence for item in result.ordered_envelopes) == (1, 2, 3)
    assert tuple(item.canonical_envelope for item in result.ordered_envelopes) == tuple(
        bytes.fromhex(reference["vectors"][name]["canonical_envelope_hex"]) for name in names
    )
    assert tuple(item.key_epoch for item in result.key_history) == (0, 1)
    assert tuple(item.introduced_by for item in result.key_history) == (
        None,
        reference["vectors"]["transition"]["anchor_hash"],
    )
    assert result.failed_issues == result.incomplete_issues == result.issues_omitted == 0
    assert result.issues == ()
    assert result.duplicate_observations == 0
    assert result.required_checkpoint_relation == scenario["expected_required_relation"]
    assert result.scope == "supplied-v2-graph"
    assert result.bootstrap_assurance == "external-pin-only"
    assert result.unproved_checks == _UNPROVED_CHECKS


def _evaluate(
    reference: dict[str, Any], name: str, observations: Any = None, limits: Any = None
) -> lineage.LineageEvaluation:
    from easysynq_api.services.audit import lineage

    return lineage.evaluate_lineage(
        _enrollment_from_fixture(reference, name),
        _observations_from_fixture(reference, name) if observations is None else observations,
        limits=lineage.LineageLimits(4096, 16 * 1024 * 1024, 32) if limits is None else limits,
    )


def _assert_assurance(result: lineage.LineageEvaluation) -> None:
    assert result.scope == "supplied-v2-graph"
    assert result.bootstrap_assurance == "external-pin-only"
    assert result.unproved_checks == _UNPROVED_CHECKS
    assert result.issues_omitted == (
        result.failed_issues + result.incomplete_issues - len(result.issues)
    )
    assert all(len(issue.observation_indexes) <= 2 for issue in result.issues)
    if result.status != "consistent":
        assert result.ordered_envelopes == result.key_history == ()
        assert result.tip_hash is result.tip_sequence is None


def _semantic_result(
    result: lineage.LineageEvaluation, observations: tuple[lineage.EnvelopeObservation, ...]
) -> tuple[Any, ...]:
    return (
        result.status,
        result.failed_issues,
        result.incomplete_issues,
        result.issues_omitted,
        result.duplicate_observations,
        result.required_checkpoint_relation,
        result.ordered_envelopes,
        result.key_history,
        result.tip_hash,
        result.tip_sequence,
        tuple(
            (
                issue.code,
                issue.severity,
                tuple(
                    (
                        observations[index].source_id,
                        observations[index].object_key,
                        observations[index].version_id,
                        hashlib.sha256(observations[index].body).hexdigest(),
                    )
                    for index in issue.observation_indexes
                ),
            )
            for issue in result.issues
        ),
    )


_SCENARIO_NAMES = (
    "valid_rotation",
    "terminal_transition",
    "old_key_successor",
    "unknown_key_successor",
    "bad_known_signature",
    "wrong_epoch_successor",
    "sequence_gap",
    "head_regression",
    "head_conflict",
    "fork",
    "missing_transition",
    "known_orphan",
    "detached_transition_cannot_enroll",
    "detached_transition_removed",
    "fork_cannot_lend_authority",
    "invalid_transition_cannot_enroll",
    "explicit_return_to_initial_key",
    "reused_anchor_id",
    "foreign_identity",
    "swapped_bootstrap",
    "swapped_initial_key",
    "swapped_initial_epoch",
    "positive_boundary_regression",
    "positive_boundary_conflict",
    "old_prefix_without_memory",
    "old_prefix_required_later",
    "required_conflict",
    "required_match_and_fork_conflict",
    "required_before_fork",
    "required_detached_not_included",
    "empty",
    "empty_required",
    "duplicate_deliveries",
    "equivalent_transport",
    "immutable_locator_transport_conflict",
)


def test_all_frozen_vectors_have_independent_crypto_controls() -> None:
    reference = _reference()
    assert {scenario["name"] for scenario in reference["scenarios"]} == set(_SCENARIO_NAMES)
    assert len(reference["vectors"]) == 23
    invalid = 0
    for vector in reference["vectors"].values():
        if vector["crypto_valid"]:
            _assert_direct_valid_vector(vector)
        else:
            invalid += 1
            envelope = json.loads(bytes.fromhex(vector["body_hex"]))
            canonical = rfc8785.dumps(envelope["checkpoint"])
            signature = base64.b64decode(envelope["signature"], validate=True)
            assert (
                hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest()
                == (envelope["anchor_hash"])
            )
            with pytest.raises(InvalidSignature):
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(vector["public_key_hex"])).verify(
                    signature, _SIGNATURE_DOMAIN + canonical
                )
    assert invalid == 1


@pytest.mark.parametrize("name", _SCENARIO_NAMES)
def test_frozen_scenarios_have_order_independent_authority_and_diagnostics(name: str) -> None:
    reference = _reference()
    scenario = _scenario_by_name(reference, name)
    observations = _observations_from_fixture(reference, name)
    baseline = _evaluate(reference, name)
    assert baseline.status == scenario["expected_status"]
    assert {issue.code for issue in baseline.issues} == set(scenario["expected_codes"])
    assert baseline.required_checkpoint_relation == scenario["expected_required_relation"]
    expected = _semantic_result(baseline, observations)
    for ordering in itertools.permutations(observations):
        result = _evaluate(reference, name, ordering)
        _assert_assurance(result)
        assert _semantic_result(result, ordering) == expected


def test_terminal_transition_and_explicit_material_reuse_only_expose_used_epochs() -> None:
    reference = _reference()
    terminal = _evaluate(reference, "terminal_transition")
    assert tuple(item.key_epoch for item in terminal.key_history) == (0,)
    assert terminal.ordered_envelopes[-1].next_public_key == bytes.fromhex(
        reference["vectors"]["successor"]["public_key_hex"]
    )
    returned = _evaluate(reference, "explicit_return_to_initial_key")
    assert tuple(item.key_epoch for item in returned.key_history) == (0, 1, 2)
    assert returned.key_history[0].public_key == returned.key_history[2].public_key
    assert returned.key_history[0].key_id == returned.key_history[2].key_id
    assert returned.key_history[2].introduced_by == returned.ordered_envelopes[-2].anchor_hash


def _track_real_work(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from easysynq_api.services.audit import checkpoint_v2, lineage

    counts: dict[str, Any] = {
        "inspect": 0,
        "verify": 0,
        "edge": 0,
        "materials": 0,
        "keys": [],
        "key_events": 0,
    }
    inspect = checkpoint_v2.inspect_envelope_route
    verify = checkpoint_v2.verify_envelope
    edge = lineage._edge_fault
    relation = lineage._Graph.required_relation
    authenticate = lineage._Graph.authenticate

    def inspected(*args: Any, **kwargs: Any) -> Any:
        counts["inspect"] += 1
        return inspect(*args, **kwargs)

    def verified(*args: Any, **kwargs: Any) -> Any:
        counts["verify"] += 1
        counts["keys"].append(kwargs["public_key"].public_bytes(Encoding.Raw, PublicFormat.Raw))
        return verify(*args, **kwargs)

    def assessed(*args: Any, **kwargs: Any) -> Any:
        counts["edge"] += 1
        return edge(*args, **kwargs)

    def related(graph: Any) -> Any:
        counts["materials"] = len(graph.material)
        return relation(graph)

    def authenticated(graph: Any, key_id: str) -> Any:
        counts["key_events"] += 1
        return authenticate(graph, key_id)

    monkeypatch.setattr(checkpoint_v2, "inspect_envelope_route", inspected)
    monkeypatch.setattr(checkpoint_v2, "verify_envelope", verified)
    monkeypatch.setattr(lineage, "_edge_fault", assessed)
    monkeypatch.setattr(lineage._Graph, "required_relation", related)
    monkeypatch.setattr(lineage._Graph, "authenticate", authenticated)
    return counts


@pytest.mark.parametrize(
    ("name", "verified", "edge_count"),
    [
        ("old_key_successor", 3, 3),
        ("unknown_key_successor", 2, 2),
        ("bad_known_signature", 3, 2),
        ("wrong_epoch_successor", 3, 3),
        ("detached_transition_cannot_enroll", 2, 1),
        ("detached_transition_removed", 1, 1),
        ("invalid_transition_cannot_enroll", 2, 2),
    ],
)
def test_declared_key_authentication_is_distinct_from_edge_authority(
    monkeypatch: pytest.MonkeyPatch, name: str, verified: int, edge_count: int
) -> None:
    reference = _reference()
    counts = _track_real_work(monkeypatch)
    observations = tuple(reversed(_observations_from_fixture(reference, name)))
    result = _evaluate(reference, name, observations)
    scenario = _scenario_by_name(reference, name)
    assert result.status == scenario["expected_status"]
    assert {issue.code for issue in result.issues} == set(scenario["expected_codes"])
    assert counts["verify"] == verified
    assert counts["edge"] == edge_count
    initial_public = bytes.fromhex(reference["vectors"]["anchor"]["public_key_hex"])
    expected_keys = [initial_public] * verified
    if name in {"bad_known_signature", "wrong_epoch_successor"}:
        expected_keys[-1] = bytes.fromhex(reference["vectors"]["successor"]["public_key_hex"])
    assert sorted(counts["keys"]) == sorted(expected_keys)
    _assert_assurance(result)


def _test_key(number: int) -> Ed25519PrivateKey:
    # Public deterministic synthetic test seeds; never operational signing material.
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(
            b"EasySynQ lineage synthetic test key " + str(number).encode("ascii")
        ).digest()
    )


def _key_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _key_id(key: Ed25519PrivateKey) -> str:
    return "ed25519-sha256:" + hashlib.sha256(_key_bytes(key)).hexdigest()


def _signed_body(
    payload: dict[str, Any], signer: Ed25519PrivateKey, next_signer: Ed25519PrivateKey | None = None
) -> bytes:
    checkpoint = copy.deepcopy(payload)
    if next_signer is not None:
        checkpoint.pop("next_key_signature", None)
        proof_bytes = _PROOF_DOMAIN + rfc8785.dumps(checkpoint)
        proof = next_signer.sign(proof_bytes)
        next_signer.public_key().verify(proof, proof_bytes)
        checkpoint["next_key_signature"] = base64.b64encode(proof).decode("ascii")
    canonical = rfc8785.dumps(checkpoint)
    signature = signer.sign(_SIGNATURE_DOMAIN + canonical)
    signer.public_key().verify(signature, _SIGNATURE_DOMAIN + canonical)
    return rfc8785.dumps(
        {
            "checkpoint": checkpoint,
            "signature": base64.b64encode(signature).decode("ascii"),
            "anchor_hash": hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest(),
        }
    )


def _observation(body: bytes, index: int = 0) -> lineage.EnvelopeObservation:
    from easysynq_api.services.audit import lineage

    return lineage.EnvelopeObservation("synthetic-witness", f"checkpoint/{index:05d}", "v1", body)


def _anchor_payload(reference: dict[str, Any], **updates: Any) -> dict[str, Any]:
    return {**reference["vectors"]["anchor"]["checkpoint"], **updates}


@pytest.mark.parametrize(
    ("updates", "fault"),
    [
        ({"key_epoch": "1", "sequence": "7", "latest_id": "1"}, "KEY_EPOCH_VIOLATION"),
        ({"sequence": "7", "latest_id": "1"}, "SEQUENCE_DISCONTINUITY"),
        ({"latest_id": "1"}, "AUDIT_HEAD_REGRESSION"),
        ({"latest_id": "42", "latest_row_hash": "7" * 64}, "AUDIT_HEAD_CONFLICT"),
    ],
)
def test_rejected_transition_reports_first_fault_without_releasing_next_material(
    monkeypatch: pytest.MonkeyPatch, updates: dict[str, str], fault: str
) -> None:
    reference = _reference()
    initial = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    next_key = _test_key(1)
    checkpoint = copy.deepcopy(reference["vectors"]["transition"]["checkpoint"])
    checkpoint.update(updates)
    checkpoint.update(
        next_key_id=_key_id(next_key),
        next_public_key=base64.b64encode(_key_bytes(next_key)).decode("ascii"),
        next_key_epoch=str(int(checkpoint["key_epoch"]) + 1),
    )
    transition = _signed_body(checkpoint, initial, next_key)
    child = _signed_body(
        _anchor_payload(
            reference,
            anchor_id=str(UUID(int=500)),
            previous_anchor_hash=json.loads(transition)["anchor_hash"],
            sequence=str(int(checkpoint["sequence"]) + 1),
            key_id=_key_id(next_key),
            key_epoch=checkpoint["next_key_epoch"],
        ),
        next_key,
    )
    observations = (
        _observation(bytes.fromhex(reference["vectors"]["anchor"]["body_hex"])),
        _observation(transition, 1),
        _observation(child, 2),
    )
    counts = _track_real_work(monkeypatch)
    result = _evaluate(reference, "valid_rotation", tuple(reversed(observations)))
    assert result.status == "failed"
    assert {issue.code for issue in result.issues} == {fault, "UNKNOWN_KEY"}
    assert result.failed_issues == result.incomplete_issues == 1
    assert counts["verify"] == counts["edge"] == 2
    assert counts["materials"] == 1
    _assert_assurance(result)


def test_locator_and_anchor_conflicts_keep_two_distinct_stable_sides() -> None:
    reference = _reference()
    base = _observations_from_fixture(reference, "reused_anchor_id")
    observations = (
        dataclasses.replace(base[0], object_key="same", version_id="same"),
        dataclasses.replace(base[0], object_key="same", version_id="same"),
        dataclasses.replace(base[1], object_key="same", version_id="same"),
        dataclasses.replace(base[1], object_key="same", version_id="same"),
    )
    for ordering in itertools.permutations(observations):
        result = _evaluate(reference, "reused_anchor_id", ordering)
        assert result.duplicate_observations == 2
        assert result.failed_issues == 2
        assert result.incomplete_issues == 1
        for issue in result.issues:
            if issue.code in {"ANCHOR_ID_CONFLICT", "IMMUTABLE_LOCATOR_CONFLICT"}:
                left, right = issue.observation_indexes
                assert ordering[left].body != ordering[right].body
                semantic = [
                    (
                        ordering[index].source_id,
                        ordering[index].object_key,
                        ordering[index].version_id,
                        hashlib.sha256(ordering[index].body).hexdigest(),
                    )
                    for index in (left, right)
                ]
                assert semantic == sorted(semantic)
                assert left == min(i for i, item in enumerate(ordering) if item == ordering[left])
                assert right == min(i for i, item in enumerate(ordering) if item == ordering[right])


def test_a_late_failure_dominates_exhaustive_counts_at_the_display_cap() -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    unknown = bytes.fromhex(reference["vectors"]["unknown_key"]["body_hex"])
    observations = tuple(_observation(unknown + b" " * index, index) for index in range(40))
    observations += (_observation(b"invalid", 40),)
    expected = None
    for ordering in (
        observations,
        tuple(reversed(observations)),
        observations[::2] + observations[1:][::2],
    ):
        result = _evaluate(
            reference, "valid_rotation", ordering, lineage.LineageLimits(4096, 2**24, 1)
        )
        assert result.status == "failed"
        assert result.failed_issues == 1
        assert result.incomplete_issues == 40
        assert result.issues_omitted == 40
        assert result.issues[0].code == "ENVELOPE_INVALID"
        _assert_assurance(result)
        semantic = _semantic_result(result, ordering)
        if expected is None:
            expected = semantic
        assert semantic == expected


def test_independent_categories_are_sorted_by_code_then_stable_subject() -> None:
    reference = _reference()
    unknown = bytes.fromhex(reference["vectors"]["unknown_key"]["body_hex"])
    bodies = (b"not-json", b"also-invalid", unknown, unknown + b" ")
    observations = tuple(_observation(body, i) for i, body in enumerate(bodies))
    result = _evaluate(reference, "valid_rotation", observations)
    assert tuple(issue.code for issue in result.issues) == (
        "ENVELOPE_INVALID",
        "ENVELOPE_INVALID",
        "UNKNOWN_KEY",
        "UNKNOWN_KEY",
    )
    for issues in (result.issues[:2], result.issues[2:]):
        hashes = [
            hashlib.sha256(observations[issue.observation_indexes[0]].body).hexdigest()
            for issue in issues
        ]
        assert hashes == sorted(hashes)


@pytest.mark.parametrize("kind", ["duplicates", "unknowns", "fork"])
def test_maximum_width_inputs_bound_authentication_edges_and_representatives(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    reference = _reference()
    anchor = bytes.fromhex(reference["vectors"]["anchor"]["body_hex"])
    if kind == "duplicates":
        observations = tuple(_observation(anchor, index) for index in range(4096))
    elif kind == "unknowns":
        unknown = bytes.fromhex(reference["vectors"]["unknown_key"]["body_hex"])
        observations = tuple(_observation(unknown + b" " * index, index) for index in range(4096))
    else:
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        observations = tuple(
            _observation(
                _signed_body(_anchor_payload(reference, anchor_id=str(UUID(int=index + 1))), key),
                index,
            )
            for index in range(4096)
        )
    counts = _track_real_work(monkeypatch)
    ordering = tuple(reversed(observations[::2] + observations[1:][::2]))
    result = _evaluate(reference, "valid_rotation", ordering)
    _assert_assurance(result)
    if kind == "duplicates":
        assert result.status == "consistent"
        assert result.duplicate_observations == 4095
        assert len(result.ordered_envelopes) == 1
        assert counts["inspect"] == counts["verify"] == counts["edge"] == 1
        assert counts["key_events"] == 1
    elif kind == "unknowns":
        assert result.status == "incomplete"
        assert result.incomplete_issues == 4096
        assert result.issues_omitted == 4096 - 32
        assert counts["inspect"] == 4096
        assert counts["verify"] == counts["edge"] == 0
        assert counts["key_events"] == 1
    else:
        assert result.status == "failed"
        assert result.failed_issues == 1
        assert result.incomplete_issues == 0
        assert result.issues[0].code == "LINEAGE_FORK"
        assert len(result.issues[0].observation_indexes) == 2
        assert (
            tuple(ordering[index] for index in result.issues[0].observation_indexes)
            == observations[:2]
        )
        assert counts["inspect"] == counts["verify"] == counts["edge"] == 4096
        assert counts["key_events"] == 1


def test_4096_transitions_allow_terminal_4097th_material_and_only_4096_used_epochs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    enrollment = _enrollment_from_fixture(reference)
    keys = [_test_key(index) for index in range(4097)]
    enrollment = dataclasses.replace(
        enrollment,
        bootstrap=dataclasses.replace(
            enrollment.bootstrap,
            initial_public_key=_key_bytes(keys[0]),
            initial_key_id=_key_id(keys[0]),
        ),
    )
    previous = enrollment.bootstrap.commitment_hash
    observations = []
    hashes = []
    for index in range(4096):
        body = _signed_body(
            _anchor_payload(
                reference,
                kind="key_transition",
                anchor_id=str(UUID(int=index + 1)),
                sequence=str(index + 1),
                previous_anchor_hash=previous,
                key_id=_key_id(keys[index]),
                key_epoch=str(index),
                next_key_id=_key_id(keys[index + 1]),
                next_public_key=base64.b64encode(_key_bytes(keys[index + 1])).decode("ascii"),
                next_key_epoch=str(index + 1),
            ),
            keys[index],
            keys[index + 1],
        )
        previous = json.loads(body)["anchor_hash"]
        hashes.append(previous)
        observations.append(_observation(body, index))
    counts = _track_real_work(monkeypatch)
    result = lineage.evaluate_lineage(
        enrollment, tuple(reversed(observations)), limits=lineage.LineageLimits(4096, 2**24, 32)
    )
    assert result.status == "consistent"
    assert tuple(item.anchor_hash for item in result.ordered_envelopes) == tuple(hashes)
    assert result.tip_sequence == 4096
    assert tuple(item.key_epoch for item in result.key_history) == tuple(range(4096))
    assert tuple(item.introduced_by for item in result.key_history) == (None, *hashes[:-1])
    assert result.ordered_envelopes[-1].next_public_key == _key_bytes(keys[-1])
    assert counts["inspect"] == counts["verify"] == counts["edge"] == 4096
    assert counts["materials"] == 4097
    assert counts["key_events"] == 4097
    _assert_assurance(result)


def _replace_path(record: Any, path: str, value: Any) -> Any:
    field, separator, rest = path.partition(".")
    if separator:
        value = _replace_path(getattr(record, field), rest, value)
    return dataclasses.replace(record, **{field: value})


def _complete_enrollment(reference: dict[str, Any]) -> lineage.StreamEnrollment:
    from easysynq_api.services.audit import lineage

    enrollment = _enrollment_from_fixture(reference)
    anchor = reference["vectors"]["anchor"]
    return dataclasses.replace(
        enrollment,
        bootstrap=dataclasses.replace(
            enrollment.bootstrap,
            audit_boundary=lineage.AuditHead(42, anchor["checkpoint"]["latest_row_hash"]),
        ),
        required_checkpoint=lineage.RequiredCheckpointPin(anchor["anchor_hash"], 1),
    )


def _assert_input_rejected(enrollment: Any, observations: Any, limits: Any = None) -> None:
    from easysynq_api.services.audit import lineage

    with pytest.raises(lineage.LineageInputError) as caught:
        lineage.evaluate_lineage(
            enrollment,
            observations,
            limits=lineage.LineageLimits(4096, 2**24, 32) if limits is None else limits,
        )
    assert str(caught.value) == "invalid lineage input"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert "SYNTHETIC-SENSITIVE" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("org_id", "00000000-0000-4000-8000-000000000001"),
        ("stream_id", None),
        ("bootstrap", {}),
        ("bootstrap.commitment_hash", "A" * 64),
        ("bootstrap.commitment_hash", "a" * 63),
        ("bootstrap.commitment_hash", "g" * 64),
        ("bootstrap.commitment_hash", b"a" * 64),
        ("bootstrap.initial_key_id", "ed25519-sha256:" + "A" * 64),
        ("bootstrap.initial_key_id", "sha256:" + "a" * 64),
        ("bootstrap.initial_key_id", "ed25519-sha256:" + "a" * 63),
        ("bootstrap.initial_key_id", b"ed25519-sha256:" + b"a" * 64),
        ("bootstrap.initial_public_key", b""),
        ("bootstrap.initial_public_key", bytes(31)),
        ("bootstrap.initial_public_key", bytes(33)),
        ("bootstrap.initial_public_key", bytearray(32)),
        ("bootstrap.initial_public_key", memoryview(bytes(32))),
        ("bootstrap.initial_public_key", "SYNTHETIC-SENSITIVE"),
        ("bootstrap.initial_key_epoch", True),
        ("bootstrap.initial_key_epoch", -1),
        ("bootstrap.initial_key_epoch", 2**63),
        ("bootstrap.initial_key_epoch", 0.0),
        ("bootstrap.initial_key_epoch", "0"),
        ("bootstrap.audit_boundary", {}),
        ("bootstrap.audit_boundary.latest_id", 0),
        ("bootstrap.audit_boundary.latest_id", True),
        ("bootstrap.audit_boundary.latest_id", 2**63),
        ("bootstrap.audit_boundary.latest_id", 1.0),
        ("bootstrap.audit_boundary.latest_row_hash", "a" * 65),
        ("bootstrap.audit_boundary.latest_row_hash", "a" * 63 + "\n"),
        ("required_checkpoint", {}),
        ("required_checkpoint.sequence", 0),
        ("required_checkpoint.sequence", False),
        ("required_checkpoint.sequence", 2**63),
        ("required_checkpoint.sequence", "1"),
        ("required_checkpoint.anchor_hash", "\u0661" * 64),
        ("required_checkpoint.anchor_hash", None),
    ],
)
def test_enrollment_requires_exact_bounded_public_values(path: str, value: Any) -> None:
    reference = _reference()
    enrollment = _replace_path(_complete_enrollment(reference), path, value)
    _assert_input_rejected(enrollment, _observations_from_fixture(reference, "valid_rotation"))


@pytest.mark.parametrize("field", ["maximum_observations", "maximum_total_bytes", "maximum_issues"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "1", None])
def test_limits_accept_only_positive_builtin_integers(field: str, value: Any) -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    limits = dataclasses.replace(lineage.LineageLimits(4096, 2**24, 32), **{field: value})
    _assert_input_rejected(_enrollment_from_fixture(reference), (), limits)


@pytest.mark.parametrize(
    ("field", "value"),
    [("maximum_observations", 4097), ("maximum_total_bytes", 2**24 + 1), ("maximum_issues", 33)],
)
def test_selected_limits_cannot_raise_implementation_ceilings(field: str, value: int) -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    limits = dataclasses.replace(lineage.LineageLimits(4096, 2**24, 32), **{field: value})
    _assert_input_rejected(_enrollment_from_fixture(reference), (), limits)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", ""),
        ("source_id", "s" * 129),
        ("source_id", "\u00e9"),
        ("source_id", "\n"),
        ("source_id", "\x7f"),
        ("source_id", b"witness"),
        ("object_key", ""),
        ("object_key", "x" * 1025),
        ("object_key", "\u00e9" * 513),
        ("object_key", "\U0001f600" * 257),
        ("object_key", "\ud800"),
        ("object_key", "\udfff"),
        ("object_key", "x\x00y"),
        ("object_key", "x\x1fy"),
        ("object_key", "x\x7fy"),
        ("object_key", "x\x80y"),
        ("object_key", "x\x9fy"),
        ("object_key", b"key"),
        ("version_id", ""),
        ("version_id", "v" * 1025),
        ("version_id", "\u00e9" * 513),
        ("version_id", "\ud800"),
        ("version_id", "x\nSYNTHETIC-SENSITIVE"),
        ("version_id", None),
        ("body", bytearray()),
        ("body", memoryview(b"body")),
        ("body", "body"),
        ("body", None),
    ],
)
def test_observation_fields_reject_bad_types_controls_and_unicode_bounds(
    field: str, value: Any
) -> None:
    reference = _reference()
    observation = dataclasses.replace(_observation(b"{}"), **{field: value})
    _assert_input_rejected(_enrollment_from_fixture(reference), (observation,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", " " + "s" * 126 + "~"),
        ("object_key", "x" * 1024),
        ("object_key", "\u00e9" * 512),
        ("object_key", "\U0001f600" * 256),
        ("object_key", "\u00a0"),
        ("version_id", "v" * 1024),
        ("version_id", "\u00e9" * 512),
    ],
)
def test_valid_locator_boundaries_are_correlation_metadata(field: str, value: str) -> None:
    reference = _reference()
    anchor = _observations_from_fixture(reference, "old_prefix_without_memory")[0]
    observation = dataclasses.replace(anchor, **{field: value})
    assert _evaluate(reference, "old_prefix_without_memory", (observation,)).status == "consistent"


def test_records_scalars_and_outer_collections_reject_subclasses_and_coercion() -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    enrollment = _complete_enrollment(reference)
    observation = _observations_from_fixture(reference, "valid_rotation")[0]
    limits = lineage.LineageLimits(4096, 2**24, 32)
    for value in ({}, None, (enrollment,), dataclasses.asdict(enrollment)):
        _assert_input_rejected(value, (observation,))
    for value in ([observation], iter((observation,)), {observation}, None):
        _assert_input_rejected(enrollment, value)
    for value in (None, {}, (4096, 2**24, 32)):
        with pytest.raises(lineage.LineageInputError):
            lineage.evaluate_lineage(enrollment, (observation,), limits=value)
    for value in ({}, None, observation.body):
        _assert_input_rejected(enrollment, (value,))

    def subclass_record(record: Any) -> Any:
        derived = type("DerivedRecord", (type(record),), {})
        return derived(
            **{field.name: getattr(record, field.name) for field in dataclasses.fields(record)}
        )

    _assert_input_rejected(subclass_record(enrollment), (observation,))
    _assert_input_rejected(enrollment, (subclass_record(observation),))
    _assert_input_rejected(enrollment, (observation,), subclass_record(limits))
    for path in ("bootstrap", "bootstrap.audit_boundary", "required_checkpoint"):
        current = enrollment
        for field in path.split("."):
            current = getattr(current, field)
        _assert_input_rejected(
            _replace_path(enrollment, path, subclass_record(current)), (observation,)
        )
    for path in (
        "org_id",
        "stream_id",
        "bootstrap.commitment_hash",
        "bootstrap.initial_key_id",
        "bootstrap.initial_public_key",
        "bootstrap.initial_key_epoch",
        "bootstrap.audit_boundary.latest_id",
        "bootstrap.audit_boundary.latest_row_hash",
        "required_checkpoint.anchor_hash",
        "required_checkpoint.sequence",
    ):
        current = enrollment
        for field in path.split("."):
            current = getattr(current, field)
        derived = type("DerivedScalar", (type(current),), {})
        value = derived(str(current)) if type(current) is UUID else derived(current)
        _assert_input_rejected(_replace_path(enrollment, path, value), (observation,))
    for field in ("source_id", "object_key", "version_id", "body"):
        current = getattr(observation, field)
        derived = type("DerivedField", (type(current),), {})
        _assert_input_rejected(
            enrollment, (dataclasses.replace(observation, **{field: derived(current)}),)
        )
    derived_tuple = type("DerivedTuple", (tuple,), {})
    _assert_input_rejected(enrollment, derived_tuple((observation,)))
    derived_int = type("DerivedInt", (int,), {})
    _assert_input_rejected(
        enrollment, (), dataclasses.replace(limits, maximum_issues=derived_int(1))
    )


def test_initial_public_material_uses_existing_admission_and_exact_fingerprint() -> None:
    reference = _reference()
    enrollment = _enrollment_from_fixture(reference)
    codec_fixture = json.loads(
        (_FIXTURE_PATH.parent / "audit_checkpoint_v2_vectors.json").read_bytes()
    )
    for case in codec_fixture["key_admissibility"]:
        if case["admissible"]:
            continue
        raw = bytes.fromhex(case["public_key_hex"])
        rejected = dataclasses.replace(
            enrollment,
            bootstrap=dataclasses.replace(
                enrollment.bootstrap,
                initial_public_key=raw,
                initial_key_id="ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
            ),
        )
        _assert_input_rejected(rejected, ())
    rejected = _replace_path(enrollment, "bootstrap.initial_key_id", "ed25519-sha256:" + "0" * 64)
    _assert_input_rejected(rejected, ())


@pytest.mark.parametrize("kind", ["count", "bytes"])
def test_resource_abort_precedes_material_or_body_work_and_leaves_pin_unassessed(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from easysynq_api.services.audit import checkpoint_v2, lineage

    reference = _reference()
    enrollment = _complete_enrollment(reference)
    enrollment = _replace_path(enrollment, "bootstrap.initial_public_key", bytes(32))
    anchor = _observations_from_fixture(reference, "valid_rotation")[0]
    observations = (anchor, object()) if kind == "count" else (anchor, _observation(b"bad", 1))
    limits = (
        lineage.LineageLimits(1, 2**24, 32) if kind == "count" else lineage.LineageLimits(2, 1, 32)
    )

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resource preflight must finish before cryptographic work")

    monkeypatch.setattr(checkpoint_v2, "public_key_id", unexpected)
    monkeypatch.setattr(checkpoint_v2, "inspect_envelope_route", unexpected)
    monkeypatch.setattr(checkpoint_v2, "verify_envelope", unexpected)
    result = lineage.evaluate_lineage(enrollment, observations, limits=limits)
    assert result.status == "incomplete"
    assert result.required_checkpoint_relation == "unassessed"
    assert result.issues == (lineage.LineageIssue("RESOURCE_LIMIT", "incomplete", ()),)
    assert result.duplicate_observations == result.failed_issues == 0
    assert result.incomplete_issues == 1
    _assert_assurance(result)


def test_aggregate_excess_does_not_hide_any_count_bounded_invalid_record() -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    good = _observation(b"x" * 20)
    bad = dataclasses.replace(good, version_id="SYNTHETIC-SENSITIVE\n")
    for ordering in ((good, bad), (bad, good)):
        _assert_input_rejected(
            _enrollment_from_fixture(reference), ordering, lineage.LineageLimits(2, 1, 1)
        )


def test_exact_aggregate_ceiling_is_processed_and_one_more_byte_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _reference()
    anchor = bytes.fromhex(reference["vectors"]["anchor"]["body_hex"])
    padded = anchor + b" " * (65536 - len(anchor))
    observations = tuple(_observation(padded, index) for index in range(256))
    counts = _track_real_work(monkeypatch)
    result = _evaluate(reference, "old_prefix_without_memory", observations)
    assert result.status == "consistent"
    assert result.duplicate_observations == 255
    assert counts["inspect"] == counts["verify"] == 1
    excess = _evaluate(
        reference, "old_prefix_without_memory", (*observations, _observation(b"x", 256))
    )
    assert excess.status == "incomplete"
    assert tuple(issue.code for issue in excess.issues) == ("RESOURCE_LIMIT",)
    assert excess.duplicate_observations == 0
    assert excess.required_checkpoint_relation == "not-provided"
    assert counts["inspect"] == counts["verify"] == 1
    _assert_assurance(excess)


@pytest.mark.parametrize("body", [b"", b"{}", b"\xff", b"[]", b"null", b"x" * 65537])
def test_well_typed_bounded_bad_bodies_are_report_issues(body: bytes) -> None:
    reference = _reference()
    result = _evaluate(reference, "valid_rotation", (_observation(body),))
    assert result.status == "failed"
    assert tuple(issue.code for issue in result.issues) == ("ENVELOPE_INVALID",)
    _assert_assurance(result)


@pytest.mark.parametrize("fault", ["signature", "hash", "next-proof"])
def test_unverified_route_never_substitutes_for_crypto_or_enrolls_bad_transition(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from easysynq_api.services.audit import checkpoint_v2

    reference = _reference()
    vector = reference["vectors"]["transition" if fault == "next-proof" else "anchor"]
    envelope = json.loads(bytes.fromhex(vector["body_hex"]))
    if fault == "signature":
        envelope["signature"] = base64.b64encode(bytes(64)).decode("ascii")
    elif fault == "hash":
        envelope["anchor_hash"] = "0" * 64
    else:
        envelope["checkpoint"]["next_key_signature"] = base64.b64encode(bytes(64)).decode("ascii")
        envelope = json.loads(
            _signed_body(
                envelope["checkpoint"], Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
            )
        )
    body = rfc8785.dumps(envelope)
    route = checkpoint_v2.inspect_envelope_route(body)
    assert route.key_id == vector["checkpoint"]["key_id"]
    counts = _track_real_work(monkeypatch)
    observations = (_observation(body),)
    if fault == "next-proof":
        observations += (
            _observation(bytes.fromhex(reference["vectors"]["anchor"]["body_hex"]), 1),
            _observation(bytes.fromhex(reference["vectors"]["successor"]["body_hex"]), 2),
        )
    result = _evaluate(reference, "valid_rotation", observations)
    assert result.status == "failed"
    assert {issue.code for issue in result.issues} == (
        {"ENVELOPE_INVALID", "UNKNOWN_KEY"} if fault == "next-proof" else {"ENVELOPE_INVALID"}
    )
    assert counts["materials"] == 1
    _assert_assurance(result)
    _assert_input_rejected(_enrollment_from_fixture(reference), (_observation(route),))


def test_authenticated_duplicate_count_excludes_invalid_foreign_and_unknown_bodies() -> None:
    reference = _reference()
    for name in ("foreign_identity", "unknown_key_successor", "bad_known_signature"):
        observations = _observations_from_fixture(reference, name)
        repeated = observations + (observations[-1],) * 3
        result = _evaluate(reference, name, repeated)
        assert result.duplicate_observations == 0
        _assert_assurance(result)
    for name in (
        "duplicate_deliveries",
        "equivalent_transport",
        "immutable_locator_transport_conflict",
    ):
        assert _evaluate(reference, name).duplicate_observations == 1


def test_evaluator_does_not_mutate_inputs_and_all_public_records_are_frozen_slots() -> None:
    from easysynq_api.services.audit import checkpoint_v2, lineage

    reference = _reference()
    enrollment = _complete_enrollment(reference)
    observations = _observations_from_fixture(reference, "valid_rotation")
    limits = lineage.LineageLimits(4096, 2**24, 32)
    before = copy.deepcopy((enrollment, observations, limits))
    result = lineage.evaluate_lineage(enrollment, observations, limits=limits)
    assert (enrollment, observations, limits) == before
    records = (
        enrollment,
        enrollment.bootstrap,
        enrollment.bootstrap.audit_boundary,
        enrollment.required_checkpoint,
        *observations,
        limits,
        result,
        *result.key_history,
        *result.ordered_envelopes,
        lineage.LineageIssue("ENVELOPE_INVALID", "failed", (0,)),
        checkpoint_v2.inspect_envelope_route(observations[0].body),
    )
    for record in records:
        assert dataclasses.is_dataclass(record)
        assert not hasattr(record, "__dict__")
        field = dataclasses.fields(record)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field, None)
        for field in dataclasses.fields(record):
            value = getattr(record, field.name)
            assert not isinstance(value, (list, dict, set, bytearray))
    for field in ("ordered_envelopes", "key_history", "issues", "unproved_checks"):
        assert type(getattr(result, field)) is tuple
    with pytest.raises(TypeError):
        observations[0].body[0] = 0


@pytest.mark.parametrize("seam", ["inspect_envelope_route", "verify_envelope", "public_key_id"])
def test_unexpected_resource_errors_propagate_instead_of_becoming_a_verdict(
    monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    from easysynq_api.services.audit import checkpoint_v2

    reference = _reference()

    def exhausted(*args: Any, **kwargs: Any) -> Any:
        raise MemoryError

    monkeypatch.setattr(checkpoint_v2, seam, exhausted)
    with pytest.raises(MemoryError):
        _evaluate(reference, "valid_rotation")


def test_wrong_declared_key_transition_cannot_release_material_despite_other_edge_faults() -> None:
    reference = _reference()
    initial = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    next_key = _test_key(909)
    payload = copy.deepcopy(reference["vectors"]["transition"]["checkpoint"])
    payload.update(
        anchor_id=str(UUID(int=909)),
        previous_anchor_hash=reference["vectors"]["transition"]["anchor_hash"],
        sequence="7",
        latest_id="1",
        next_key_id=_key_id(next_key),
        next_public_key=base64.b64encode(_key_bytes(next_key)).decode("ascii"),
    )
    transition = _signed_body(payload, initial, next_key)
    child = _signed_body(
        _anchor_payload(
            reference,
            anchor_id=str(UUID(int=910)),
            sequence="8",
            previous_anchor_hash=json.loads(transition)["anchor_hash"],
            key_id=_key_id(next_key),
            key_epoch="1",
        ),
        next_key,
    )
    observations = (
        *_observations_from_fixture(reference, "terminal_transition"),
        _observation(transition, 9),
        _observation(child, 10),
    )
    for ordering in itertools.permutations(observations):
        result = _evaluate(reference, "valid_rotation", ordering)
        assert result.status == "failed"
        assert result.failed_issues == result.incomplete_issues == 1
        assert {issue.code for issue in result.issues} == {"KEY_EPOCH_VIOLATION", "UNKNOWN_KEY"}
        _assert_assurance(result)


def test_terminal_transition_material_can_authenticate_a_detached_node_only_diagnostically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _reference()
    second = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    body = _signed_body(
        _anchor_payload(
            reference,
            anchor_id=str(UUID(int=911)),
            sequence="3",
            previous_anchor_hash="9" * 64,
            key_id=_key_id(second),
            key_epoch="1",
        ),
        second,
    )
    observations = (
        _observation(body, 9),
        *_observations_from_fixture(reference, "terminal_transition"),
    )
    counts = _track_real_work(monkeypatch)
    result = _evaluate(reference, "valid_rotation", observations)
    assert result.status == "incomplete"
    assert tuple(issue.code for issue in result.issues) == ("DISCONNECTED_GRAPH",)
    assert counts["verify"] == 3
    assert counts["edge"] == counts["materials"] == 2
    _assert_assurance(result)


def test_required_pin_same_hash_different_sequence_conflicts_and_uses_one_real_observation() -> (
    None
):
    from easysynq_api.services.audit import lineage

    reference = _reference()
    enrollment = _complete_enrollment(reference)
    enrollment = _replace_path(enrollment, "required_checkpoint.sequence", 2)
    observations = _observations_from_fixture(reference, "old_prefix_without_memory")
    result = lineage.evaluate_lineage(
        enrollment, observations, limits=lineage.LineageLimits(1, 2**24, 32)
    )
    assert result.required_checkpoint_relation == "conflicting"
    assert result.issues == (lineage.LineageIssue("REQUIRED_CHECKPOINT_CONFLICT", "failed", (0,)),)
    _assert_assurance(result)


def test_required_pin_on_rejected_edge_is_missing_even_with_an_authenticated_matching_body() -> (
    None
):
    from easysynq_api.services.audit import lineage

    reference = _reference()
    observations = _observations_from_fixture(reference, "old_key_successor")
    wrong = json.loads(observations[-1].body)
    enrollment = dataclasses.replace(
        _enrollment_from_fixture(reference),
        required_checkpoint=lineage.RequiredCheckpointPin(wrong["anchor_hash"], 3),
    )
    result = lineage.evaluate_lineage(
        enrollment, observations, limits=lineage.LineageLimits(4096, 2**24, 32)
    )
    assert result.required_checkpoint_relation == "missing"
    assert {issue.code for issue in result.issues} == {
        "KEY_EPOCH_VIOLATION",
        "REQUIRED_CHECKPOINT_MISSING",
    }
    assert result.failed_issues == result.incomplete_issues == 1
    _assert_assurance(result)


def test_locator_conflicts_and_canonical_representative_updates() -> None:
    reference = _reference()
    invalids = (_observation(b"bad"), _observation(b"worse"), _observation(b"bad"))
    result = _evaluate(reference, "valid_rotation", invalids)
    assert result.failed_issues == 3
    assert result.duplicate_observations == 0
    assert tuple(issue.code for issue in result.issues) == (
        "ENVELOPE_INVALID",
        "ENVELOPE_INVALID",
        "IMMUTABLE_LOCATOR_CONFLICT",
    )
    observations = _observations_from_fixture(reference, "old_key_successor")
    alternate = dataclasses.replace(
        observations[-1],
        object_key="a-first",
        body=json.dumps(json.loads(observations[-1].body), indent=2).encode(),
    )
    ordering = (*observations, alternate)
    result = _evaluate(reference, "valid_rotation", ordering)
    assert result.duplicate_observations == 1
    assert result.issues[0].code == "KEY_EPOCH_VIOLATION"
    assert result.issues[0].observation_indexes == (3,)


def test_anchor_id_conflict_includes_rejected_edges_without_manufacturing_a_fork() -> None:
    reference = _reference()
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    anchor = reference["vectors"]["anchor"]
    invalid_edge = _signed_body(
        _anchor_payload(reference, previous_anchor_hash=anchor["anchor_hash"], sequence="7"), key
    )
    observations = (_observation(bytes.fromhex(anchor["body_hex"])), _observation(invalid_edge, 1))
    result = _evaluate(reference, "valid_rotation", observations)
    assert result.status == "failed"
    assert result.failed_issues == 2
    assert result.incomplete_issues == 0
    assert {issue.code for issue in result.issues} == {
        "ANCHOR_ID_CONFLICT",
        "SEQUENCE_DISCONTINUITY",
    }


def test_unknown_structural_cycle_hints_remain_unresolved_without_guessed_key_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _reference()
    unknown = json.loads(bytes.fromhex(reference["vectors"]["unknown_key"]["body_hex"]))
    bodies = []
    for own, predecessor in (("8", "9"), ("9", "8")):
        # Deliberately unverified structural hints, not a claimed authentic hash cycle.
        envelope = copy.deepcopy(unknown)
        envelope["anchor_hash"] = own * 64
        envelope["checkpoint"]["previous_anchor_hash"] = predecessor * 64
        bodies.append(rfc8785.dumps(envelope))
    counts = _track_real_work(monkeypatch)
    result = _evaluate(
        reference, "valid_rotation", tuple(_observation(body, i) for i, body in enumerate(bodies))
    )
    assert result.status == "incomplete"
    assert result.incomplete_issues == 2
    assert {issue.code for issue in result.issues} == {"UNKNOWN_KEY"}
    assert counts["verify"] == counts["edge"] == 0
    _assert_assurance(result)


def test_enrolled_maximum_epoch_and_positive_head_are_exact_valid_integer_boundaries() -> None:
    from easysynq_api.services.audit import lineage

    reference = _reference()
    maximum = 2**63 - 1
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    body = _signed_body(
        _anchor_payload(reference, key_epoch=str(maximum), latest_id=str(maximum)), key
    )
    enrollment = _enrollment_from_fixture(reference)
    enrollment = dataclasses.replace(
        enrollment,
        bootstrap=dataclasses.replace(
            enrollment.bootstrap,
            initial_key_epoch=maximum,
            audit_boundary=lineage.AuditHead(
                maximum, reference["vectors"]["anchor"]["checkpoint"]["latest_row_hash"]
            ),
        ),
    )
    result = lineage.evaluate_lineage(
        enrollment, (_observation(body),), limits=lineage.LineageLimits(1, len(body), 1)
    )
    assert result.status == "consistent"
    assert result.key_history[0].key_epoch == maximum
    _assert_assurance(result)


def test_multiple_forks_count_per_parent_with_stable_subject_sort_and_bounded_representatives() -> (
    None
):
    from easysynq_api.services.audit import lineage

    reference = _reference()
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    anchor = reference["vectors"]["anchor"]
    observations = [_observation(bytes.fromhex(anchor["body_hex"]))]
    groups: dict[str, tuple[int, int]] = {}
    parents = []
    for index in (1, 2):
        body = _signed_body(
            _anchor_payload(
                reference,
                anchor_id=str(UUID(int=1000 + index)),
                sequence="2",
                previous_anchor_hash=anchor["anchor_hash"],
            ),
            key,
        )
        observations.append(_observation(body, index))
        parents.append(json.loads(body)["anchor_hash"])
    groups[anchor["anchor_hash"]] = (1, 2)
    for parent in parents:
        first = len(observations)
        for offset in (0, 1):
            index = first + offset
            body = _signed_body(
                _anchor_payload(
                    reference,
                    anchor_id=str(UUID(int=1000 + index)),
                    sequence="3",
                    previous_anchor_hash=parent,
                ),
                key,
            )
            observations.append(_observation(body, index))
        groups[parent] = (first, first + 1)
    for ordering in (tuple(observations), tuple(reversed(observations))):
        result = _evaluate(
            reference, "valid_rotation", ordering, lineage.LineageLimits(4096, 2**24, 2)
        )
        assert result.status == "failed"
        assert result.failed_issues == 3
        assert result.incomplete_issues == 0
        assert result.issues_omitted == 1
        assert tuple(issue.code for issue in result.issues) == ("LINEAGE_FORK", "LINEAGE_FORK")
        for issue, subject in zip(result.issues, sorted(groups)[:2], strict=True):
            assert tuple(ordering[i] for i in issue.observation_indexes) == tuple(
                observations[i] for i in groups[subject]
            )
        _assert_assurance(result)
