from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import multiprocessing
import tempfile
import threading
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
import rfc8785

from easysynq_api.services.audit import version_page_transport
from easysynq_api.services.audit.bootstrap_bridge import BridgeWitnessPin
from easysynq_api.services.audit.sink import ExplicitHistoryReader

pytestmark = pytest.mark.unit

ORG_ID = UUID("00000000-0000-4000-8000-000000000011")
WITNESS_ID = UUID("00000000-0000-4000-8000-000000000021")
OTHER_WITNESS_ID = UUID("00000000-0000-4000-8000-000000000022")
_MODULE_NAME = "easysynq_api.services.audit.history_collection"
_NAMESPACE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/namespace\0"
_FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "audit_history_collection_vectors.json").read_text()
)
_NAMESPACE = _FIXTURE["namespaces"][0]
_OTHER_NAMESPACE = _FIXTURE["namespaces"][1]
_LEGACY_NAMESPACE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "audit_bootstrap_bridge_vectors.json").read_text()
)["namespaces"][0]


class _String(str):
    pass


class _UUID(UUID):
    pass


class _Tuple(tuple):
    pass


class _Event(threading.Event):
    pass


def _collection_module() -> ModuleType:
    try:
        return importlib.import_module(_MODULE_NAME)
    except ModuleNotFoundError as error:
        if error.name != _MODULE_NAME:
            raise
        pytest.fail("audit history collection module is not implemented", pytrace=False)


def _reader(vector: dict[str, Any] = _NAMESPACE) -> ExplicitHistoryReader:
    namespace = vector["namespace"]
    return ExplicitHistoryReader(
        namespace["endpoint"],
        namespace["bucket"],
        namespace["region"],
        "fixture-access",
        "fixture-secret",
    )


def _limits(collection: ModuleType, **changes: Any) -> Any:
    values = {
        "maximum_pages": 8,
        "maximum_observations": 5_000,
        "maximum_total_bytes": 16_777_216,
        "maximum_spool_bytes": 33_554_432,
        "maximum_wall_seconds": 60,
        "maximum_issues": 8,
    }
    values.update(changes)
    return collection.HistoryCollectionLimits(**values)


def _required(collection: ModuleType, vector: dict[str, Any] = _NAMESPACE) -> Any:
    return collection.RequiredHistoryWitness(UUID(vector["witness_id"]), _reader(vector))


def _pin(vector: dict[str, Any] = _NAMESPACE) -> BridgeWitnessPin:
    return BridgeWitnessPin(UUID(vector["witness_id"]), vector["namespace_hash"])


def _admit_one(collection: ModuleType, *, limits: Any | None = None) -> tuple[Any, ...]:
    return collection._validate_collection_inputs(
        ORG_ID,
        (_pin(),),
        (_required(collection),),
        _limits(collection) if limits is None else limits,
        None,
    )


def test_namespace_binding_matches_independent_literal_vector() -> None:
    for vector in _FIXTURE["namespaces"]:
        canonical = bytes.fromhex(vector["canonical_namespace_hex"])
        assert rfc8785.dumps(vector["namespace"]) == canonical
        assert hashlib.sha256(_NAMESPACE_DOMAIN + canonical).hexdigest() == vector["namespace_hash"]

    collection = _collection_module()
    assert collection._namespace_hash(ORG_ID, _reader()) == _NAMESPACE["namespace_hash"]


def test_entire_scope_rejects_changed_required_witness_namespace() -> None:
    collection = _collection_module()
    admitted_reader = collection.RequiredHistoryWitness(WITNESS_ID, _reader())

    with pytest.raises(collection.HistoryCollectionInputError) as raised:
        collection._validate_collection_inputs(
            ORG_ID,
            (BridgeWitnessPin(WITNESS_ID, "0" * 64),),
            (admitted_reader,),
            _limits(collection),
            None,
        )
    assert str(raised.value) == "invalid checkpoint history collection input"


def test_admission_returns_readers_sorted_by_uuid_bytes() -> None:
    collection = _collection_module()
    first = _required(collection)
    second = _required(collection, _OTHER_NAMESPACE)

    admitted = collection._validate_collection_inputs(
        ORG_ID,
        (_pin(_OTHER_NAMESPACE), _pin()),
        (second, first),
        _limits(collection),
        None,
    )

    assert admitted == (first, second)
    assert [item.witness_id.bytes for item in admitted] == sorted(
        item.witness_id.bytes for item in admitted
    )


def test_legacy_empty_region_hash_remains_stable_but_is_not_admissible() -> None:
    collection = _collection_module()
    reader = _reader(_LEGACY_NAMESPACE)
    required = collection.RequiredHistoryWitness(WITNESS_ID, reader)
    pin = BridgeWitnessPin(WITNESS_ID, _LEGACY_NAMESPACE["namespace_hash"])

    assert collection._namespace_hash(ORG_ID, reader) == _LEGACY_NAMESPACE["namespace_hash"]
    with pytest.raises(collection.HistoryCollectionInputError):
        collection._validate_collection_inputs(
            ORG_ID, (pin,), (required,), _limits(collection), None
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximum_pages", 1),
        ("maximum_pages", 4_096),
        ("maximum_observations", 1),
        ("maximum_observations", 100_000),
        ("maximum_total_bytes", 1),
        ("maximum_total_bytes", 1_073_741_824),
        ("maximum_spool_bytes", 65_536),
        ("maximum_spool_bytes", 1_073_741_824),
        ("maximum_wall_seconds", 1),
        ("maximum_wall_seconds", 86_400),
        ("maximum_issues", 1),
        ("maximum_issues", 32),
    ],
)
def test_each_limit_accepts_inclusive_boundaries(field: str, value: int) -> None:
    collection = _collection_module()

    assert _admit_one(collection, limits=_limits(collection, **{field: value})) == (
        _required(collection),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximum_pages", 0),
        ("maximum_pages", 4_097),
        ("maximum_pages", True),
        ("maximum_observations", 0),
        ("maximum_observations", 100_001),
        ("maximum_observations", False),
        ("maximum_total_bytes", 0),
        ("maximum_total_bytes", 1_073_741_825),
        ("maximum_total_bytes", True),
        ("maximum_spool_bytes", 65_535),
        ("maximum_spool_bytes", 1_073_741_825),
        ("maximum_spool_bytes", 65_537),
        ("maximum_spool_bytes", False),
        ("maximum_wall_seconds", 0),
        ("maximum_wall_seconds", 86_401),
        ("maximum_wall_seconds", True),
        ("maximum_issues", 0),
        ("maximum_issues", 33),
        ("maximum_issues", False),
    ],
)
def test_each_limit_rejects_bool_and_out_of_range_values(field: str, value: Any) -> None:
    collection = _collection_module()

    with pytest.raises(collection.HistoryCollectionInputError):
        _admit_one(collection, limits=_limits(collection, **{field: value}))


@pytest.mark.parametrize(
    "argument,value",
    [
        ("org_id", "00000000-0000-4000-8000-000000000011"),
        ("org_id", _UUID(str(ORG_ID))),
        ("required_witnesses", [_pin()]),
        ("required_witnesses", _Tuple((_pin(),))),
        ("readers", []),
        ("readers", _Tuple(())),
        ("cancel", _Event()),
        ("cancel", object()),
    ],
)
def test_rejects_non_exact_outer_argument_types(argument: str, value: Any) -> None:
    collection = _collection_module()
    arguments = {
        "org_id": ORG_ID,
        "required_witnesses": (_pin(),),
        "readers": (_required(collection),),
        "limits": _limits(collection),
        "cancel": None,
        argument: value,
    }

    with pytest.raises(collection.HistoryCollectionInputError):
        collection._validate_collection_inputs(**arguments)


def test_rejects_dataclass_subclasses_and_uuid_string_substitutions() -> None:
    collection = _collection_module()

    @dataclasses.dataclass(frozen=True, slots=True)
    class PinSubclass(BridgeWitnessPin):
        pass

    @dataclasses.dataclass(frozen=True, slots=True)
    class RequiredSubclass(collection.RequiredHistoryWitness):
        pass

    @dataclasses.dataclass(frozen=True, slots=True)
    class LimitsSubclass(collection.HistoryCollectionLimits):
        pass

    @dataclasses.dataclass(frozen=True, slots=True)
    class ReaderSubclass(ExplicitHistoryReader):
        pass

    invalid_cases = [
        (
            (PinSubclass(WITNESS_ID, _NAMESPACE["namespace_hash"]),),
            (_required(collection),),
            _limits(collection),
        ),
        (
            (_pin(),),
            (RequiredSubclass(WITNESS_ID, _reader()),),
            _limits(collection),
        ),
        (
            (_pin(),),
            (_required(collection),),
            LimitsSubclass(8, 5_000, 16_777_216, 33_554_432, 60, 8),
        ),
        (
            (BridgeWitnessPin(str(WITNESS_ID), _NAMESPACE["namespace_hash"]),),
            (_required(collection),),
            _limits(collection),
        ),
        (
            (_pin(),),
            (collection.RequiredHistoryWitness(str(WITNESS_ID), _reader()),),
            _limits(collection),
        ),
        (
            (_pin(),),
            (
                collection.RequiredHistoryWitness(
                    WITNESS_ID,
                    ReaderSubclass(
                        "https://witness-1.invalid",
                        "synthetic-witness-1",
                        "us-east-1",
                        "fixture-access",
                        "fixture-secret",
                    ),
                ),
            ),
            _limits(collection),
        ),
    ]
    for pins, readers, limits in invalid_cases:
        with pytest.raises(collection.HistoryCollectionInputError):
            collection._validate_collection_inputs(ORG_ID, pins, readers, limits, None)


@pytest.mark.parametrize(
    "digest",
    [
        "0" * 63,
        "0" * 65,
        "A" * 64,
        "g" * 64,
        _String("0" * 64),
        b"0" * 64,
    ],
)
def test_rejects_noncanonical_namespace_digest(digest: Any) -> None:
    collection = _collection_module()

    with pytest.raises(collection.HistoryCollectionInputError):
        collection._validate_collection_inputs(
            ORG_ID,
            (BridgeWitnessPin(WITNESS_ID, digest),),
            (_required(collection),),
            _limits(collection),
            None,
        )


def test_rejects_empty_too_many_duplicate_missing_and_extra_witnesses() -> None:
    collection = _collection_module()
    five_pins = tuple(BridgeWitnessPin(UUID(int=index), "0" * 64) for index in range(1, 6))
    first = _required(collection)
    second = _required(collection, _OTHER_NAMESPACE)
    cases = [
        ((), ()),
        (five_pins, ()),
        ((_pin(), _pin()), (first,)),
        ((_pin(),), (first, first)),
        ((_pin(), _pin(_OTHER_NAMESPACE)), (first,)),
        ((_pin(),), (first, second)),
    ]

    for pins, readers in cases:
        with pytest.raises(collection.HistoryCollectionInputError):
            collection._validate_collection_inputs(ORG_ID, pins, readers, _limits(collection), None)


def test_oversized_scope_rejects_before_member_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _collection_module()
    valid_pin = _pin()
    valid_reader = _required(collection)
    oversized_pins = (valid_pin,) * 5
    oversized_readers = (valid_reader,) * 5

    class ForbiddenMember:
        def __get__(self, _instance: object, _owner: type[object]) -> Any:
            pytest.fail("oversized scope member was inspected")

    monkeypatch.setattr(BridgeWitnessPin, "witness_id", ForbiddenMember())
    monkeypatch.setattr(collection.RequiredHistoryWitness, "witness_id", ForbiddenMember())

    for pins, readers in (
        (oversized_pins, (valid_reader,)),
        ((valid_pin,), oversized_readers),
    ):
        with pytest.raises(collection.HistoryCollectionInputError):
            collection._validate_collection_inputs(ORG_ID, pins, readers, _limits(collection), None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "https://WITNESS-1.invalid"),
        ("endpoint", "https://witness-1.invalid/"),
        ("endpoint", "http://witness-1.invalid"),
        ("endpoint", "https://user:fixture-secret@witness-1.invalid"),
        ("bucket", "Bad_Bucket"),
        ("bucket", "127.0.0.1"),
        ("bucket", _String("synthetic-witness-1")),
        ("region", ""),
        ("region", "invalid region"),
        ("region", _String("us-east-1")),
        ("access_key", ""),
        ("access_key", " fixture-access"),
        ("access_key", _String("fixture-access")),
        ("secret_key", ""),
        ("secret_key", False),
        ("secret_key", _String("fixture-secret")),
    ],
)
def test_rejects_invalid_reader_scope_and_credentials(field: str, value: Any) -> None:
    collection = _collection_module()
    reader = dataclasses.replace(_reader(), **{field: value})
    required = collection.RequiredHistoryWitness(WITNESS_ID, reader)

    with pytest.raises(collection.HistoryCollectionInputError) as raised:
        collection._validate_collection_inputs(
            ORG_ID, (_pin(),), (required,), _limits(collection), None
        )
    assert "fixture-secret" not in str(raised.value)
    assert "fixture-secret" not in repr(raised.value)


def test_entire_two_witness_scope_is_rejected_before_any_runtime_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _collection_module()

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("invalid complete scope reached a runtime seam")

    monkeypatch.setattr(version_page_transport, "_create_client", forbidden)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(multiprocessing, "Process", forbidden)
    monkeypatch.setattr(threading, "Thread", forbidden)
    first = _required(collection)
    second = dataclasses.replace(
        _required(collection, _OTHER_NAMESPACE),
        reader=dataclasses.replace(_reader(_OTHER_NAMESPACE), region=""),
    )

    assert collection._namespace_hash(ORG_ID, first.reader) == _NAMESPACE["namespace_hash"]
    with pytest.raises(collection.HistoryCollectionInputError):
        collection._validate_collection_inputs(
            ORG_ID,
            (_pin(), _pin(_OTHER_NAMESPACE)),
            (first, second),
            _limits(collection),
            None,
        )


def test_public_result_types_are_frozen_slotted_and_have_no_count_defaults() -> None:
    collection = _collection_module()
    summary = collection.HistoryWitnessSummary(
        WITNESS_ID,
        _NAMESPACE["namespace_hash"],
        True,
        1,
        1,
        1,
        0,
        1,
        0,
        0,
        0,
    )
    issue = collection.HistoryCollectionIssue("LIST_UNAVAILABLE", "incomplete", WITNESS_ID, 1, ())
    report = collection.HistoryCollectionReport(
        "incomplete",
        "required-witness-provider-traversal",
        (summary,),
        (issue,),
        0,
        1,
        0,
        10,
        ("provider-non-omission",),
    )

    public_values = (_required(collection), _limits(collection), summary, issue, report)
    for value in public_values:
        assert dataclasses.is_dataclass(value)
        assert not hasattr(value, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            value.__setattr__(dataclasses.fields(value)[0].name, None)
        for field in dataclasses.fields(value):
            assert field.default is dataclasses.MISSING
            assert field.default_factory is dataclasses.MISSING
    assert "fixture-access" not in repr(public_values[0])
    assert "fixture-secret" not in repr(public_values[0])
    assert tuple(field.name for field in dataclasses.fields(summary)) == (
        "witness_id",
        "namespace_hash",
        "terminal_reached",
        "page_attempts",
        "admitted_pages",
        "version_observations",
        "delete_observations",
        "successful_reads",
        "unavailable_reads",
        "duplicate_body_deliveries",
        "conflicting_locators",
    )
    assert tuple(field.name for field in dataclasses.fields(issue)) == (
        "code",
        "severity",
        "witness_id",
        "count",
        "observation_ordinals",
    )
    assert tuple(field.name for field in dataclasses.fields(report)) == (
        "status",
        "scope",
        "witnesses",
        "issues",
        "failed_issues",
        "incomplete_issues",
        "issues_omitted",
        "admitted_total_bytes",
        "unproved_checks",
    )


def test_collection_errors_have_fixed_codes_messages_and_no_secret_values() -> None:
    collection = _collection_module()
    codes = (
        "RESOURCE_LIMIT",
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "STORAGE_FAILED",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    )

    for code in codes:
        error = collection.HistoryCollectionError(code)
        assert error.code == code
        assert str(error) == "checkpoint history collection failed"
    with pytest.raises(ValueError) as raised:
        collection.HistoryCollectionError("fixture-secret")
    assert str(raised.value) == "invalid checkpoint history collection error code"
    assert "fixture-secret" not in repr(raised.value)
    assert str(collection.HistoryCollectionCancelled()) == "checkpoint history collection cancelled"
