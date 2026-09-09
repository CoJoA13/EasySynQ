"""Independent public-fixture controls and bootstrap bridge behavior contracts."""

from __future__ import annotations

import base64
import copy
import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if TYPE_CHECKING:
    from easysynq_api.services.audit import bootstrap_bridge, lineage

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "audit_bootstrap_bridge_vectors.json"
)
_FIXTURE_SHA256 = "145343df71a42f35310acbacc446e0e9c7a8bf11e0e6ef9281f73e7c1df01d16"
_NAMESPACE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/namespace\0"
_BODY_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/raw-body\0"
_PAGE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/page\0"
_ROOT_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/root\0"
_SIGNATURE_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/signature\0"
_HASH_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/hash\0"
_PROOF_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0"

pytestmark = pytest.mark.unit


@dataclasses.dataclass(frozen=True, slots=True)
class _ReferenceCase:
    enrollment: bootstrap_bridge.BridgeEnrollment
    root_body: bytes | None
    pages: tuple[bootstrap_bridge.BridgePageObservation, ...]
    observations: tuple[bootstrap_bridge.LegacyObservation, ...]


def _reference() -> dict[str, Any]:
    raw = _FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    return json.loads(raw)


def _observation(
    item: dict[str, Any], reference: dict[str, Any], bridge: ModuleType
) -> bootstrap_bridge.LegacyObservation:
    witness = UUID(item["witness_id"])
    if item["kind"] == "gap":
        return bridge.WitnessCollectionGap(witness, item["reason"])
    locator = (witness, item["object_key"], item["version_id"])
    if item["kind"] == "body":
        raw = bytes.fromhex(reference["legacy_vectors"][item["vector"]]["body_hex"])
        return bridge.LegacyBodyObservation(*locator, raw)
    if item["kind"] == "unavailable":
        return bridge.LegacyUnavailableObservation(*locator)
    assert item["kind"] == "delete"
    return bridge.LegacyDeleteObservation(*locator)


def _reference_case(name: str, bridge: ModuleType) -> _ReferenceCase:
    """Materialize frozen scenario inputs without producing bytes or expected verdicts."""
    from easysynq_api.services.audit import lineage

    reference = _reference()
    scenario = next(item for item in reference["scenarios"] if item["name"] == name)
    package = reference["packages"][scenario["package"]]
    external = copy.deepcopy(scenario.get("enrollment_override", package["enrollment"]))
    omitted_keys = set(scenario.get("omit_legacy_key_ids", ()))
    pin = external["bootstrap"]
    head = pin["audit_boundary"]
    required = external["required_checkpoint"]
    enrollment = bridge.BridgeEnrollment(
        stream=lineage.StreamEnrollment(
            org_id=UUID(external["org_id"]),
            stream_id=UUID(external["stream_id"]),
            bootstrap=lineage.BootstrapPin(
                commitment_hash=pin["commitment_hash"],
                initial_key_id=pin["initial_key_id"],
                initial_public_key=bytes.fromhex(pin["initial_public_key_hex"]),
                initial_key_epoch=pin["initial_key_epoch"],
                audit_boundary=lineage.AuditHead(head["latest_id"], head["latest_row_hash"]),
            ),
            required_checkpoint=(
                None
                if required is None
                else lineage.RequiredCheckpointPin(required["anchor_hash"], required["sequence"])
            ),
        ),
        witnesses=tuple(
            bridge.BridgeWitnessPin(UUID(item["witness_id"]), item["namespace_hash"])
            for item in external["witnesses"]
        ),
        legacy_keys=tuple(
            bridge.LegacyPublicMaterial(item["key_id"], bytes.fromhex(item["public_key_hex"]))
            for item in external["legacy_keys"]
            if item["key_id"] not in omitted_keys
        ),
    )
    root_hex = scenario.get("root_body_override_hex", package["root_body_hex"])
    omitted_pages = set(scenario.get("omit_pages", ()))
    page_items = [item for index, item in enumerate(package["pages"]) if index not in omitted_pages]
    page_items.extend(
        reference["packages"][item["package"]]["pages"][item["index"]]
        for item in scenario.get("extra_pages", ())
    )
    omitted_observations = set(scenario.get("omit_observations", ()))
    replacements = {
        item["index"]: item["observation"] for item in scenario.get("replace_observations", ())
    }
    observation_items = [
        replacements.get(index, item)
        for index, item in enumerate(package["observations"])
        if index not in omitted_observations
    ]
    observation_items.extend(scenario.get("append_observations", ()))
    return _ReferenceCase(
        enrollment=enrollment,
        root_body=None if root_hex is None else bytes.fromhex(root_hex),
        pages=tuple(
            bridge.BridgePageObservation(bytes.fromhex(item["body_hex"])) for item in page_items
        ),
        observations=tuple(_observation(item, reference, bridge) for item in observation_items),
    )


def test_reference_crypto_and_hash_controls() -> None:
    """Establish the security falsifier's genuine signatures and complete pinned base."""
    reference = _reference()
    for material in reference["materials"].values():
        public = bytes.fromhex(material["public_key_hex"])
        assert material["key_id"] == "ed25519-sha256:" + hashlib.sha256(public).hexdigest()
    for namespace in reference["namespaces"]:
        canonical = rfc8785.dumps(namespace["namespace"])
        assert canonical == bytes.fromhex(namespace["canonical_namespace_hex"])
        assert (
            hashlib.sha256(_NAMESPACE_DOMAIN + canonical).hexdigest() == namespace["namespace_hash"]
        )

    valid_signatures = 0
    invalid_signatures = 0
    for vector in reference["legacy_vectors"].values():
        raw = bytes.fromhex(vector["body_hex"])
        assert len(raw) == vector["body_bytes"]
        assert hashlib.sha256(_BODY_DOMAIN + raw).hexdigest() == vector["body_hash"]
        envelope = json.loads(raw)
        payload = envelope["checkpoint"]
        timestamp = datetime.datetime.fromisoformat(payload["timestamp"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime.UTC)
        normalized = {
            "org_id": payload["org_id"],
            "latest_id": payload["latest_id"],
            "latest_row_hash": bytes.fromhex(payload["latest_row_hash"]).hex(),
            "timestamp": timestamp.astimezone(datetime.UTC).isoformat(),
        }
        canonical = rfc8785.dumps(normalized)
        assert canonical == bytes.fromhex(vector["canonical_payload_hex"])
        assert vector["head"] == {
            "latest_id": normalized["latest_id"],
            "latest_row_hash": normalized["latest_row_hash"],
        }
        signature = base64.b64decode(envelope["signature"], validate=True)
        assert signature == bytes.fromhex(vector["signature_hex"])
        public = bytes.fromhex(reference["materials"][vector["material"]]["public_key_hex"])
        key = Ed25519PublicKey.from_public_bytes(public)
        if vector["crypto_valid"]:
            key.verify(signature, canonical)
            valid_signatures += 1
        else:
            with pytest.raises(InvalidSignature):
                key.verify(signature, canonical)
            invalid_signatures += 1
    assert (valid_signatures, invalid_signatures) == (14, 1)

    for control in reference["legacy_numeric_controls"]:
        value = {"latest_id": int(control["value_decimal"])}
        if control["supported"]:
            assert rfc8785.dumps(value) == bytes.fromhex(control["canonical_hex"])
        else:
            with pytest.raises(rfc8785.IntegerDomainError):
                rfc8785.dumps(value)

    for package in reference["packages"].values():
        root_raw = bytes.fromhex(package["root_body_hex"])
        assert rfc8785.dumps(json.loads(root_raw)) == root_raw
        assert hashlib.sha256(_ROOT_DOMAIN + root_raw).hexdigest() == package["root_hash"]
        assert package["root_hash"] == package["enrollment"]["bootstrap"]["commitment_hash"]
        for page in package["pages"]:
            page_raw = bytes.fromhex(page["body_hex"])
            assert rfc8785.dumps(json.loads(page_raw)) == page_raw
            assert hashlib.sha256(_PAGE_DOMAIN + page_raw).hexdigest() == page["page_hash"]

    baseline = reference["packages"]["baseline"]
    root = json.loads(bytes.fromhex(baseline["root_body_hex"]))
    entries = []
    for page, commitment in zip(baseline["pages"], root["pages"], strict=True):
        decoded = json.loads(bytes.fromhex(page["body_hex"]))
        assert commitment == {
            "page_index": str(page["page_index"]),
            "entry_count": str(page["entry_count"]),
            "page_hash": page["page_hash"],
        }
        assert len(decoded["entries"]) == page["entry_count"]
        entries.extend(decoded["entries"])
    assert [page["entry_count"] for page in baseline["pages"]] == [512, 2]
    assert len(entries) == len(baseline["observations"]) == int(root["entry_count"]) == 514
    locators = [
        (entry["witness_id"], entry["object_key"], entry["version_id"]) for entry in entries
    ]
    assert locators == sorted(set(locators))
    assert len(root["witnesses"]) == 2
    assert any(entry["version_id"] == "null" for entry in entries)
    for entry, observation in zip(entries, baseline["observations"], strict=True):
        for field in ("witness_id", "object_key", "version_id"):
            assert entry[field] == observation[field]
        vector = reference["legacy_vectors"][observation["vector"]]
        assert entry["body_hash"] == vector["body_hash"]
        assert int(entry["body_bytes"]) == vector["body_bytes"]
    unlisted = next(item for item in reference["scenarios"] if item["name"] == "unlisted_old")
    extra = unlisted["append_observations"][0]
    assert (extra["witness_id"], extra["object_key"], extra["version_id"]) not in locators
    assert reference["legacy_vectors"][extra["vector"]]["head"]["latest_id"] == 5
    assert {
        reference["legacy_vectors"][item["vector"]]["head"]["latest_id"]
        for item in baseline["observations"]
    } == {10, 42}
    assert unlisted["expected_status"] == "failed"
    assert unlisted["expected_codes"] == ["UNLISTED_AUTHENTIC_LEGACY"]

    previous = baseline["root_hash"]
    for vector in reference["v2_composition"]:
        envelope = json.loads(bytes.fromhex(vector["body_hex"]))
        checkpoint = envelope["checkpoint"]
        canonical = rfc8785.dumps(checkpoint)
        assert canonical == bytes.fromhex(vector["canonical_payload_hex"])
        assert checkpoint["previous_anchor_hash"] == previous
        public = bytes.fromhex(reference["materials"][vector["material"]]["public_key_hex"])
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, _SIGNATURE_DOMAIN + canonical)
        assert (
            hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest()
            == vector["anchor_hash"]
            == envelope["anchor_hash"]
        )
        if checkpoint["kind"] == "key_transition":
            proof_payload = {
                key: value for key, value in checkpoint.items() if key != "next_key_signature"
            }
            next_public = base64.b64decode(checkpoint["next_public_key"], validate=True)
            Ed25519PublicKey.from_public_bytes(next_public).verify(
                base64.b64decode(checkpoint["next_key_signature"], validate=True),
                _PROOF_DOMAIN + rfc8785.dumps(proof_payload),
            )
        previous = envelope["anchor_hash"]


def test_authenticated_unlisted_old_body_cannot_be_omitted() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("unlisted_old", bridge)
    result = bridge.evaluate_bootstrap_bridge(
        case.enrollment,
        case.root_body,
        case.pages,
        case.observations,
        limits=bridge.BridgeLimits(4096, 16 * 1024 * 1024, 32),
    )
    assert result.status == "failed"
    assert {issue.code for issue in result.issues} == {"UNLISTED_AUTHENTIC_LEGACY"}
    assert result.usable_bootstrap_pin is None
    assert result.established_checks == ()


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
_ESTABLISHED = (
    "external-root-content-binding",
    "committed-page-and-locator-closure",
    "retained-legacy-signature-authentication",
    "supplied-observation-reconciliation",
    "per-witness-signed-boundary-binding",
)


def _evaluate(
    case: _ReferenceCase, *, maximum_issues: int = 32
) -> bootstrap_bridge.BridgeEvaluation:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    return bridge.evaluate_bootstrap_bridge(
        case.enrollment,
        case.root_body,
        case.pages,
        case.observations,
        limits=bridge.BridgeLimits(4096, 16 * 1024 * 1024, maximum_issues),
    )


def _assert_result(
    result: bootstrap_bridge.BridgeEvaluation,
    status: str,
    codes: set[str],
    *,
    failed: int | None = None,
    incomplete: int | None = None,
) -> None:
    assert result.status == status
    assert {issue.code for issue in result.issues} == codes
    assert result.scope == "supplied-legacy-bootstrap-package"
    assert result.unproved_checks == _UNPROVED
    assert result.issues_omitted == 0
    if failed is not None:
        assert result.failed_issues == failed
    if incomplete is not None:
        assert result.incomplete_issues == incomplete
    for issue in result.issues:
        assert len(issue.observation_indexes) + len(issue.page_indexes) <= 2
    if status == "consistent":
        assert result.established_checks == _ESTABLISHED
        assert result.usable_bootstrap_pin is not None
    else:
        assert result.established_checks == result.witness_summaries == ()
        assert result.usable_bootstrap_pin is None


def _repin(
    case: _ReferenceCase,
    *,
    root: dict[str, Any] | None = None,
    page_documents: list[dict[str, Any]] | None = None,
) -> _ReferenceCase:
    """Independent test producer for deliberately changed commitments; no production codec."""
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    root = json.loads(case.root_body) if root is None else copy.deepcopy(root)
    pages = case.pages
    if page_documents is not None:
        raw_pages = [rfc8785.dumps(page) for page in page_documents]
        pages = tuple(bridge.BridgePageObservation(raw) for raw in raw_pages)
        root["pages"] = [
            {
                "page_index": str(page["page_index"]),
                "entry_count": str(len(page["entries"])),
                "page_hash": hashlib.sha256(_PAGE_DOMAIN + raw).hexdigest(),
            }
            for page, raw in zip(page_documents, raw_pages, strict=True)
        ]
    raw_root = rfc8785.dumps(root)
    pin = dataclasses.replace(
        case.enrollment.stream.bootstrap,
        commitment_hash=hashlib.sha256(_ROOT_DOMAIN + raw_root).hexdigest(),
    )
    enrollment = dataclasses.replace(
        case.enrollment, stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin)
    )
    return dataclasses.replace(case, enrollment=enrollment, root_body=raw_root, pages=pages)


def _committed_body_variant(vector_name: str) -> _ReferenceCase:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    vector = _reference()["legacy_vectors"][vector_name]
    documents = [json.loads(page.body) for page in case.pages]
    documents[0]["entries"][0].update(
        body_hash=vector["body_hash"], body_bytes=str(vector["body_bytes"])
    )
    observations = list(case.observations)
    observations[0] = dataclasses.replace(observations[0], body=bytes.fromhex(vector["body_hex"]))
    return _repin(
        dataclasses.replace(case, observations=tuple(observations)), page_documents=documents
    )


def test_consistent_reference_package() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    case = _reference_case("consistent", bridge)
    result = _evaluate(case)
    _assert_result(result, "consistent", set(), failed=0, incomplete=0)
    assert result.usable_bootstrap_pin is case.enrollment.stream.bootstrap
    assert result.duplicate_body_observations == result.duplicate_page_observations == 0
    witness_ids = sorted(item.witness_id for item in case.enrollment.witnesses)
    assert [item.witness_id for item in result.witness_summaries] == witness_ids
    old = _reference()["legacy_vectors"]["old"]["head"]
    assert result.witness_summaries == (
        bridge.BridgeWitnessSummary(
            witness_ids[0],
            513,
            lineage.AuditHead(**old),
            case.enrollment.stream.bootstrap.audit_boundary,
        ),
        bridge.BridgeWitnessSummary(
            witness_ids[1],
            1,
            case.enrollment.stream.bootstrap.audit_boundary,
            case.enrollment.stream.bootstrap.audit_boundary,
        ),
    )
    for field in ("verified", "complete_history", "independent_attestation", "activation"):
        assert not hasattr(result, field)


@pytest.mark.parametrize("name", [item["name"] for item in _reference()["scenarios"]])
def test_frozen_reference_scenarios(name: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    expected = next(item for item in _reference()["scenarios"] if item["name"] == name)
    result = _evaluate(_reference_case(name, bridge))
    _assert_result(result, expected["expected_status"], set(expected["expected_codes"]))
    assert result.duplicate_body_observations == expected.get("expected_duplicate_bodies", 0)
    assert result.duplicate_page_observations == expected.get("expected_duplicate_pages", 0)


def test_conflicting_raw_transports_at_one_locator_keep_both_issues() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("same_locator_transport_conflict", bridge)
    result = _evaluate(case)
    _assert_result(
        result,
        "failed",
        {"IMMUTABLE_LOCATOR_CONFLICT", "LEGACY_BODY_COMMITMENT_MISMATCH"},
        failed=2,
        incomplete=0,
    )
    issues = {issue.code: issue for issue in result.issues}
    assert set(issues["IMMUTABLE_LOCATOR_CONFLICT"].observation_indexes) == {0, 514}
    assert issues["LEGACY_BODY_COMMITMENT_MISMATCH"].observation_indexes == (514,)
    assert all(issue.page_indexes == () for issue in result.issues)


def test_cross_page_duplicate_is_first_manifest_fault() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("manifest_duplicate", bridge)
    assert [len(json.loads(page.body)["entries"]) for page in case.pages] == [512, 2]
    result = _evaluate(case)
    _assert_result(result, "failed", {"MANIFEST_LOCATOR_DUPLICATE"}, failed=1, incomplete=0)
    assert result.issues[0].page_indexes == (0, 1)
    assert result.issues[0].observation_indexes == ()


def test_missing_page_does_not_invent_unlisted_body() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("missing_page", bridge)
    result = _evaluate(case)
    _assert_result(result, "incomplete", {"PAGE_MISSING"}, failed=0, incomplete=1)
    assert result.issues[0].observation_indexes == result.issues[0].page_indexes == ()


@pytest.mark.parametrize(
    "root_name", ["missing_root", "invalid_root", "root_commitment_mismatch", "namespace_mismatch"]
)
def test_invalid_root_does_not_claim_keyset_or_membership(root_name: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case(root_name, bridge)
    case = dataclasses.replace(
        case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=())
    )
    result = _evaluate(case)
    assert not {
        "LEGACY_KEY_MISSING",
        "LEGACY_KEYSET_MISMATCH",
        "LEGACY_BODY_MISSING",
        "UNLISTED_AUTHENTIC_LEGACY",
    } & {issue.code for issue in result.issues}
    assert result.incomplete_issues == (3 if root_name == "missing_root" else 2)
    assert result.usable_bootstrap_pin is None
    assert "LEGACY_AUTHENTICATION_UNESTABLISHED" in {issue.code for issue in result.issues}


def test_missing_retained_key_and_exact_body_remain_incomplete() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    result = _evaluate(_reference_case("missing_key_and_body", bridge))
    _assert_result(
        result,
        "incomplete",
        {"LEGACY_KEY_MISSING", "LEGACY_BODY_MISSING", "LEGACY_AUTHENTICATION_UNESTABLISHED"},
        failed=0,
        incomplete=3,
    )
    issues = {issue.code: issue for issue in result.issues}
    assert issues["LEGACY_KEY_MISSING"].observation_indexes == ()
    assert issues["LEGACY_BODY_MISSING"].observation_indexes == ()
    assert issues["LEGACY_AUTHENTICATION_UNESTABLISHED"].observation_indexes == (512,)


def test_root_namespace_binding_cannot_be_replaced() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    result = _evaluate(_reference_case("namespace_mismatch", bridge))
    _assert_result(result, "failed", {"ROOT_ENROLLMENT_MISMATCH"}, failed=1, incomplete=0)
    assert result.issues[0].observation_indexes == result.issues[0].page_indexes == ()


@pytest.mark.parametrize(
    "field",
    [
        "org_id",
        "stream_id",
        "initial_key_id",
        "initial_public_key",
        "initial_key_epoch",
        "audit_boundary",
    ],
)
def test_root_external_bindings_are_not_self_enrolled(field: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    if field in ("org_id", "stream_id"):
        root[field] = str(UUID(int=999))
    elif field == "initial_key_id":
        root[field] = _reference()["materials"]["v2_next"]["key_id"]
    elif field == "initial_public_key":
        root[field] = base64.b64encode(
            bytes.fromhex(_reference()["materials"]["v2_next"]["public_key_hex"])
        ).decode()
    elif field == "initial_key_epoch":
        root[field] = "1"
    else:
        root[field]["latest_id"] = "43"
        for witness in root["witnesses"]:
            witness["highest_head"]["latest_id"] = "43"
    _assert_result(_evaluate(_repin(case, root=root)), "failed", {"ROOT_ENROLLMENT_MISMATCH"})


@pytest.mark.parametrize(
    "name",
    ["old_offset", "old_naive", "old_z", "old_upper_hash", "old_base64_padbits", "old_transport"],
)
def test_legacy_offset_transport_matches_unchanged_verifier(name: str) -> None:
    from easysynq_api.services.audit import checkpoint, sink, trust

    case = _committed_body_variant(name)
    observation = case.observations[0]
    doc = json.loads(observation.body.decode("utf-8"), object_pairs_hook=sink._unique_json_object)
    verifier = trust.legacy_verifier(
        tuple(
            trust.TrustedLegacyKey(item.key_id, Ed25519PublicKey.from_public_bytes(item.public_key))
            for item in case.enrollment.legacy_keys
        )
    )
    authenticated, reason = checkpoint._authenticate_offhost_doc_with_verifier(
        case.enrollment.stream.org_id, verifier, doc
    )
    assert reason is None and authenticated is not None
    assert authenticated.latest_id == 10
    _assert_result(_evaluate(case), "consistent", set(), failed=0, incomplete=0)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "wrong-org",
        "boolean",
        "float",
        "huge",
        "unsupported-boundary",
        "version",
        "timezone-overflow",
        "nested",
        "invalid-utf8",
        "too-large",
    ],
)
def test_legacy_malformed_evidence_is_not_authentication_uncertainty(mutation: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    original = case.observations[0]
    doc = json.loads(original.body)
    if mutation == "duplicate":
        raw = original.body.replace(b'"checkpoint":', b'"signature":"x","checkpoint":', 1)
    elif mutation == "invalid-utf8":
        raw = b"\xff"
    elif mutation == "too-large":
        raw = b" " * 65537
    elif mutation == "nested":
        raw = b"[" * 1000 + b"]" * 1000
    else:
        if mutation == "wrong-org":
            doc["checkpoint"]["org_id"] = str(UUID(int=999))
        elif mutation == "version":
            doc["format_version"] = 2
        elif mutation == "timezone-overflow":
            doc["checkpoint"]["timestamp"] = "0001-01-01T00:00:00+23:59"
        else:
            doc["checkpoint"]["latest_id"] = {
                "boolean": True,
                "float": 10.0,
                "huge": 10**100,
                "unsupported-boundary": 2**53,
            }[mutation]
        raw = json.dumps(doc).encode()
    # No root means key completeness and membership are unassessed, but malformed remains failed.
    case = dataclasses.replace(
        case,
        root_body=None,
        pages=(),
        observations=(dataclasses.replace(original, body=raw),),
        enrollment=dataclasses.replace(case.enrollment, legacy_keys=()),
    )
    _assert_result(
        _evaluate(case), "failed", {"ROOT_MISSING", "LEGACY_BODY_INVALID"}, failed=1, incomplete=1
    )


def test_maximum_legacy_supported_integer_is_authenticated_before_boundary_comparison() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    raw = bytes.fromhex(_reference()["legacy_vectors"]["legacy_max_integer"]["body_hex"])
    extra = dataclasses.replace(case.observations[0], version_id="extra-max", body=raw)
    _assert_result(
        _evaluate(dataclasses.replace(case, observations=(*case.observations, extra))),
        "failed",
        {"UNLISTED_AUTHENTIC_LEGACY", "ABOVE_BOOTSTRAP_BOUNDARY"},
        failed=2,
        incomplete=0,
    )


@pytest.mark.parametrize("record", ["unavailable", "delete", "gap"])
def test_collection_failures_are_sticky_beside_successful_delivery(record: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    body = case.observations[0]
    locator = (body.witness_id, body.object_key, body.version_id)
    extras = {
        "unavailable": bridge.LegacyUnavailableObservation(*locator),
        "delete": bridge.LegacyDeleteObservation(*locator),
        "gap": bridge.WitnessCollectionGap(body.witness_id, "listing-unavailable"),
    }
    code = {
        "unavailable": "LEGACY_BODY_UNAVAILABLE",
        "delete": "LEGACY_DELETE_MARKER",
        "gap": "WITNESS_COLLECTION_GAP",
    }[record]
    result = _evaluate(dataclasses.replace(case, observations=(*case.observations, extras[record])))
    _assert_result(result, "failed" if record == "delete" else "incomplete", {code})


def test_every_required_witness_needs_its_exact_body() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    # Copying the second witness's bytes to another version does not replace its named locator.
    other = dataclasses.replace(case.observations[-1], version_id="copied")
    result = _evaluate(dataclasses.replace(case, observations=(*case.observations[:-1], other)))
    _assert_result(
        result,
        "failed",
        {"LEGACY_BODY_MISSING", "UNLISTED_AUTHENTIC_LEGACY"},
        failed=1,
        incomplete=1,
    )
    result = _evaluate(dataclasses.replace(case, observations=case.observations[:-1]))
    _assert_result(result, "incomplete", {"LEGACY_BODY_MISSING"}, failed=0, incomplete=1)


def test_same_id_conflicts_survive_newer_boundary_and_duplicate_deliveries() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("old_head_conflict", bridge)
    case = dataclasses.replace(case, observations=(case.observations[0],) * 12 + case.observations)
    result = _evaluate(case)
    _assert_result(
        result,
        "failed",
        {"UNLISTED_AUTHENTIC_LEGACY", "SIGNED_HEAD_CONFLICT"},
        failed=2,
        incomplete=0,
    )
    issue = next(item for item in result.issues if item.code == "SIGNED_HEAD_CONFLICT")
    assert len(issue.observation_indexes) == 2
    rows = [
        json.loads(case.observations[index].body)["checkpoint"]["latest_row_hash"]
        for index in issue.observation_indexes
    ]
    assert len(set(rows)) == 2
    assert result.duplicate_body_observations == 12


@pytest.mark.parametrize("kind", ["object-prefix", "ineligible-name", "witness"])
def test_out_of_scope_bodies_are_not_authenticated_or_matched(kind: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    observation = case.observations[0]
    if kind == "object-prefix":
        observation = dataclasses.replace(observation, object_key="checkpoints/wrong/1-old")
    elif kind == "ineligible-name":
        observation = dataclasses.replace(
            observation, object_key=f"checkpoints/{case.enrollment.stream.org_id}/not-legacy"
        )
    else:
        observation = dataclasses.replace(observation, witness_id=UUID(int=999))
    observation = dataclasses.replace(observation, body=b"invalid")
    _assert_result(
        _evaluate(dataclasses.replace(case, observations=(*case.observations, observation))),
        "failed",
        {"OBSERVATION_SCOPE_MISMATCH"},
        failed=1,
        incomplete=0,
    )


def _semantic_issues(
    result: bootstrap_bridge.BridgeEvaluation, case: _ReferenceCase
) -> tuple[Any, ...]:
    return tuple(
        (
            issue.code,
            issue.severity,
            tuple(
                sorted(
                    (
                        str(case.observations[index].witness_id),
                        getattr(case.observations[index], "object_key", ""),
                        getattr(case.observations[index], "version_id", ""),
                        hashlib.sha256(getattr(case.observations[index], "body", b"")).hexdigest(),
                    )
                    for index in issue.observation_indexes
                )
            ),
            tuple(
                sorted(
                    hashlib.sha256(case.pages[index].body).hexdigest()
                    for index in issue.page_indexes
                )
            ),
        )
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "name",
    [
        "consistent",
        "same_locator_transport_conflict",
        "manifest_duplicate",
        "missing_key_and_body",
        "old_head_conflict",
    ],
)
def test_input_permutations_preserve_semantic_diagnostics(name: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case(name, bridge)
    expected = _evaluate(case)
    for observations in (case.observations[::-1], case.observations[1:] + case.observations[:1]):
        changed = dataclasses.replace(
            case,
            observations=observations,
            pages=case.pages[::-1],
            enrollment=dataclasses.replace(
                case.enrollment,
                legacy_keys=case.enrollment.legacy_keys[::-1],
                witnesses=case.enrollment.witnesses[::-1],
            ),
        )
        actual = _evaluate(changed)
        assert (
            actual.status,
            actual.failed_issues,
            actual.incomplete_issues,
            actual.usable_bootstrap_pin,
            actual.witness_summaries,
        ) == (
            expected.status,
            expected.failed_issues,
            expected.incomplete_issues,
            expected.usable_bootstrap_pin,
            expected.witness_summaries,
        )
        assert _semantic_issues(actual, changed) == _semantic_issues(expected, case)


def test_equivalent_wire_json_is_canonical_duplicate_transport() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    page = bridge.BridgePageObservation(
        json.dumps(json.loads(case.pages[0].body), indent=2, sort_keys=False).encode()
    )
    result = _evaluate(
        dataclasses.replace(
            case,
            root_body=json.dumps(json.loads(case.root_body), indent=2).encode(),
            pages=(*case.pages, page),
        )
    )
    _assert_result(result, "consistent", set())
    assert result.duplicate_page_observations == 1


@pytest.mark.parametrize("target", ["root", "page"])
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "deep",
        "float-version",
        "bool-version",
        "unknown-version",
        "extra-member",
        "empty-array",
        "bad-uuid",
        "bad-utf8",
        "scalar-surrogate",
        "oversize",
    ],
)
def test_new_wire_rejection_is_bounded_and_has_first_category(target: str, mutation: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    raw = case.root_body if target == "root" else case.pages[0].body
    doc = json.loads(raw)
    if mutation == "duplicate":
        raw = raw.replace(b'"kind":', b'"kind":"x","kind":', 1)
    elif mutation == "deep":
        raw = b"[" * 7 + b"]" * 7
    elif mutation == "bad-utf8":
        raw = b"\xff"
    elif mutation == "oversize":
        raw = b" " * (262145 if target == "root" else 2097153)
    else:
        if mutation.endswith("version"):
            doc["format_version"] = {
                "float-version": 1.0,
                "bool-version": True,
                "unknown-version": 2,
            }[mutation]
        elif mutation == "extra-member":
            doc["extra"] = "x"
        elif mutation == "empty-array":
            doc["witnesses" if target == "root" else "entries"] = []
        elif mutation == "bad-uuid":
            doc["org_id"] = "ABC"
        else:
            doc["kind"] = "\ud800"
        raw = json.dumps(doc).encode()
    changed = (
        dataclasses.replace(case, root_body=raw)
        if target == "root"
        else dataclasses.replace(case, pages=(bridge.BridgePageObservation(raw), case.pages[1]))
    )
    expected = {"ROOT_INVALID"} if target == "root" else {"PAGE_INVALID", "PAGE_MISSING"}
    _assert_result(_evaluate(changed), "failed", expected)


def test_same_index_competing_page_does_not_replace_committed_page() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    doc = json.loads(case.pages[0].body)
    doc["entries"][0]["version_id"] = "different-version"
    extra = bridge.BridgePageObservation(rfc8785.dumps(doc))
    result = _evaluate(dataclasses.replace(case, pages=(*case.pages, extra)))
    _assert_result(result, "failed", {"PAGE_CONFLICT", "PAGE_UNLISTED"}, failed=2, incomplete=0)
    assert set(
        next(item for item in result.issues if item.code == "PAGE_CONFLICT").page_indexes
    ) == {0, 2}
    result = _evaluate(dataclasses.replace(case, pages=(extra, case.pages[1])))
    _assert_result(result, "failed", {"PAGE_UNLISTED", "PAGE_MISSING"}, failed=1, incomplete=1)


def test_page_identity_failure_is_independent_of_root_availability() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    doc = json.loads(case.pages[0].body)
    doc["stream_id"] = str(UUID(int=999))
    changed = dataclasses.replace(
        case, root_body=None, pages=(bridge.BridgePageObservation(rfc8785.dumps(doc)),)
    )
    _assert_result(
        _evaluate(changed),
        "failed",
        {"ROOT_MISSING", "PAGE_IDENTITY_MISMATCH"},
        failed=1,
        incomplete=1,
    )


def test_extra_retained_key_cannot_silently_change_authoritative_inventory() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    extra = _reference()["materials"]["unenrolled"]
    keys = (
        *case.enrollment.legacy_keys,
        bridge.LegacyPublicMaterial(extra["key_id"], bytes.fromhex(extra["public_key_hex"])),
    )
    changed = dataclasses.replace(
        case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=keys)
    )
    _assert_result(_evaluate(changed), "failed", {"LEGACY_KEYSET_MISMATCH"}, failed=1, incomplete=0)


def _sized_package(count: int) -> _ReferenceCase:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    old, boundary_one, boundary_two = (
        case.observations[0],
        case.observations[-2],
        case.observations[-1],
    )
    observations = [
        dataclasses.replace(
            old,
            object_key=f"checkpoints/{case.enrollment.stream.org_id}/{index:010d}-old",
            version_id=f"v-{index}",
        )
        for index in range(count - 2)
    ]
    observations.extend((boundary_one, boundary_two))
    observations.sort(key=lambda item: (str(item.witness_id), item.object_key, item.version_id))
    entries = [
        {
            "witness_id": str(item.witness_id),
            "object_key": item.object_key,
            "version_id": item.version_id,
            "body_hash": hashlib.sha256(_BODY_DOMAIN + item.body).hexdigest(),
            "body_bytes": str(len(item.body)),
        }
        for item in observations
    ]
    template = json.loads(case.pages[0].body)
    pages = [
        dict(template, page_index=str(index // 512), entries=entries[index : index + 512])
        for index in range(0, len(entries), 512)
    ]
    root = json.loads(case.root_body)
    root["entry_count"] = str(count)
    root["witnesses"][0]["entry_count"] = str(count - 1)
    return _repin(
        dataclasses.replace(case, observations=tuple(observations)), root=root, page_documents=pages
    )


def test_maximum_capacity_is_bounded_and_all_distinct_bodies_use_retained_keys_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import legacy_checkpoint_compat as legacy

    case = _sized_package(4096)
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
    root["legacy_key_ids"] = sorted(item.key_id for item in keys)
    case = _repin(
        dataclasses.replace(
            case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=tuple(keys))
        ),
        root=root,
    )
    # Exercise all four exact implementation ceilings together, with legal page transports.
    current = len(case.root_body) + sum(len(item.body) for item in case.pages + case.observations)
    remaining = 16777216 - current
    pages = []
    for page in case.pages:
        addition = min(2097152 - len(page.body), remaining)
        pages.append(dataclasses.replace(page, body=page.body + b" " * addition))
        remaining -= addition
    assert remaining == 0
    case = dataclasses.replace(case, pages=tuple(pages))
    assert len(case.pages) == len(case.enrollment.legacy_keys) == 8
    assert len(case.observations) == 4096
    original = legacy._authenticate
    payloads = []
    trials = []

    class CountingKey:
        def __init__(self, key: Any) -> None:
            self.key = key

        def verify(self, signature: bytes, payload: bytes) -> None:
            trials.append(payload)
            self.key.verify(signature, payload)

    def counted(payload: Any, supplied: Any) -> bool:
        payloads.append(payload.canonical)
        return original(payload, tuple(CountingKey(key) for key in supplied))

    monkeypatch.setattr(legacy, "_authenticate", counted)
    result = _evaluate(case)
    _assert_result(result, "consistent", set())
    assert result.witness_summaries[0].committed_locators == 4095
    assert len(payloads) == len(set(payloads)) == 2
    assert 2 <= len(trials) <= 16
    # One extra byte yields only resource incompleteness, before any additional authentication.
    overflow = dataclasses.replace(
        case,
        pages=(dataclasses.replace(case.pages[0], body=case.pages[0].body + b" "), *case.pages[1:]),
    )
    before = len(trials)
    _assert_result(_evaluate(overflow), "incomplete", {"RESOURCE_LIMIT"}, failed=0, incomplete=1)
    assert len(trials) == before


@pytest.mark.parametrize("kind", ["observation", "page", "aggregate", "declared"])
def test_resource_abort_precedes_crypto_or_partial_success(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import legacy_checkpoint_compat as legacy

    case = _reference_case("consistent", bridge)
    limits = bridge.BridgeLimits(4096, 16777216, 32)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resource-only result must not authenticate legacy evidence")

    monkeypatch.setattr(legacy, "_decode", forbidden)
    if kind == "observation":
        case = dataclasses.replace(case, observations=(object(),) * 4097)
    elif kind == "page":
        case = dataclasses.replace(case, pages=(object(),) * 9)
    elif kind == "aggregate":
        limits = dataclasses.replace(limits, maximum_total_bytes=1)
    else:
        case = _reference_case("declared_large", bridge)
    if kind != "declared":
        monkeypatch.setattr(bridge.checkpoint_v2, "public_key_id", forbidden)
    result = bridge.evaluate_bootstrap_bridge(
        case.enrollment, case.root_body, case.pages, case.observations, limits=limits
    )
    _assert_result(result, "incomplete", {"RESOURCE_LIMIT"}, failed=0, incomplete=1)
    assert result.duplicate_body_observations == result.duplicate_page_observations == 0


def test_lowered_count_and_byte_limits_include_duplicate_transports() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    size = len(case.root_body) + sum(len(item.body) for item in case.pages + case.observations)
    limits = bridge.BridgeLimits(len(case.observations), size, 32)
    result = bridge.evaluate_bootstrap_bridge(
        case.enrollment, case.root_body, case.pages, case.observations, limits=limits
    )
    _assert_result(result, "consistent", set())
    for changed in (
        dataclasses.replace(case, observations=(*case.observations, case.observations[0])),
        dataclasses.replace(case, pages=(*case.pages, case.pages[0])),
    ):
        result = bridge.evaluate_bootstrap_bridge(
            changed.enrollment,
            changed.root_body,
            changed.pages,
            changed.observations,
            limits=limits,
        )
        _assert_result(result, "incomplete", {"RESOURCE_LIMIT"})


def test_late_failure_survives_display_cap_and_counts_every_group() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    first = case.observations[0]
    gaps = tuple(
        bridge.LegacyUnavailableObservation(
            first.witness_id, first.object_key, f"missing-{index:03d}"
        )
        for index in range(40)
    )
    marker = bridge.LegacyDeleteObservation(first.witness_id, first.object_key, "late-marker")
    case = dataclasses.replace(case, observations=case.observations + gaps + (marker,))
    for cap in (1, 32):
        result = _evaluate(case, maximum_issues=cap)
        assert result.status == "failed"
        assert (result.failed_issues, result.incomplete_issues, result.issues_omitted) == (
            1,
            40,
            41 - cap,
        )
        assert result.issues[0].code == "LEGACY_DELETE_MARKER"
        assert len(result.issues) == cap
        assert result.usable_bootstrap_pin is None
        reversed_case = dataclasses.replace(case, observations=case.observations[::-1])
        other = _evaluate(reversed_case, maximum_issues=cap)
        assert _semantic_issues(other, reversed_case) == _semantic_issues(result, case)


@pytest.mark.parametrize("fault", [MemoryError, OSError, KeyboardInterrupt])
def test_unexpected_failures_propagate_instead_of_becoming_evidence(
    fault: type[BaseException], monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import legacy_checkpoint_compat as legacy

    case = _reference_case("consistent", bridge)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise fault("synthetic fault")

    monkeypatch.setattr(legacy, "_authenticate", broken)
    with pytest.raises(fault):
        _evaluate(case)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("enrollment", {}),
        ("root_body", bytearray(b"{}")),
        ("pages", []),
        ("observations", []),
        ("page-member", b"{}"),
        ("body-member", object()),
        ("body-bytes", bytearray(b"{}")),
        ("witness", "not-uuid"),
        ("object-label", "bad\x00label"),
        ("object-label", "\ud800"),
        ("object-label", "é" * 513),
        ("version-label", None),
        ("version-label", ""),
        ("key-bytes", b"x"),
        ("key-id", "bad"),
        ("key-fingerprint", "ed25519-sha256:" + "0" * 64),
        ("boundary", None),
        ("head-id", True),
        ("head-id", 0),
        ("head-id", 2**63),
        ("epoch", -1),
        ("epoch", True),
        ("epoch", 2**63),
        ("witnesses", ()),
        ("keys", []),
        ("gap", "other"),
        ("required", {}),
        ("required-sequence", True),
        ("required-sequence", 2**63),
    ],
)
def test_exact_public_input_contract_has_fixed_safe_errors(field: str, bad: Any) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    case = _reference_case("consistent", bridge)
    if field in ("enrollment", "root_body", "pages", "observations"):
        case = dataclasses.replace(case, **{field: bad})
    elif field == "page-member":
        case = dataclasses.replace(case, pages=(bad,))
    elif field == "body-member":
        case = dataclasses.replace(case, observations=(bad,))
    elif field in ("body-bytes", "witness", "object-label", "version-label"):
        attr = {
            "body-bytes": "body",
            "witness": "witness_id",
            "object-label": "object_key",
            "version-label": "version_id",
        }[field]
        case = dataclasses.replace(
            case, observations=(dataclasses.replace(case.observations[0], **{attr: bad}),)
        )
    elif field.startswith("key-"):
        material = dataclasses.replace(
            case.enrollment.legacy_keys[0],
            **{"public_key" if field == "key-bytes" else "key_id": bad},
        )
        case = dataclasses.replace(
            case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=(material,))
        )
    elif field in ("witnesses", "keys"):
        case = dataclasses.replace(
            case,
            enrollment=dataclasses.replace(
                case.enrollment, **{"witnesses" if field == "witnesses" else "legacy_keys": bad}
            ),
        )
    elif field == "gap":
        case = dataclasses.replace(
            case,
            observations=(
                bridge.WitnessCollectionGap(case.enrollment.witnesses[0].witness_id, bad),
            ),
        )
    elif field.startswith("required"):
        required = bad if field == "required" else lineage.RequiredCheckpointPin("0" * 64, bad)
        case = dataclasses.replace(
            case,
            enrollment=dataclasses.replace(
                case.enrollment,
                stream=dataclasses.replace(case.enrollment.stream, required_checkpoint=required),
            ),
        )
    else:
        pin = case.enrollment.stream.bootstrap
        if field == "head-id":
            pin = dataclasses.replace(
                pin, audit_boundary=dataclasses.replace(pin.audit_boundary, latest_id=bad)
            )
        else:
            pin = dataclasses.replace(
                pin, **{"audit_boundary" if field == "boundary" else "initial_key_epoch": bad}
            )
        case = dataclasses.replace(
            case,
            enrollment=dataclasses.replace(
                case.enrollment, stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin)
            ),
        )
    with pytest.raises(bridge.BridgeInputError) as error:
        _evaluate(case)
    assert str(error.value) == "invalid bootstrap bridge input"
    assert error.value.__suppress_context__ is True
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "limits",
    [
        (0, 1, 1),
        (4097, 1, 1),
        (True, 1, 1),
        (1, 0, 1),
        (1, 16777217, 1),
        (1, True, 1),
        (1, 1, 0),
        (1, 1, 33),
        (1, 1, True),
    ],
)
def test_public_limits_only_lower_builtin_integer_ceilings(limits: tuple[Any, ...]) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    with pytest.raises(bridge.BridgeInputError, match=r"^invalid bootstrap bridge input$"):
        bridge.evaluate_bootstrap_bridge(
            case.enrollment,
            case.root_body,
            case.pages,
            case.observations,
            limits=bridge.BridgeLimits(*limits),
        )


def test_member_validation_precedes_aggregate_but_follows_count_limit() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    with pytest.raises(bridge.BridgeInputError):
        bridge.evaluate_bootstrap_bridge(
            case.enrollment,
            case.root_body,
            case.pages,
            (object(),),
            limits=bridge.BridgeLimits(1, 1, 1),
        )
    result = bridge.evaluate_bootstrap_bridge(
        case.enrollment,
        case.root_body,
        case.pages,
        (object(), object()),
        limits=bridge.BridgeLimits(1, 1, 1),
    )
    _assert_result(result, "incomplete", {"RESOURCE_LIMIT"})


def test_public_records_are_frozen_slots_and_inputs_are_unchanged() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    before = copy.deepcopy(case)
    result = _evaluate(case)
    assert case == before
    body = case.observations[0]
    records = (
        case.enrollment,
        case.enrollment.legacy_keys[0],
        case.enrollment.witnesses[0],
        case.pages[0],
        body,
        bridge.LegacyUnavailableObservation(body.witness_id, body.object_key, body.version_id),
        bridge.LegacyDeleteObservation(body.witness_id, body.object_key, body.version_id),
        bridge.WitnessCollectionGap(body.witness_id, "collection-limit"),
        bridge.BridgeLimits(1, 1, 1),
        result,
        result.witness_summaries[0],
        bridge.BridgeIssue("ROOT_MISSING", "incomplete", (), ()),
    )
    for record in records:
        assert dataclasses.is_dataclass(record)
        assert not hasattr(record, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, dataclasses.fields(record)[0].name, None)
    with pytest.raises(bridge.BridgeInputError):
        bridge.evaluate_bootstrap_bridge(
            case.enrollment,
            case.root_body,
            case.pages,
            (result,),
            limits=bridge.BridgeLimits(4096, 16777216, 32),
        )


def test_retained_legacy_key_admission_is_not_retroactively_v2_admission() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import checkpoint_v2

    case = _reference_case("consistent", bridge)
    raw = b"\x01" + b"\x00" * 31
    key = Ed25519PublicKey.from_public_bytes(raw)
    with pytest.raises(checkpoint_v2.CheckpointV2Error):
        checkpoint_v2.public_key_id(key)
    material = bridge.LegacyPublicMaterial("ed25519-sha256:" + hashlib.sha256(raw).hexdigest(), raw)
    changed = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(
            case.enrollment, legacy_keys=(*case.enrollment.legacy_keys, material)
        ),
    )
    root = json.loads(case.root_body)
    root["legacy_key_ids"].append(material.key_id)
    root["legacy_key_ids"].sort()
    _assert_result(_evaluate(_repin(changed, root=root)), "consistent", set())


def test_fresh_process_imports_are_pure() -> None:
    import subprocess
    import sys

    script = """
import sys
from easysynq_api.services.audit import (
    bootstrap_bridge, bootstrap_bridge_codec, legacy_checkpoint_compat,
)
for name in sys.modules:
    assert not name.startswith(('sqlalchemy', 'easysynq_api.settings'))
    assert name not in (
        'easysynq_api.core.config', 'easysynq_api.services.audit.checkpoint',
        'easysynq_api.services.audit.trust', 'easysynq_api.services.audit.sink',
        'easysynq_api.services.audit.external',
    )
print('PURE_BRIDGE_IMPORT_OK')
"""
    result = subprocess.run(  # noqa: S603 - current interpreter, literal script
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PURE_BRIDGE_IMPORT_OK"


def test_bridge_composes_with_full_r77_rotation_without_assurance_upgrade() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    case = _reference_case("consistent", bridge)
    result = _evaluate(case)
    _assert_result(result, "consistent", set())
    observations = tuple(
        lineage.EnvelopeObservation(
            "synthetic", f"v2-{index}", "version", bytes.fromhex(vector["body_hex"])
        )
        for index, vector in enumerate(_reference()["v2_composition"])
    )
    stream = dataclasses.replace(case.enrollment.stream, bootstrap=result.usable_bootstrap_pin)
    evaluated = lineage.evaluate_lineage(
        stream, observations, limits=lineage.LineageLimits(4096, 16777216, 32)
    )
    assert evaluated.status == "consistent"
    assert evaluated.tip_sequence == 3
    assert evaluated.scope == "supplied-v2-graph"
    assert evaluated.bootstrap_assurance == "external-pin-only"
    assert evaluated.unproved_checks == (
        "bootstrap-contents",
        "legacy-bridge-coverage",
        "witness-collection-completeness",
        "witness-custody",
        "audit-chain-comparison",
        "freshness",
        "operational-key-activation",
    )
    assert evaluated.required_checkpoint_relation == "not-provided"
    assert [item.key_epoch for item in evaluated.key_history] == [0, 1]
    for required, relation in (
        (
            lineage.RequiredCheckpointPin(_reference()["v2_composition"][-1]["anchor_hash"], 3),
            "included",
        ),
        (lineage.RequiredCheckpointPin("e" * 64, 4), "missing"),
    ):
        checked = lineage.evaluate_lineage(
            dataclasses.replace(stream, required_checkpoint=required),
            observations,
            limits=lineage.LineageLimits(4096, 16777216, 32),
        )
        assert checked.required_checkpoint_relation == relation
    incomplete_bridge = _evaluate(dataclasses.replace(case, pages=case.pages[:1]))
    assert incomplete_bridge.status == "incomplete"
    assert evaluated.status == "consistent"
    assert incomplete_bridge.usable_bootstrap_pin is None
    assert _evaluate(case).unproved_checks == _UNPROVED


def _evaluate_signed_first_edge(field: str, value: str) -> lineage.LineageEvaluation:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    case = _reference_case("consistent", bridge)
    # Independent fresh test key; the root and external initial material are rebound together.
    key = Ed25519PrivateKey.from_private_bytes(b"\x71" * 32)
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = "ed25519-sha256:" + hashlib.sha256(public).hexdigest()
    pin = dataclasses.replace(
        case.enrollment.stream.bootstrap, initial_key_id=key_id, initial_public_key=public
    )
    case = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(
            case.enrollment, stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin)
        ),
    )
    root = json.loads(case.root_body)
    root.update(initial_key_id=key_id, initial_public_key=base64.b64encode(public).decode())
    case = _repin(case, root=root)
    _assert_result(_evaluate(case), "consistent", set())
    checkpoint = json.loads(bytes.fromhex(_reference()["v2_composition"][0]["body_hex"]))[
        "checkpoint"
    ]
    checkpoint.update(
        key_id=key_id, previous_anchor_hash=case.enrollment.stream.bootstrap.commitment_hash
    )
    checkpoint[field] = value
    canonical = rfc8785.dumps(checkpoint)
    signature = key.sign(_SIGNATURE_DOMAIN + canonical)
    key.public_key().verify(signature, _SIGNATURE_DOMAIN + canonical)
    raw = rfc8785.dumps(
        {
            "checkpoint": checkpoint,
            "signature": base64.b64encode(signature).decode(),
            "anchor_hash": hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest(),
        }
    )
    return lineage.evaluate_lineage(
        case.enrollment.stream,
        (lineage.EnvelopeObservation("synthetic", "edge", "version", raw),),
        limits=lineage.LineageLimits(4096, 16777216, 32),
    )


@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        ("previous_anchor_hash", "e" * 64, None),
        ("key_epoch", "1", None),
        ("sequence", "2", None),
        ("latest_id", "41", "AUDIT_HEAD_REGRESSION"),
        ("latest_row_hash", "e" * 64, "AUDIT_HEAD_CONFLICT"),
        ("key_id", _reference()["materials"]["v2_next"]["key_id"], None),
    ],
    ids=[
        "previous_anchor_hash",
        "key_epoch",
        "sequence",
        "lower_head",
        "conflicting_head",
        "key_id",
    ],
)
def test_r77_rejects_signed_first_edge_tampering_after_valid_bridge(
    field: str,
    value: str,
    expected_code: str | None,
) -> None:
    result = _evaluate_signed_first_edge(field, value)
    assert result.status != "consistent"
    assert result.ordered_envelopes == ()
    if expected_code is not None:
        assert result.status == "failed"
        assert {issue.code for issue in result.issues} == {expected_code}
        assert (result.failed_issues, result.incomplete_issues) == (1, 0)


def test_r77_accepts_signed_advancing_first_head_without_assurance_upgrade() -> None:
    result = _evaluate_signed_first_edge("latest_id", "43")
    assert result.status == "consistent"
    assert result.issues == ()
    assert (result.failed_issues, result.incomplete_issues) == (0, 0)
    assert result.tip_sequence == 1
    assert len(result.ordered_envelopes) == 1
    assert result.ordered_envelopes[0].latest_id == 43
    assert result.scope == "supplied-v2-graph"
    assert result.bootstrap_assurance == "external-pin-only"
    assert result.unproved_checks == (
        "bootstrap-contents",
        "legacy-bridge-coverage",
        "witness-collection-completeness",
        "witness-custody",
        "audit-chain-comparison",
        "freshness",
        "operational-key-activation",
    )


def test_legacy_timestamp_order_never_invents_freshness_or_predecessor_rules() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    key = Ed25519PrivateKey.from_private_bytes(b"\x61" * 32)
    raw_key = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    material = bridge.LegacyPublicMaterial(
        "ed25519-sha256:" + hashlib.sha256(raw_key).hexdigest(), raw_key
    )
    observations = []
    for item in case.observations:
        payload = json.loads(item.body)["checkpoint"]
        # Reversing timestamps across signed IDs cannot create a nonexistent legacy sequence.
        payload["timestamp"] = (
            "9999-01-01T00:00:00+00:00"
            if payload["latest_id"] == 10
            else "0001-01-01T00:00:00+00:00"
        )
        canonical = rfc8785.dumps(payload)
        signature = key.sign(canonical)
        key.public_key().verify(signature, canonical)
        raw = rfc8785.dumps(
            {"checkpoint": payload, "signature": base64.b64encode(signature).decode()}
        )
        observations.append(dataclasses.replace(item, body=raw))
    documents = [json.loads(page.body) for page in case.pages]
    for entry, observation in zip(
        [entry for page in documents for entry in page["entries"]], observations, strict=True
    ):
        entry.update(
            body_hash=hashlib.sha256(_BODY_DOMAIN + observation.body).hexdigest(),
            body_bytes=str(len(observation.body)),
        )
    root = json.loads(case.root_body)
    root["legacy_key_ids"] = [material.key_id]
    case = _repin(
        dataclasses.replace(
            case,
            enrollment=dataclasses.replace(case.enrollment, legacy_keys=(material,)),
            observations=tuple(observations),
        ),
        root=root,
        page_documents=documents,
    )
    _assert_result(_evaluate(case), "consistent", set())


def test_lagging_witness_cannot_borrow_boundary_from_sibling() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    raw = case.observations[0].body
    documents = [json.loads(page.body) for page in case.pages]
    documents[-1]["entries"][-1].update(
        body_hash=hashlib.sha256(_BODY_DOMAIN + raw).hexdigest(), body_bytes=str(len(raw))
    )
    observations = (*case.observations[:-1], dataclasses.replace(case.observations[-1], body=raw))
    changed = _repin(dataclasses.replace(case, observations=observations), page_documents=documents)
    result = _evaluate(changed)
    _assert_result(result, "failed", {"WITNESS_SUMMARY_MISMATCH"}, failed=1, incomplete=0)
    assert result.issues[0].observation_indexes == (513,)


def test_present_invalid_body_is_not_also_missing() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    changed = dataclasses.replace(
        case,
        observations=(
            dataclasses.replace(case.observations[0], body=b"{}"),
            *case.observations[1:],
        ),
    )
    _assert_result(
        _evaluate(changed),
        "failed",
        {"LEGACY_BODY_INVALID", "LEGACY_BODY_COMMITMENT_MISMATCH"},
        failed=2,
        incomplete=0,
    )


def test_unlisted_boundary_at_new_version_is_a_discrepancy() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    extra = dataclasses.replace(case.observations[-1], version_id="extra-boundary")
    _assert_result(
        _evaluate(dataclasses.replace(case, observations=(*case.observations, extra))),
        "failed",
        {"UNLISTED_AUTHENTIC_LEGACY"},
        failed=1,
        incomplete=0,
    )


def test_declared_resource_count_has_no_authority_before_root_bindings() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("declared_large", bridge)
    pin = dataclasses.replace(case.enrollment.stream.bootstrap, commitment_hash="0" * 64)
    changed = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(
            case.enrollment, stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin)
        ),
    )
    _assert_result(
        _evaluate(changed), "failed", {"ROOT_COMMITMENT_MISMATCH"}, failed=1, incomplete=0
    )


@pytest.mark.parametrize("which", ["keys", "witnesses"])
@pytest.mark.parametrize("mutation", ["duplicate", "over-count", "wrong-record"])
def test_external_inventory_has_exact_record_and_cardinality_bounds(
    which: str, mutation: str
) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    field = "legacy_keys" if which == "keys" else "witnesses"
    original = getattr(case.enrollment, field)
    if mutation == "wrong-record":
        changed = (object(),)
    else:
        changed = (original[0],) * (2 if mutation == "duplicate" else 9 if which == "keys" else 5)
    case = dataclasses.replace(
        case, enrollment=dataclasses.replace(case.enrollment, **{field: changed})
    )
    with pytest.raises(bridge.BridgeInputError):
        _evaluate(case)


def test_zero_keys_mean_missing_material_not_empty_history() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    changed = dataclasses.replace(
        case, enrollment=dataclasses.replace(case.enrollment, legacy_keys=())
    )
    _assert_result(
        _evaluate(changed),
        "incomplete",
        {"LEGACY_KEY_MISSING", "LEGACY_AUTHENTICATION_UNESTABLISHED"},
        failed=0,
        incomplete=4,
    )


def test_exact_empty_history_is_not_an_enrollable_package() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    for field, value in (
        ("entry_count", "0"),
        ("witnesses", []),
        ("pages", []),
        ("legacy_key_ids", []),
    ):
        root = json.loads(case.root_body)
        root[field] = value
        changed = _repin(dataclasses.replace(case, observations=(), pages=()), root=root)
        _assert_result(_evaluate(changed), "failed", {"ROOT_INVALID"})


def test_raw_body_commitment_is_sensitive_to_domain_and_single_byte() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    original = case.observations[0]
    # Whitespace preserves the old signature but changes exact body custody.
    changed = dataclasses.replace(
        case,
        observations=(
            dataclasses.replace(original, body=original.body + b" "),
            *case.observations[1:],
        ),
    )
    _assert_result(_evaluate(changed), "failed", {"LEGACY_BODY_COMMITMENT_MISMATCH"})
    docs = [json.loads(page.body) for page in case.pages]
    docs[0]["entries"][0]["body_hash"] = hashlib.sha256(original.body).hexdigest()
    _assert_result(
        _evaluate(_repin(case, page_documents=docs)), "failed", {"LEGACY_BODY_COMMITMENT_MISMATCH"}
    )


def test_retained_raw_reader_and_bridge_agree_without_reserializing_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    from easysynq_api.services.audit import checkpoint, sink, trust

    case = _committed_body_variant("old_offset")
    observation = case.observations[0]
    stream = io.BytesIO(observation.body)

    class Client:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "Body": stream,
                "ContentLength": len(observation.body),
                "VersionId": observation.version_id,
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(sink, "_audit_history_read_client", lambda _connection: Client())
    document = sink.read_offhost_checkpoint_version(
        "worm_bucket",
        {"bucket": "synthetic"},
        sink.CheckpointVersionRef(observation.object_key, observation.version_id),
    )
    verifier = trust.legacy_verifier(
        tuple(
            trust.TrustedLegacyKey(item.key_id, Ed25519PublicKey.from_public_bytes(item.public_key))
            for item in case.enrollment.legacy_keys
        )
    )
    verified, reason = checkpoint._authenticate_offhost_doc_with_verifier(
        case.enrollment.stream.org_id, verifier, document
    )
    assert reason is None and verified is not None
    assert verified.latest_id == 10
    assert stream.closed
    _assert_result(_evaluate(case), "consistent", set())
    # Reserializing a retained reader's dict cannot satisfy the original raw commitment.
    rewritten = dataclasses.replace(observation, body=json.dumps(document, indent=2).encode())
    changed = dataclasses.replace(case, observations=(rewritten, *case.observations[1:]))
    _assert_result(_evaluate(changed), "failed", {"LEGACY_BODY_COMMITMENT_MISMATCH"})


def test_signed_head_conflicts_across_committed_pages_are_not_hidden_by_summaries() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    vector = _reference()["legacy_vectors"]["old_head_conflict"]
    raw = bytes.fromhex(vector["body_hex"])
    documents = [json.loads(page.body) for page in case.pages]
    documents[-1]["entries"][-1].update(body_hash=vector["body_hash"], body_bytes=str(len(raw)))
    observations = (*case.observations[:-1], dataclasses.replace(case.observations[-1], body=raw))
    changed = _repin(dataclasses.replace(case, observations=observations), page_documents=documents)
    result = _evaluate(changed)
    _assert_result(result, "failed", {"SIGNED_HEAD_CONFLICT"}, failed=1, incomplete=0)
    issue = result.issues[0]
    assert set(issue.observation_indexes) == {0, 513}


@pytest.mark.parametrize("value", ["01", "+1", "1.0", "9223372036854775808"])
def test_new_wire_decimal_spellings_are_not_legacy_numeric_coercions(value: str) -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    root["initial_key_epoch"] = value
    _assert_result(_evaluate(_repin(case, root=root)), "failed", {"ROOT_INVALID"})


def test_positive_external_bigint_endpoint_is_structurally_valid_without_claiming_history() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import lineage

    case = _reference_case("consistent", bridge)
    pin = dataclasses.replace(
        case.enrollment.stream.bootstrap,
        initial_key_epoch=2**63 - 1,
        audit_boundary=lineage.AuditHead(2**63 - 1, "e" * 64),
    )
    stream = dataclasses.replace(
        case.enrollment.stream,
        bootstrap=pin,
        required_checkpoint=lineage.RequiredCheckpointPin("a" * 64, 2**63 - 1),
    )
    changed = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(case.enrollment, stream=stream),
        root_body=None,
        pages=(),
        observations=(),
    )
    _assert_result(_evaluate(changed), "incomplete", {"ROOT_MISSING"})


def test_new_wire_root_page_and_legacy_body_exact_transport_ceilings() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    # Raw JSON whitespace is accepted up to each independent transport ceiling.
    padded_root = case.root_body + b" " * (262144 - len(case.root_body))
    padded_page = case.pages[0].body + b" " * (2097152 - len(case.pages[0].body))
    _assert_result(
        _evaluate(
            dataclasses.replace(
                case,
                root_body=padded_root,
                pages=(bridge.BridgePageObservation(padded_page), case.pages[1]),
            )
        ),
        "consistent",
        set(),
    )
    raw = case.observations[0].body
    raw += b" " * (65536 - len(raw))
    documents = [json.loads(page.body) for page in case.pages]
    documents[0]["entries"][0].update(
        body_hash=hashlib.sha256(_BODY_DOMAIN + raw).hexdigest(), body_bytes="65536"
    )
    changed = _repin(
        dataclasses.replace(
            case,
            observations=(
                dataclasses.replace(case.observations[0], body=raw),
                *case.observations[1:],
            ),
        ),
        page_documents=documents,
    )
    _assert_result(_evaluate(changed), "consistent", set())


def test_all_duplicate_manifest_groups_are_counted_before_later_order_faults() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    documents = [json.loads(page.body) for page in case.pages]
    documents[1]["entries"] = [
        copy.deepcopy(documents[0]["entries"][0]),
        copy.deepcopy(documents[0]["entries"][1]),
    ]
    changed = _repin(case, page_documents=documents)
    result = _evaluate(changed)
    _assert_result(result, "failed", {"MANIFEST_LOCATOR_DUPLICATE"}, failed=2, incomplete=0)
    assert all(item.page_indexes == (0, 1) for item in result.issues)


def test_four_required_witnesses_each_need_boundary_evidence() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    documents = [json.loads(page.body) for page in case.pages]
    observations = list(case.observations)
    witnesses = list(case.enrollment.witnesses)
    for number in (35, 36):
        witness_id = UUID(int=number)
        # UUID(int=...) sorts before the fixture UUIDs; sort both input commitments globally.
        namespace = hashlib.sha256(
            _NAMESPACE_DOMAIN
            + rfc8785.dumps(
                {
                    "kind": "worm_bucket",
                    "endpoint": f"https://synthetic-{number}.invalid",
                    "bucket": "synthetic",
                    "region": "",
                    "prefix": f"checkpoints/{case.enrollment.stream.org_id}/",
                }
            )
        ).hexdigest()
        witnesses.append(bridge.BridgeWitnessPin(witness_id, namespace))
        root["witnesses"].append(
            dict(root["witnesses"][-1], witness_id=str(witness_id), namespace_hash=namespace)
        )
        observations.append(dataclasses.replace(case.observations[-1], witness_id=witness_id))
        documents[-1]["entries"].append(
            dict(documents[-1]["entries"][-1], witness_id=str(witness_id))
        )
    root["witnesses"].sort(key=lambda item: item["witness_id"])
    root["entry_count"] = "516"
    entries = sorted(
        [entry for page in documents for entry in page["entries"]],
        key=lambda item: (item["witness_id"], item["object_key"], item["version_id"]),
    )
    for index, page in enumerate(documents):
        page["entries"] = entries[index * 512 : (index + 1) * 512]
    case = _repin(
        dataclasses.replace(
            case,
            enrollment=dataclasses.replace(case.enrollment, witnesses=tuple(witnesses)),
            observations=tuple(observations),
        ),
        root=root,
        page_documents=documents,
    )
    result = _evaluate(case)
    _assert_result(result, "consistent", set())
    assert len(result.witness_summaries) == 4
    for witness in witnesses:
        incomplete = dataclasses.replace(
            case,
            observations=tuple(
                item for item in case.observations if item.witness_id != witness.witness_id
            ),
        )
        result = _evaluate(incomplete)
        assert result.status == "incomplete" and result.usable_bootstrap_pin is None
        assert all(issue.code == "LEGACY_BODY_MISSING" for issue in result.issues)


def test_initial_v2_material_still_requires_unchanged_public_admission() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    raw = b"\x01" + b"\x00" * 31
    pin = dataclasses.replace(
        case.enrollment.stream.bootstrap,
        initial_public_key=raw,
        initial_key_id="ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    changed = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(
            case.enrollment, stream=dataclasses.replace(case.enrollment.stream, bootstrap=pin)
        ),
    )
    with pytest.raises(bridge.BridgeInputError) as error:
        _evaluate(changed)
    assert str(error.value) == "invalid bootstrap bridge input"
    assert error.value.__suppress_context__ is True


def test_deleted_or_unavailable_literal_null_version_cannot_be_normalized_away() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    original = next(item for item in case.observations if item.version_id == "null")
    marker = bridge.LegacyDeleteObservation(original.witness_id, original.object_key, "null")
    unavailable = bridge.LegacyUnavailableObservation(
        original.witness_id, original.object_key, "null"
    )
    result = _evaluate(
        dataclasses.replace(case, observations=(*case.observations, marker, unavailable))
    )
    _assert_result(
        result,
        "failed",
        {"LEGACY_DELETE_MARKER", "LEGACY_BODY_UNAVAILABLE"},
        failed=1,
        incomplete=1,
    )


def test_single_required_witness_still_needs_its_complete_nonempty_package() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    root["entry_count"] = "513"
    root["witnesses"] = root["witnesses"][:1]
    documents = [json.loads(page.body) for page in case.pages]
    documents[-1]["entries"] = documents[-1]["entries"][:1]
    changed = dataclasses.replace(
        case,
        enrollment=dataclasses.replace(case.enrollment, witnesses=case.enrollment.witnesses[:1]),
        observations=case.observations[:-1],
    )
    _assert_result(
        _evaluate(_repin(changed, root=root, page_documents=documents)), "consistent", set()
    )


def test_skipped_page_index_is_not_a_new_root_or_partial_window() -> None:
    from easysynq_api.services.audit import bootstrap_bridge as bridge

    case = _reference_case("consistent", bridge)
    root = json.loads(case.root_body)
    root["pages"][1]["page_index"] = "2"
    _assert_result(_evaluate(_repin(case, root=root)), "failed", {"ROOT_INVALID"})
    documents = [json.loads(page.body) for page in case.pages]
    documents[1]["page_index"] = "2"
    changed = _repin(case, page_documents=documents)
    # The root reference itself must be contiguous even when a page is well-shaped.
    _assert_result(_evaluate(changed), "failed", {"ROOT_INVALID"})
