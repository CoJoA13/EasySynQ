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


# These transport-boundary fixtures are authored independently of the collector,
# R81 decoder and spool. The actual spool worker remains in every traversal test.
_PREFIX = "checkpoints/00000000-0000-4000-8000-000000000011/"
_WIRE_PREFIX = "checkpoints%2F00000000-0000-4000-8000-000000000011%2F"
_EXACT_KEY = _PREFIX + "unclassified +%2F"
_ENTRY_XML = (
    b"<Version><Key>checkpoints%2F00000000-0000-4000-8000-000000000011%2F"
    b"unclassified%20%2B%252F</Key><VersionId>null</VersionId>"
    b"<IsLatest>false</IsLatest></Version>"
)
_OPAQUE_BODY = b"\x00not-json\xff\nunknown original body"


def _original_page(
    entries: bytes = _ENTRY_XML,
    *,
    bucket: str = "synthetic-witness-1",
    marker: str = "",
    version_marker: str = "",
    truncated: bool = False,
    next_marker: str = "",
    next_version_marker: str = "",
) -> bytes:
    # Callers supply literal percent-encoded wire labels, never parser output.
    return (
        (
            '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">\n'
            f"<Name>{bucket}</Name>\n"
            "<Prefix>checkpoints%2F00000000-0000-4000-8000-000000000011%2F</Prefix>\n"
            f"<KeyMarker>{marker}</KeyMarker><VersionIdMarker>{version_marker}</VersionIdMarker>"
            "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
            f"<IsTruncated>{str(truncated).lower()}</IsTruncated>"
            f"<NextKeyMarker>{next_marker}</NextKeyMarker>"
            f"<NextVersionIdMarker>{next_version_marker}</NextVersionIdMarker>"
        ).encode()
        + entries
        + b"\n</ListVersionsResult>"
    )


def _collector(collection: ModuleType) -> Any:
    entry_point = getattr(collection, "collect_required_checkpoint_history", None)
    assert callable(entry_point), "required-witness collector entry point is not implemented"
    return entry_point


def _boundary_transports(
    monkeypatch: pytest.MonkeyPatch,
    pages: list[tuple[str, str | None, str | None, Any]],
    bodies: list[bytes | BaseException],
) -> tuple[list[tuple[str, str, str]], list[threading.Event]]:
    from easysynq_api.services.audit import isolated_raw, isolated_version_page
    from easysynq_api.services.audit.raw_transport import RawCheckpointVersion

    gets: list[tuple[str, str, str]] = []
    events: list[threading.Event] = []

    def list_page(
        reader: Any,
        org: UUID,
        *,
        key_marker: str | None = None,
        version_id_marker: str | None = None,
        cancel: Any = None,
    ) -> Any:
        assert org == ORG_ID
        assert type(cancel) is threading.Event
        events.append(cancel)
        assert pages, "collector sent an unplanned list request"
        bucket, key, version, outcome = pages.pop(0)
        assert (reader.bucket, key_marker, version_id_marker) == (bucket, key, version)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def read_body(reader: Any, ref: Any, *, cancel: Any = None) -> Any:
        assert type(cancel) is threading.Event
        events.append(cancel)
        gets.append((reader.bucket, ref.key, ref.version_id))
        assert bodies, "collector sent an unplanned version GET"
        outcome = bodies.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return RawCheckpointVersion(ref.key, ref.version_id, outcome)

    monkeypatch.setattr(
        isolated_version_page, "read_raw_checkpoint_version_page_isolated", list_page
    )
    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", read_body)
    return gets, events


def _raw_page(
    body: bytes,
    count: int = 1,
    *,
    truncated: bool = False,
    next_key: str | None = None,
    next_version: str | None = None,
) -> Any:
    from easysynq_api.services.audit.sink import CheckpointVersionRef, CheckpointVersionsPage
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage

    return RawCheckpointVersionPage(
        body,
        CheckpointVersionsPage(
            (CheckpointVersionRef(_EXACT_KEY, "null"),) * count,
            (),
            truncated,
            next_key,
            next_version,
        ),
    )


def test_collects_every_required_witness_with_unchanged_version_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from easysynq_api.services.audit.raw_transport import RawVersionReadError

    collection = _collection_module()
    collect = _collector(collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first_xml = _original_page()
    second_xml = _original_page(bucket="synthetic-witness-2")
    pages = [
        ("synthetic-witness-1", None, None, _raw_page(first_xml)),
        ("synthetic-witness-2", None, None, _raw_page(second_xml)),
    ]
    bodies = [_OPAQUE_BODY, RawVersionReadError("PROVIDER_FAILURE")]
    gets, events = _boundary_transports(monkeypatch, pages, bodies)

    report = collect(
        ORG_ID,
        (_pin(_OTHER_NAMESPACE), _pin()),
        (_required(collection, _OTHER_NAMESPACE), _required(collection)),
        _limits(collection),
    )

    assert report.status == "incomplete"
    assert report.scope == "required-witness-provider-traversal"
    assert [w.witness_id for w in report.witnesses] == [WITNESS_ID, OTHER_WITNESS_ID]
    assert [w.terminal_reached for w in report.witnesses] == [True, True]
    assert [w.unavailable_reads for w in report.witnesses] == [0, 1]
    assert [w.successful_reads for w in report.witnesses] == [1, 0]
    assert [w.version_observations for w in report.witnesses] == [1, 1]
    assert report.issues == (
        collection.HistoryCollectionIssue(
            "VERSION_UNAVAILABLE", "incomplete", OTHER_WITNESS_ID, 1, (2,)
        ),
    )
    assert (report.failed_issues, report.incomplete_issues, report.issues_omitted) == (0, 1, 0)
    assert "body-format-and-signatures" in report.unproved_checks
    assert report.admitted_total_bytes == len(first_xml) + len(second_xml) + len(_OPAQUE_BODY)
    assert gets == [
        ("synthetic-witness-1", _EXACT_KEY, "null"),
        ("synthetic-witness-2", _EXACT_KEY, "null"),
    ]
    assert len(events) == 4 and all(event is events[0] for event in events)
    assert not pages and not bodies
    assert list(tmp_path.iterdir()) == []
    assert "fixture-secret" not in repr(report)
    assert "unclassified" not in repr(report)


def test_late_conflict_after_4096_deliveries_keeps_every_duplicate_get(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    collect = _collector(collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # Five independently specified pages: 4,000 + 98 deliveries. Delivery 4,098
    # changes bytes at the same locator; all earlier 4,097 deliveries are identical.
    specifications = [
        (None, "", "cursor-a", 1000),
        ("cursor-a", "cursor-a", "cursor-b", 1000),
        ("cursor-b", "cursor-b", "cursor-c", 1000),
        ("cursor-c", "cursor-c", "cursor-d", 1000),
        ("cursor-d", "cursor-d", None, 98),
    ]
    pages = []
    xml_bodies = []
    for requested, marker, following, count in specifications:
        xml = _original_page(
            _ENTRY_XML * count,
            marker=_WIRE_PREFIX + marker if marker else "",
            truncated=following is not None,
            next_marker=_WIRE_PREFIX + following if following else "",
        )
        xml_bodies.append(xml)
        pages.append(
            (
                "synthetic-witness-1",
                _PREFIX + requested if requested else None,
                None,
                _raw_page(
                    xml,
                    count,
                    truncated=following is not None,
                    next_key=_PREFIX + following if following else None,
                ),
            )
        )
    bodies = [b"original"] * 4097 + [b"changed"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)

    report = collect(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))

    assert report.status == "failed"
    (witness,) = report.witnesses
    assert (witness.page_attempts, witness.admitted_pages, witness.terminal_reached) == (5, 5, True)
    assert (witness.version_observations, witness.successful_reads, witness.unavailable_reads) == (
        4098,
        4098,
        0,
    )
    assert (witness.duplicate_body_deliveries, witness.conflicting_locators) == (4096, 1)
    assert report.issues == (
        collection.HistoryCollectionIssue("LOCATOR_CONFLICT", "failed", WITNESS_ID, 1, (4098,)),
    )
    assert report.admitted_total_bytes == sum(map(len, xml_bodies)) + 4097 * 8 + 7
    assert gets == [("synthetic-witness-1", _EXACT_KEY, "null")] * 4098
    assert not pages and not bodies
    assert list(tmp_path.iterdir()) == []


def test_nonadjacent_populated_cursor_cycle_records_preceding_body_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    collect = _collector(collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # The same key with literal "null" versus an absent version is two cursors.
    xmls = [
        _original_page(
            truncated=True, next_marker=_WIRE_PREFIX + "cursor", next_version_marker="null"
        ),
        _original_page(
            marker=_WIRE_PREFIX + "cursor",
            version_marker="null",
            truncated=True,
            next_marker=_WIRE_PREFIX + "cursor",
        ),
        _original_page(
            marker=_WIRE_PREFIX + "cursor",
            truncated=True,
            next_marker=_WIRE_PREFIX + "cursor",
            next_version_marker="null",
        ),
    ]
    pages = [
        (
            "synthetic-witness-1",
            None,
            None,
            _raw_page(xmls[0], truncated=True, next_key=_PREFIX + "cursor", next_version="null"),
        ),
        (
            "synthetic-witness-1",
            _PREFIX + "cursor",
            "null",
            _raw_page(xmls[1], truncated=True, next_key=_PREFIX + "cursor"),
        ),
        (
            "synthetic-witness-1",
            _PREFIX + "cursor",
            None,
            _raw_page(xmls[2], truncated=True, next_key=_PREFIX + "cursor", next_version="null"),
        ),
    ]
    bodies = [b"first", b"first", b"last"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)

    report = collect(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))

    assert report.status == "failed"
    (witness,) = report.witnesses
    assert (witness.page_attempts, witness.admitted_pages, witness.terminal_reached) == (
        3,
        3,
        False,
    )
    assert (witness.version_observations, witness.successful_reads) == (3, 3)
    assert (witness.duplicate_body_deliveries, witness.conflicting_locators) == (1, 1)
    assert report.issues == (
        collection.HistoryCollectionIssue("CURSOR_CYCLE", "incomplete", WITNESS_ID, 1, ()),
        collection.HistoryCollectionIssue("LOCATOR_CONFLICT", "failed", WITNESS_ID, 1, (3,)),
    )
    assert (report.failed_issues, report.incomplete_issues) == (1, 1)
    assert report.admitted_total_bytes == sum(map(len, xmls)) + 14
    assert gets == [("synthetic-witness-1", _EXACT_KEY, "null")] * 3
    assert not pages and not bodies
    assert list(tmp_path.iterdir()) == []


def test_empty_truncated_cursor_invalid_is_list_gap_and_next_witness_is_attempted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    collect = _collector(collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # R83 rejects an empty truncated response before returning an admitted page.
    # Its original failure payload contributes no admitted bytes.
    second_xml = _original_page(b"", bucket="synthetic-witness-2")
    pages = [
        (
            "synthetic-witness-1",
            None,
            None,
            version_page_transport.VersionPageReadError("CURSOR_INVALID"),
        ),
        ("synthetic-witness-2", None, None, _raw_page(second_xml, 0)),
    ]
    bodies = []
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)

    report = collect(
        ORG_ID,
        (_pin(), _pin(_OTHER_NAMESPACE)),
        (_required(collection), _required(collection, _OTHER_NAMESPACE)),
        _limits(collection),
    )

    assert report.status == "incomplete"
    assert [w.terminal_reached for w in report.witnesses] == [False, True]
    assert [w.page_attempts for w in report.witnesses] == [1, 1]
    assert [w.admitted_pages for w in report.witnesses] == [0, 1]
    assert [w.version_observations for w in report.witnesses] == [0, 0]
    assert report.issues == (
        collection.HistoryCollectionIssue("LIST_UNAVAILABLE", "incomplete", WITNESS_ID, 1, ()),
    )
    assert report.admitted_total_bytes == len(second_xml)
    assert not gets and not pages and not bodies
    assert list(tmp_path.iterdir()) == []


def _exception_nodes(error: BaseException) -> list[BaseException]:
    """Iterative test oracle, including group identities without Python recursion."""
    pending = [error]
    nodes = []
    while pending:
        item = pending.pop()
        nodes.append(item)
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
    return nodes


def _capture_owners(monkeypatch: pytest.MonkeyPatch, collection: ModuleType) -> list[Any]:
    owners = []
    original_init = collection._CollectionOwner.__init__

    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        owners.append(self)

    monkeypatch.setattr(collection._CollectionOwner, "__init__", initialize)
    return owners


@pytest.mark.parametrize("invalid", ["pin", "missing", "extra", "duplicate", "reader", "limits"])
def test_public_admission_rejects_entire_request_before_ownership_or_io(
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    from easysynq_api.services.audit import _history_spool, isolated_raw, isolated_version_page

    collection = _collection_module()
    pins = (_pin(), _pin(_OTHER_NAMESPACE))
    readers = (_required(collection), _required(collection, _OTHER_NAMESPACE))
    limits = _limits(collection)
    if invalid == "pin":
        pins = (pins[0], dataclasses.replace(pins[1], namespace_hash="0" * 64))
    elif invalid == "missing":
        readers = readers[:1]
    elif invalid == "extra":
        pins = pins[:1]
    elif invalid == "duplicate":
        readers = (readers[0], readers[0])
    elif invalid == "reader":
        readers = (
            readers[0],
            dataclasses.replace(
                readers[1], reader=dataclasses.replace(readers[1].reader, region="")
            ),
        )
    else:
        limits = dataclasses.replace(limits, maximum_pages=True)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid later witness reached runtime ownership or I/O")

    monkeypatch.setattr(collection, "_CollectionOwner", forbidden)
    monkeypatch.setattr(_history_spool, "_SpoolSession", forbidden)
    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", forbidden)
    monkeypatch.setattr(
        isolated_version_page, "read_raw_checkpoint_version_page_isolated", forbidden
    )
    with pytest.raises(collection.HistoryCollectionInputError):
        _collector(collection)(ORG_ID, pins, readers, limits)


@pytest.mark.parametrize("boundary", ["list", "read"])
@pytest.mark.parametrize(
    "family,code",
    [
        ("transport", "PROVIDER_FAILURE"),
        ("transport", "TRANSPORT_FAILURE"),
        ("transport", "RESPONSE_INVALID"),
        ("transport", "BODY_LIMIT"),
        ("transport", "LENGTH_MISMATCH"),
        ("transport", "ROUTING_REJECTED"),
        ("transport", "DEADLINE_EXCEEDED"),
        ("isolated", "RUNTIME_UNSUPPORTED"),
        ("isolated", "WORKER_START_FAILED"),
        ("isolated", "WORKER_FAILED"),
        ("isolated", "PROTOCOL_INVALID"),
        ("isolated", "OUTPUT_LIMIT"),
        ("isolated", "DEADLINE_EXCEEDED"),
    ],
)
def test_plain_request_failures_remain_gaps_and_required_peer_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    family: str,
    code: str,
) -> None:
    from easysynq_api.services.audit import isolated_raw, isolated_version_page, raw_transport

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    error_class = {
        ("list", "transport"): version_page_transport.VersionPageReadError,
        ("list", "isolated"): isolated_version_page.IsolatedVersionPageError,
        ("read", "transport"): raw_transport.RawVersionReadError,
        ("read", "isolated"): isolated_raw.IsolatedRawReadError,
    }[boundary, family]
    failure = error_class(code)
    first_xml = _original_page()
    second_xml = _original_page(bucket="synthetic-witness-2")
    pages = [
        (
            "synthetic-witness-1",
            None,
            None,
            failure if boundary == "list" else _raw_page(first_xml),
        ),
        ("synthetic-witness-2", None, None, _raw_page(second_xml)),
    ]
    bodies = [b"healthy"] if boundary == "list" else [failure, b"healthy"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)

    report = _collector(collection)(
        ORG_ID,
        (_pin(), _pin(_OTHER_NAMESPACE)),
        (_required(collection), _required(collection, _OTHER_NAMESPACE)),
        _limits(collection),
    )

    assert report.status == "incomplete"
    assert [w.successful_reads for w in report.witnesses] == [0, 1]
    assert [w.unavailable_reads for w in report.witnesses] == (
        [0, 0] if boundary == "list" else [1, 0]
    )
    assert [w.terminal_reached for w in report.witnesses] == (
        [False, True] if boundary == "list" else [True, True]
    )
    assert report.issues == (
        collection.HistoryCollectionIssue(
            "LIST_UNAVAILABLE" if boundary == "list" else "VERSION_UNAVAILABLE",
            "incomplete",
            WITNESS_ID,
            1,
            () if boundary == "list" else (1,),
        ),
    )
    assert report.admitted_total_bytes == len(second_xml) + 7 + (
        0 if boundary == "list" else len(first_xml)
    )
    assert len(gets) == (1 if boundary == "list" else 2)
    assert not pages and not bodies and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "boundary,code",
    [
        ("list", "PAGE_LIMIT"),
        ("list", "SCOPE_MISMATCH"),
        ("list", "CURSOR_INVALID"),
        ("read", "VERSION_MISMATCH"),
        ("read", "DELETE_MARKER"),
    ],
)
def test_boundary_specific_codes_preserve_counter_and_severity_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    code: str,
) -> None:
    from easysynq_api.services.audit.raw_transport import RawVersionReadError

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    xml = _original_page()
    failure = (
        version_page_transport.VersionPageReadError(code)
        if boundary == "list"
        else RawVersionReadError(code)
    )
    pages = [("synthetic-witness-1", None, None, failure if boundary == "list" else _raw_page(xml))]
    bodies = [] if boundary == "list" else [failure]
    _boundary_transports(monkeypatch, pages, bodies)

    report = _collector(collection)(
        ORG_ID, (_pin(),), (_required(collection),), _limits(collection)
    )

    (witness,) = report.witnesses
    assert witness.delete_observations == 0
    assert witness.successful_reads == 0
    assert witness.unavailable_reads == (0 if boundary == "list" else 1)
    assert report.status == ("failed" if code == "DELETE_MARKER" else "incomplete")
    (issue,) = report.issues
    assert issue.code == (
        "LIST_UNAVAILABLE"
        if boundary == "list"
        else "DELETE_OBSERVATION"
        if code == "DELETE_MARKER"
        else "VERSION_UNAVAILABLE"
    )
    assert not pages and not bodies and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("boundary", ["list", "read"])
@pytest.mark.parametrize(
    "kind",
    [
        "cleanup",
        "isolated-cleanup",
        "unexpected",
        "fatal",
        "known-group",
        "mixed-group",
        "subclass",
    ],
)
@pytest.mark.parametrize("cancelled", [False, True])
def test_fatal_or_grouped_transport_outcomes_preserve_identity_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    kind: str,
    cancelled: bool,
) -> None:
    from easysynq_api.services.audit import isolated_raw, isolated_version_page, raw_transport

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    error_class = (
        version_page_transport.VersionPageReadError
        if boundary == "list"
        else raw_transport.RawVersionReadError
    )
    isolated_class = (
        isolated_version_page.IsolatedVersionPageError
        if boundary == "list"
        else isolated_raw.IsolatedRawReadError
    )
    fatal = KeyboardInterrupt("synthetic fatal")
    ordinary = error_class("PROVIDER_FAILURE")

    class ErrorSubclass(error_class):
        pass

    failures = {
        "cleanup": error_class("CLEANUP_FAILED"),
        "isolated-cleanup": isolated_class("CLEANUP_FAILED"),
        "unexpected": RuntimeError("synthetic unexpected"),
        "fatal": fatal,
        "known-group": ExceptionGroup(
            "synthetic known group", [ordinary, error_class("TRANSPORT_FAILURE")]
        ),
        "mixed-group": BaseExceptionGroup(
            "synthetic mixed group", [ordinary, BaseExceptionGroup("nested", [fatal])]
        ),
        "subclass": ErrorSubclass("PROVIDER_FAILURE"),
    }
    failure = failures[kind]
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    gets, _events = _boundary_transports(monkeypatch, pages, [])
    calls = []

    def fail(*args: Any, **kwargs: Any) -> Any:
        calls.append(boundary)
        if cancelled:
            caller.set()
        raise failure

    module = isolated_version_page if boundary == "list" else isolated_raw
    name = (
        "read_raw_checkpoint_version_page_isolated"
        if boundary == "list"
        else "read_raw_checkpoint_version_isolated"
    )
    monkeypatch.setattr(module, name, fail)
    with pytest.raises(BaseException) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    assert any(node is failure for node in _exception_nodes(caught.value))
    assert calls == [boundary]
    assert not gets
    assert len(owners) == 1 and not owners[0]._thread.is_alive()
    assert list(tmp_path.iterdir()) == []


def test_deep_group_identity_survives_simultaneous_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sys

    from easysynq_api.services.audit import isolated_raw

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    failure: BaseException = KeyboardInterrupt("synthetic deeply nested fatal")
    for _ in range(sys.getrecursionlimit() + 50):
        failure = BaseExceptionGroup("synthetic nested group", [failure])
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [])

    def fail(*args: Any, **kwargs: Any) -> Any:
        caller.set()
        raise failure

    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", fail)
    with pytest.raises(BaseException) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    if not any(node is failure for node in _exception_nodes(caught.value)):
        pytest.fail(
            "original deep group identity was replaced during cancellation handling", pytrace=False
        )
    assert not owners[0]._thread.is_alive()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("changed", ["key", "version"])
def test_returned_get_identity_mismatch_aborts_before_body_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: str,
) -> None:
    from easysynq_api.services.audit import _history_spool, isolated_raw, raw_transport

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [])

    def wrong_identity(reader: Any, ref: Any, **kwargs: Any) -> Any:
        return raw_transport.RawCheckpointVersion(
            ref.key + "-different" if changed == "key" else ref.key,
            "other-version" if changed == "version" else ref.version_id,
            b"untrusted",
        )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("mismatched GET body reached storage")

    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", wrong_identity)
    monkeypatch.setattr(_history_spool._SpoolSession, "record_body", forbidden)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "changed,value", [("first_ordinal", 2), ("version_count", 0), ("delete_count", 1)]
)
def test_page_admission_count_or_ordinal_mismatch_aborts_before_any_get(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: str,
    value: int,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    gets, _events = _boundary_transports(monkeypatch, pages, [])
    original = _history_spool._SpoolSession.admit_page

    def corrupt(self: Any, *args: Any, **kwargs: Any) -> Any:
        admitted = original(self, *args, **kwargs)
        return dataclasses.replace(admitted, **{changed: value})

    monkeypatch.setattr(_history_spool._SpoolSession, "admit_page", corrupt)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "PROTOCOL_INVALID"
    assert not gets and list(tmp_path.iterdir()) == []


def test_original_page_parent_count_disagreement_aborts_before_any_get(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page(), 2))]
    gets, _events = _boundary_transports(monkeypatch, pages, [])
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "PROTOCOL_INVALID"
    assert not gets and list(tmp_path.iterdir()) == []


_MARKER_XML = (
    b"<DeleteMarker><Key>checkpoints%2F00000000-0000-4000-8000-000000000011%2F"
    b"removed</Key><VersionId>deleted</VersionId><IsLatest>true</IsLatest></DeleteMarker>"
)
_INELIGIBLE_XML = (
    b"<Version><Key>checkpoints%2F00000000-0000-4000-8000-000000000011%2F"
    b"control%01</Key><VersionId>null</VersionId><IsLatest>false</IsLatest></Version>"
)


@pytest.mark.parametrize("maximum_issues", [1, 4, 5])
def test_markers_ineligible_refs_and_caps_keep_sticky_groups_and_witness_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    maximum_issues: int,
) -> None:
    from easysynq_api.services.audit import _history_spool
    from easysynq_api.services.audit.raw_transport import RawVersionReadError
    from easysynq_api.services.audit.sink import CheckpointVersionRef, CheckpointVersionsPage
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    ref = CheckpointVersionRef(_EXACT_KEY, "null")
    control = CheckpointVersionRef(_PREFIX + "control\x01", "null")
    marker = CheckpointVersionRef(_PREFIX + "removed", "deleted")
    # The XML starts with a marker, while observation ordinals place versions first.
    first_xml = _original_page(_MARKER_XML + _ENTRY_XML * 5 + _INELIGIBLE_XML + _ENTRY_XML * 2)
    first_page = RawCheckpointVersionPage(
        first_xml,
        CheckpointVersionsPage((ref,) * 5 + (control, ref, ref), (marker,), False, None, None),
    )
    second_xml = _original_page(bucket="synthetic-witness-2")
    pages = [
        ("synthetic-witness-1", None, None, first_page),
        ("synthetic-witness-2", None, None, _raw_page(second_xml)),
    ]
    bodies = [RawVersionReadError("PROVIDER_FAILURE") for _ in range(5)] + [
        b"first",
        b"second",
        b"peer",
    ]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)
    retained_pages = []
    retained_bodies = []
    original_page = _history_spool._SpoolSession.admit_page
    original_body = _history_spool._SpoolSession.record_body

    def retain_page(self: Any, ticket: Any, body: bytes) -> Any:
        result = original_page(self, ticket, body)
        retained_pages.append(body)
        return result

    def retain_body(self: Any, ordinal: int, body: bytes) -> None:
        original_body(self, ordinal, body)
        retained_bodies.append((ordinal, body))

    monkeypatch.setattr(_history_spool._SpoolSession, "admit_page", retain_page)
    monkeypatch.setattr(_history_spool._SpoolSession, "record_body", retain_body)

    report = _collector(collection)(
        ORG_ID,
        (_pin(), _pin(_OTHER_NAMESPACE)),
        (_required(collection), _required(collection, _OTHER_NAMESPACE)),
        _limits(collection, maximum_issues=maximum_issues),
    )

    assert report.status == "failed"
    first, second = report.witnesses
    assert (
        first.version_observations,
        first.delete_observations,
        first.successful_reads,
        first.unavailable_reads,
        first.duplicate_body_deliveries,
        first.conflicting_locators,
    ) == (8, 1, 2, 6, 0, 1)
    assert (
        second.version_observations,
        second.delete_observations,
        second.successful_reads,
        second.unavailable_reads,
        second.duplicate_body_deliveries,
        second.conflicting_locators,
    ) == (1, 0, 1, 0, 0, 0)
    expected_issues = (
        collection.HistoryCollectionIssue("DELETE_OBSERVATION", "failed", WITNESS_ID, 1, (9,)),
        collection.HistoryCollectionIssue("INELIGIBLE_LOCATOR", "incomplete", WITNESS_ID, 1, (6,)),
        collection.HistoryCollectionIssue("LOCATOR_CONFLICT", "failed", WITNESS_ID, 1, (8,)),
        collection.HistoryCollectionIssue(
            "VERSION_UNAVAILABLE", "incomplete", WITNESS_ID, 5, (1, 2, 3, 4)
        ),
    )
    assert report.issues == expected_issues[:maximum_issues]
    assert (report.failed_issues, report.incomplete_issues, report.issues_omitted) == (
        2,
        2,
        3 if maximum_issues == 1 else 0,
    )
    assert report.admitted_total_bytes == len(first_xml) + len(second_xml) + 15
    assert retained_pages == [first_xml, second_xml]
    assert retained_bodies == [(7, b"first"), (8, b"second"), (10, b"peer")]
    assert gets == [("synthetic-witness-1", _EXACT_KEY, "null")] * 7 + [
        ("synthetic-witness-2", _EXACT_KEY, "null")
    ]
    for secret in (
        "fixture-access",
        "fixture-secret",
        "witness-1.invalid",
        "control",
        "removed",
        "second",
    ):
        assert secret not in repr(report)
    assert not pages and not bodies and list(tmp_path.iterdir()) == []


def test_omitted_failed_group_still_controls_status_when_displayed_group_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from easysynq_api.services.audit.raw_transport import RawVersionReadError

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [
        ("synthetic-witness-1", None, None, _raw_page(_original_page())),
        (
            "synthetic-witness-2",
            None,
            None,
            _raw_page(_original_page(bucket="synthetic-witness-2")),
        ),
    ]
    bodies = [RawVersionReadError("PROVIDER_FAILURE"), RawVersionReadError("DELETE_MARKER")]
    _boundary_transports(monkeypatch, pages, bodies)
    report = _collector(collection)(
        ORG_ID,
        (_pin(), _pin(_OTHER_NAMESPACE)),
        (_required(collection), _required(collection, _OTHER_NAMESPACE)),
        _limits(collection, maximum_issues=1),
    )
    assert report.status == "failed"
    assert report.issues == (
        collection.HistoryCollectionIssue("VERSION_UNAVAILABLE", "incomplete", WITNESS_ID, 1, (1,)),
    )
    assert (report.failed_issues, report.incomplete_issues, report.issues_omitted) == (1, 1, 1)
    assert list(tmp_path.iterdir()) == []


def test_all_required_empty_terminal_histories_are_only_traversed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    xmls = [_original_page(b""), _original_page(b"", bucket="synthetic-witness-2")]
    pages = [
        ("synthetic-witness-1", None, None, _raw_page(xmls[0], 0)),
        ("synthetic-witness-2", None, None, _raw_page(xmls[1], 0)),
    ]
    gets, _events = _boundary_transports(monkeypatch, pages, [])
    report = _collector(collection)(
        ORG_ID,
        (_pin(), _pin(_OTHER_NAMESPACE)),
        (_required(collection), _required(collection, _OTHER_NAMESPACE)),
        _limits(collection),
    )
    assert report.status == "traversed"
    assert [w.terminal_reached for w in report.witnesses] == [True, True]
    assert [w.page_attempts for w in report.witnesses] == [1, 1]
    assert [w.admitted_pages for w in report.witnesses] == [1, 1]
    assert [w.version_observations + w.delete_observations for w in report.witnesses] == [0, 0]
    assert report.issues == ()
    assert (report.failed_issues, report.incomplete_issues, report.issues_omitted) == (0, 0, 0)
    assert report.admitted_total_bytes == sum(map(len, xmls))
    assert report.unproved_checks == (
        "body-format-and-signatures",
        "global-history-consistency",
        "provider-non-omission",
        "atomic-snapshot",
        "historical-deletion-absence",
        "witness-custody",
        "database-chain-agreement",
        "freshness",
        "rollback-memory-continuity",
        "key-activation",
    )
    assert not gets and not pages and list(tmp_path.iterdir()) == []
    assert not owners[0]._thread.is_alive()


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("budget", ["pages", "observations", "xml", "bytes"])
def test_runtime_budgets_before_at_after_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    budget: str,
    offset: int,
) -> None:
    from easysynq_api.services.audit.sink import CheckpointVersionRef, CheckpointVersionsPage
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pins = (_pin(),)
    readers = (_required(collection),)
    xml = _original_page()
    pages = [("synthetic-witness-1", None, None, _raw_page(xml))]
    bodies = [b"same"]
    if budget == "pages":
        pins += (_pin(_OTHER_NAMESPACE),)
        readers += (_required(collection, _OTHER_NAMESPACE),)
        xml = _original_page(b"", bucket="synthetic-witness-2")
        pages = [
            (
                "synthetic-witness-1",
                None,
                None,
                version_page_transport.VersionPageReadError("PROVIDER_FAILURE"),
            ),
            ("synthetic-witness-2", None, None, _raw_page(xml, 0)),
        ]
        bodies = []
        limits = _limits(collection, maximum_pages=2 + offset)
    elif budget == "observations":
        xml = _original_page(_ENTRY_XML + _MARKER_XML)
        page = RawCheckpointVersionPage(
            xml,
            CheckpointVersionsPage(
                (CheckpointVersionRef(_EXACT_KEY, "null"),),
                (CheckpointVersionRef(_PREFIX + "removed", "deleted"),),
                False,
                None,
                None,
            ),
        )
        pages = [("synthetic-witness-1", None, None, page)]
        limits = _limits(collection, maximum_observations=2 + offset)
    elif budget == "xml":
        xml = _original_page(b"")
        pages = [("synthetic-witness-1", None, None, _raw_page(xml, 0))]
        bodies = []
        limits = _limits(collection, maximum_total_bytes=len(xml) + offset)
    else:
        xml = _original_page(_ENTRY_XML * 2)
        pages = [("synthetic-witness-1", None, None, _raw_page(xml, 2))]
        bodies = [b"same", b"same"]
        limits = _limits(collection, maximum_total_bytes=len(xml) + 8 + offset)
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)

    if offset == -1:
        with pytest.raises(collection.HistoryCollectionError) as caught:
            _collector(collection)(ORG_ID, pins, readers, limits)
        assert caught.value.code == "RESOURCE_LIMIT"
        assert len(gets) == (2 if budget == "bytes" else 0)
        assert len(pages) == (1 if budget == "pages" else 0)
    else:
        report = _collector(collection)(ORG_ID, pins, readers, limits)
        assert report.status == (
            {"pages": "incomplete", "observations": "failed"}.get(budget, "traversed")
        )
        assert report.admitted_total_bytes == len(xml) + (
            8 if budget == "bytes" else 4 if budget == "observations" else 0
        )
        if budget == "bytes":
            assert report.witnesses[0].duplicate_body_deliveries == 1
        assert not pages and not bodies
    assert list(tmp_path.iterdir()) == []


def test_spool_file_limit_exhaustion_aborts_without_a_prefix_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page(_ENTRY_XML * 2), 2))]
    bodies = [b"x" * 65536, b"unreached"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(
            ORG_ID,
            (_pin(),),
            (_required(collection),),
            _limits(collection, maximum_spool_bytes=65536),
        )
    assert caught.value.code == "RESOURCE_LIMIT"
    assert len(gets) == 1
    assert bodies == [b"unreached"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reason", ["caller", "deadline"])
@pytest.mark.parametrize(
    "boundary",
    [
        "start",
        "enter",
        "reserve",
        "list",
        "admit",
        "read",
        "body",
        "list-failure",
        "version-failure",
        "cycle",
        "finish",
        "exit",
        "watchdog-close",
        "report",
    ],
)
def test_owner_interrupt_at_each_publication_or_io_boundary_cleans_before_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    boundary: str,
) -> None:
    import time

    from easysynq_api.services.audit import _history_spool, isolated_raw, isolated_version_page
    from easysynq_api.services.audit.raw_transport import RawVersionReadError

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    offset = 0.0
    tripped = []
    monkeypatch.setattr(collection, "_monotonic", lambda: time.monotonic() + offset)

    def trip() -> None:
        nonlocal offset
        if not tripped:
            tripped.append(boundary)
            if reason == "caller":
                caller.set()
            else:
                offset = 61.0

    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    bodies = [b"opaque"]
    if boundary == "list-failure":
        pages = [
            (
                "synthetic-witness-1",
                None,
                None,
                version_page_transport.VersionPageReadError("PROVIDER_FAILURE"),
            )
        ]
        bodies = []
    elif boundary == "version-failure":
        bodies = [RawVersionReadError("PROVIDER_FAILURE")]
    elif boundary == "cycle":
        pages = []
        for requested, following in ((None, "one"), ("one", "two"), ("two", "one")):
            xml = _original_page(
                marker=_WIRE_PREFIX + requested if requested else "",
                truncated=True,
                next_marker=_WIRE_PREFIX + following,
            )
            pages.append(
                (
                    "synthetic-witness-1",
                    _PREFIX + requested if requested else None,
                    None,
                    _raw_page(xml, truncated=True, next_key=_PREFIX + following),
                )
            )
        bodies = [b"same"] * 3
    _boundary_transports(monkeypatch, pages, bodies)
    targets = {
        "start": (collection._CollectionOwner, "start"),
        "enter": (_history_spool._SpoolSession, "__enter__"),
        "reserve": (_history_spool._SpoolSession, "reserve_page"),
        "list": (isolated_version_page, "read_raw_checkpoint_version_page_isolated"),
        "admit": (_history_spool._SpoolSession, "admit_page"),
        "read": (isolated_raw, "read_raw_checkpoint_version_isolated"),
        "body": (_history_spool._SpoolSession, "record_body"),
        "list-failure": (_history_spool._SpoolSession, "record_list_failure"),
        "version-failure": (_history_spool._SpoolSession, "record_version_failure"),
        "cycle": (_history_spool._SpoolSession, "record_cycle"),
        "finish": (_history_spool._SpoolSession, "finish"),
        "exit": (_history_spool._SpoolSession, "__exit__"),
        "watchdog-close": (collection._CollectionOwner, "close"),
        "report": (collection, "HistoryCollectionReport"),
    }
    target, name = targets[boundary]
    original = getattr(target, name)

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        if boundary == "start":
            trip()
        result = original(*args, **kwargs)
        if boundary != "start":
            trip()
        return result

    monkeypatch.setattr(target, name, interrupt)
    expected = (
        collection.HistoryCollectionCancelled
        if reason == "caller"
        else collection.HistoryCollectionError
    )
    with pytest.raises(expected) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    if reason == "deadline":
        assert caught.value.code == "DEADLINE_EXCEEDED"
    assert tripped == [boundary]
    assert len(owners) == 1 and not owners[0]._thread.is_alive()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reason", ["caller", "deadline"])
@pytest.mark.parametrize("boundary", ["list", "read"])
def test_watchdog_interrupts_inflight_transport_with_same_event_as_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    boundary: str,
) -> None:
    import time

    from easysynq_api.services.audit import (
        _history_spool,
        isolated_raw,
        isolated_version_page,
        raw_transport,
    )

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    offset = 0.0
    monkeypatch.setattr(collection, "_monotonic", lambda: time.monotonic() + offset)
    storage_events = []
    original_enter = _history_spool._SpoolSession.__enter__

    def enter(self: Any) -> Any:
        result = original_enter(self)
        storage_events.append(self._cancel)
        return result

    monkeypatch.setattr(_history_spool._SpoolSession, "__enter__", enter)
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [])

    def blocked(*args: Any, cancel: Any = None, **kwargs: Any) -> Any:
        nonlocal offset
        assert type(cancel) is threading.Event
        assert cancel is storage_events[0] and cancel is not caller
        if reason == "caller":
            caller.set()
        else:
            offset = 61.0
        assert cancel.wait(1.0), "watchdog did not interrupt in-flight transport"
        if boundary == "list":
            raise version_page_transport.VersionPageReadCancelled()
        raise raw_transport.RawVersionReadCancelled()

    module = isolated_version_page if boundary == "list" else isolated_raw
    name = (
        "read_raw_checkpoint_version_page_isolated"
        if boundary == "list"
        else "read_raw_checkpoint_version_isolated"
    )
    monkeypatch.setattr(module, name, blocked)
    expected = (
        collection.HistoryCollectionCancelled
        if reason == "caller"
        else collection.HistoryCollectionError
    )
    with pytest.raises(expected) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    if reason == "deadline":
        assert caught.value.code == "DEADLINE_EXCEEDED"
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("boundary", ["list", "read"])
@pytest.mark.parametrize("reason", ["caller", "deadline"])
def test_owner_interrupt_wins_over_simultaneous_plain_request_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    reason: str,
) -> None:
    import time

    from easysynq_api.services.audit import isolated_raw, isolated_version_page, raw_transport

    collection = _collection_module()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    offset = 0.0
    monkeypatch.setattr(collection, "_monotonic", lambda: time.monotonic() + offset)
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [])

    def simultaneous(*args: Any, **kwargs: Any) -> Any:
        nonlocal offset
        if reason == "caller":
            caller.set()
        else:
            offset = 61.0
        cls = (
            version_page_transport.VersionPageReadError
            if boundary == "list"
            else raw_transport.RawVersionReadError
        )
        raise cls("DEADLINE_EXCEEDED")

    module = isolated_version_page if boundary == "list" else isolated_raw
    name = (
        "read_raw_checkpoint_version_page_isolated"
        if boundary == "list"
        else "read_raw_checkpoint_version_isolated"
    )
    monkeypatch.setattr(module, name, simultaneous)
    expected = (
        collection.HistoryCollectionCancelled
        if reason == "caller"
        else collection.HistoryCollectionError
    )
    with pytest.raises(expected) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    if reason == "deadline":
        assert caught.value.code == "DEADLINE_EXCEEDED"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("phase", ["before-start", "after-start", "stop", "join", "liveness"])
def test_watchdog_lifetime_faults_abort_and_preserve_unexpected_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    failure = KeyboardInterrupt("synthetic watchdog lifetime fault")
    original_start = threading.Thread.start
    original_join = threading.Thread.join
    original_alive = threading.Thread.is_alive
    original_init = collection._CollectionOwner.__init__
    fault_calls = []

    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if phase == "stop":

            def fail_stop() -> None:
                fault_calls.append(phase)
                raise failure

            monkeypatch.setattr(self._stop, "set", fail_stop)

    monkeypatch.setattr(collection._CollectionOwner, "__init__", initialize)

    def start(thread: threading.Thread) -> None:
        if thread.name == "audit-history-watchdog" and phase == "before-start":
            fault_calls.append(phase)
            raise failure
        original_start(thread)
        if thread.name == "audit-history-watchdog" and phase == "after-start":
            fault_calls.append(phase)
            raise failure

    def join(thread: threading.Thread, timeout: float | None = None) -> None:
        original_join(thread, timeout)
        if thread.name == "audit-history-watchdog" and phase == "join":
            fault_calls.append(phase)
            raise failure

    def alive(thread: threading.Thread) -> bool:
        if thread.name == "audit-history-watchdog" and phase == "liveness":
            fault_calls.append(phase)
            raise failure
        return original_alive(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(threading.Thread, "join", join)
    monkeypatch.setattr(threading.Thread, "is_alive", alive)
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [b"opaque"])
    try:
        with pytest.raises(BaseException) as caught:
            _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
        nodes = _exception_nodes(caught.value)
        assert any(node is failure for node in nodes)
        if phase == "liveness":
            assert any(
                type(node) is collection.HistoryCollectionError and node.code == "CLEANUP_FAILED"
                for node in nodes
            )
        assert fault_calls == [phase]
        assert len(owners) == 1 and not original_alive(owners[0]._thread)
        assert list(tmp_path.iterdir()) == []
    finally:
        for owner in owners:
            owner._stopping = True
            if owner._thread.ident is not None:
                original_join(owner._thread, 2.0)


def test_watchdog_target_failure_interrupts_network_and_never_reaches_thread_excepthook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import time

    from easysynq_api.services.audit import isolated_raw, raw_transport

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    in_flight = threading.Event()
    target_failed = threading.Event()
    failure = RuntimeError("synthetic target fault")
    leaked = []

    def clock() -> float:
        if threading.current_thread().name == "audit-history-watchdog" and in_flight.is_set():
            target_failed.set()
            raise failure
        return time.monotonic()

    monkeypatch.setattr(collection, "_monotonic", clock)
    monkeypatch.setattr(threading, "excepthook", leaked.append)
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [])

    def blocked(*args: Any, cancel: Any = None, **kwargs: Any) -> Any:
        in_flight.set()
        assert cancel.wait(1.0), "target failure did not interrupt shared ownership"
        raise raw_transport.RawVersionReadCancelled()

    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", blocked)
    with pytest.raises(BaseException) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert target_failed.is_set()
    assert any(node is failure for node in _exception_nodes(caught.value))
    assert leaked == []
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("code", ["STORAGE_FAILED", "PROTOCOL_INVALID", "RESOURCE_LIMIT"])
def test_storage_fault_after_committed_body_aborts_before_later_network_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code: str,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page(_ENTRY_XML * 2), 2))]
    bodies = [b"committed", b"must remain unread"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)
    original = _history_spool._SpoolSession.record_body
    failure = collection.HistoryCollectionError(code)

    def commit_then_fail(self: Any, ordinal: int, body: bytes) -> None:
        original(self, ordinal, body)
        raise failure

    monkeypatch.setattr(_history_spool._SpoolSession, "record_body", commit_then_fail)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value is failure
    assert gets == [("synthetic-witness-1", _EXACT_KEY, "null")]
    assert bodies == [b"must remain unread"]
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


def test_real_storage_worker_death_after_healthy_page_prevents_next_listing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first_xml = _original_page(truncated=True, next_marker=_WIRE_PREFIX + "cursor")
    next_xml = _original_page(marker=_WIRE_PREFIX + "cursor")
    pages = [
        (
            "synthetic-witness-1",
            None,
            None,
            _raw_page(first_xml, truncated=True, next_key=_PREFIX + "cursor"),
        ),
        ("synthetic-witness-1", _PREFIX + "cursor", None, _raw_page(next_xml)),
    ]
    bodies = [b"committed", b"unreached"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)
    original = _history_spool._SpoolSession.record_body
    dead_children = []

    def commit_then_kill(self: Any, ordinal: int, body: bytes) -> None:
        original(self, ordinal, body)
        process = self._process
        assert process is not None and process.returncode is None
        process.kill()
        process.wait(timeout=2.0)
        dead_children.append(process)

    monkeypatch.setattr(_history_spool._SpoolSession, "record_body", commit_then_kill)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "WORKER_FAILED"
    assert len(dead_children) == 1 and dead_children[0].returncode is not None
    assert gets == [("synthetic-witness-1", _EXACT_KEY, "null")]
    assert len(pages) == 1 and bodies == [b"unreached"]
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("boundary", ["reserve", "admit", "body"])
def test_real_storage_worker_death_at_io_boundary_prevents_later_network_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # The independently authored original page and parsed result both contain two
    # eligible deliveries. No second reservation intervenes between these GETs.
    original_xml = _original_page(_ENTRY_XML * 2)
    pages = [("synthetic-witness-1", None, None, _raw_page(original_xml, 2))]
    bodies = [b"first committed body", b"second must remain unread"]
    gets, _events = _boundary_transports(monkeypatch, pages, bodies)
    method = {"reserve": "reserve_page", "admit": "admit_page", "body": "record_body"}[boundary]
    original = getattr(_history_spool._SpoolSession, method)
    committed = []
    dead_children = []

    def complete_boundary_then_kill(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        if boundary == "body":
            committed.append(args)
        if not dead_children:
            process = self._process
            assert process is not None and process.returncode is None
            process.kill()
            process.wait(timeout=2.0)
            dead_children.append(process)
        # Return normally: the collector must discover the established death
        # before entering either unchanged network transport again.
        return result

    monkeypatch.setattr(_history_spool._SpoolSession, method, complete_boundary_then_kill)
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "WORKER_FAILED"
    assert len(dead_children) == 1 and dead_children[0].returncode is not None
    assert len(owners) == 1 and not owners[0]._thread.is_alive()
    assert list(tmp_path.iterdir()) == []
    assert gets == ([("synthetic-witness-1", _EXACT_KEY, "null")] if boundary == "body" else [])
    assert committed == ([(1, b"first committed body")] if boundary == "body" else [])
    assert bodies == (
        [b"second must remain unread"]
        if boundary == "body"
        else [b"first committed body", b"second must remain unread"]
    )
    assert len(pages) == (1 if boundary == "reserve" else 0)


def test_spool_cleanup_failure_with_cancellation_keeps_group_and_stops_watchdog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    caller = threading.Event()
    fatal = KeyboardInterrupt("synthetic cleanup fatal")
    cleanup_failed = collection.HistoryCollectionError("CLEANUP_FAILED")
    failure = BaseExceptionGroup("synthetic cleanup group", [fatal, cleanup_failed])
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [b"opaque"])
    original = _history_spool._SpoolSession.__exit__
    cleanup_calls = []

    def fail_cleanup(self: Any, *args: Any) -> None:
        original(self, *args)
        cleanup_calls.append("spool-cleaned")
        caller.set()
        raise failure

    monkeypatch.setattr(_history_spool._SpoolSession, "__exit__", fail_cleanup)
    with pytest.raises(BaseException) as caught:
        _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection), cancel=caller
        )
    nodes = _exception_nodes(caught.value)
    assert any(node is failure for node in nodes)
    assert any(node is fatal for node in nodes) and any(node is cleanup_failed for node in nodes)
    assert cleanup_calls == ["spool-cleaned"]
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


def test_report_is_constructed_only_after_spool_cleanup_and_watchdog_join(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from easysynq_api.services.audit import _history_spool

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [_OPAQUE_BODY])
    phases = []
    original_finish = _history_spool._SpoolSession.finish
    original_exit = _history_spool._SpoolSession.__exit__
    original_report = collection.HistoryCollectionReport

    def finish(self: Any) -> Any:
        summary = original_finish(self)
        assert list(tmp_path.iterdir()) == []
        assert self._process is not None and self._process.returncode == 0
        phases.append("storage-finished")
        return summary

    def exit_spool(self: Any, *args: Any) -> None:
        original_exit(self, *args)
        phases.append("storage-exited")

    def report(*args: Any, **kwargs: Any) -> Any:
        assert phases == ["storage-finished", "storage-exited"]
        assert list(tmp_path.iterdir()) == []
        assert len(owners) == 1 and not owners[0]._thread.is_alive()
        phases.append("report")
        return original_report(*args, **kwargs)

    monkeypatch.setattr(_history_spool._SpoolSession, "finish", finish)
    monkeypatch.setattr(_history_spool._SpoolSession, "__exit__", exit_spool)
    monkeypatch.setattr(collection, "HistoryCollectionReport", report)
    result = _collector(collection)(
        ORG_ID, (_pin(),), (_required(collection),), _limits(collection)
    )
    assert type(result) is original_report
    assert result.status == "traversed"
    assert phases == ["storage-finished", "storage-exited", "report"]


@pytest.mark.parametrize("offset", [-0.001, 0.0, 0.001])
def test_whole_owner_deadline_before_at_after_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    offset: float,
) -> None:
    import time

    from easysynq_api.services.audit import isolated_raw

    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    frozen = None
    monkeypatch.setattr(
        collection, "_monotonic", lambda: time.monotonic() if frozen is None else frozen
    )
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [b"opaque"])
    original = isolated_raw.read_raw_checkpoint_version_isolated

    def read_at_deadline(*args: Any, **kwargs: Any) -> Any:
        nonlocal frozen
        result = original(*args, **kwargs)
        # Freeze the owner clock at the exact absolute boundary; storage continues
        # using its real bounded monotonic clock and all waits retain real timeouts.
        frozen = owners[0].deadline + offset
        return result

    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", read_at_deadline)
    if offset < 0:
        report = _collector(collection)(
            ORG_ID, (_pin(),), (_required(collection),), _limits(collection)
        )
        assert report.status == "traversed"
    else:
        with pytest.raises(collection.HistoryCollectionError) as caught:
            _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
        assert caught.value.code == "DEADLINE_EXCEEDED"
    assert not owners[0]._thread.is_alive() and list(tmp_path.iterdir()) == []


def test_unproved_watchdog_termination_is_fixed_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    owners = _capture_owners(monkeypatch, collection)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    original_alive = threading.Thread.is_alive

    def unproved(thread: threading.Thread) -> bool:
        if thread.name == "audit-history-watchdog":
            return True
        return original_alive(thread)

    monkeypatch.setattr(threading.Thread, "is_alive", unproved)
    pages = [("synthetic-witness-1", None, None, _raw_page(_original_page()))]
    _boundary_transports(monkeypatch, pages, [b"opaque"])
    with pytest.raises(collection.HistoryCollectionError) as caught:
        _collector(collection)(ORG_ID, (_pin(),), (_required(collection),), _limits(collection))
    assert caught.value.code == "CLEANUP_FAILED"
    assert not original_alive(owners[0]._thread) and list(tmp_path.iterdir()) == []
