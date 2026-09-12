"""Admission must reject incomplete external scope before acquiring any resources."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import subprocess
import tempfile
import threading
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

from easysynq_api.services.audit import bootstrap_bridge as bridge
from easysynq_api.services.audit import history_collection as collection
from easysynq_api.services.audit import lineage
from easysynq_api.services.audit.sink import ExplicitHistoryReader

pytestmark = pytest.mark.unit
_MODULE = "easysynq_api.services.audit.history_reconciliation"
_FIXTURES = Path(__file__).parents[1] / "fixtures"
_NAMESPACES = json.loads((_FIXTURES / "audit_history_collection_vectors.json").read_text())[
    "namespaces"
]
_EXTERNAL = json.loads((_FIXTURES / "audit_bootstrap_bridge_vectors.json").read_text())["packages"][
    "baseline"
]["enrollment"]
_BOUNDS = (
    ("maximum_pages", 1, 4096),
    ("maximum_observations", 1, 100_000),
    ("maximum_bridge_pages", 1, 1024),
    ("maximum_manifest_entries", 1, 100_000),
    ("maximum_total_bytes", 1, 1_073_741_824),
    ("maximum_spool_bytes", 65_536, 1_073_741_824),
    ("maximum_wall_seconds", 1, 86_400),
    ("maximum_issue_groups", 1, 1_000_000),
    ("maximum_issues", 1, 32),
)


class _Int(int):
    pass


class _Bytes(bytes):
    pass


class _Tuple(tuple):
    pass


class _String(str):
    pass


class _UUID(UUID):
    pass


class _Event(threading.Event):
    pass


def _module() -> ModuleType:
    try:
        return importlib.import_module(_MODULE)
    except ModuleNotFoundError as error:
        if error.name != _MODULE:
            raise
        pytest.fail("audit history reconciliation module is not implemented", pytrace=False)


def _limits(module: ModuleType, **changes: Any) -> Any:
    values = {
        "maximum_pages": 8,
        "maximum_observations": 5000,
        "maximum_bridge_pages": 16,
        "maximum_manifest_entries": 5000,
        "maximum_total_bytes": 16_777_216,
        "maximum_spool_bytes": 33_554_432,
        "maximum_wall_seconds": 60,
        "maximum_issue_groups": 10_000,
        "maximum_issues": 8,
    }
    values.update(changes)
    return module.HistoryReconciliationLimits(**values)


def _inputs(module: ModuleType) -> dict[str, Any]:
    pin = _EXTERNAL["bootstrap"]
    boundary = pin["audit_boundary"]
    enrollment = bridge.BridgeEnrollment(
        lineage.StreamEnrollment(
            UUID(_EXTERNAL["org_id"]),
            UUID(_EXTERNAL["stream_id"]),
            lineage.BootstrapPin(
                pin["commitment_hash"],
                pin["initial_key_id"],
                bytes.fromhex(pin["initial_public_key_hex"]),
                pin["initial_key_epoch"],
                lineage.AuditHead(boundary["latest_id"], boundary["latest_row_hash"]),
            ),
            None,
        ),
        tuple(
            bridge.BridgeWitnessPin(UUID(item["witness_id"]), item["namespace_hash"])
            for item in _NAMESPACES
        ),
        tuple(
            bridge.LegacyPublicMaterial(item["key_id"], bytes.fromhex(item["public_key_hex"]))
            for item in _EXTERNAL["legacy_keys"]
        ),
    )
    readers = tuple(
        collection.RequiredHistoryWitness(
            UUID(item["witness_id"]),
            ExplicitHistoryReader(
                item["namespace"]["endpoint"],
                item["namespace"]["bucket"],
                item["namespace"]["region"],
                "fixture-access",
                "fixture-secret",
            ),
        )
        for item in _NAMESPACES
    )
    return dict(
        enrollment=enrollment, root_body=None, pages=(), readers=readers, limits=_limits(module)
    )


def _replace(value: Any, path: tuple[str | int, ...], replacement: Any) -> Any:
    """Replace an input field, without calculating an expected admission decision."""
    if not path:
        return replacement
    field, *rest = path
    if isinstance(field, int):
        return (
            *value[:field],
            _replace(value[field], tuple(rest), replacement),
            *value[field + 1 :],
        )
    return dataclasses.replace(
        value, **{field: _replace(getattr(value, field), tuple(rest), replacement)}
    )


@pytest.fixture(autouse=True)
def _forbid_owned_io(monkeypatch: pytest.MonkeyPatch) -> None:
    from easysynq_api.services.audit import _history_spool, isolated_raw, isolated_version_page

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("admission acquired an owned resource or attempted network I/O")

    monkeypatch.setattr(collection._CollectionOwner, "__init__", forbidden)
    monkeypatch.setattr(_history_spool._SpoolSession, "__enter__", forbidden)
    monkeypatch.setattr(tempfile, "mkdtemp", forbidden)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", forbidden)
    monkeypatch.setattr(
        isolated_version_page, "read_raw_checkpoint_version_page_isolated", forbidden
    )


@pytest.mark.parametrize("field,minimum,maximum", _BOUNDS)
@pytest.mark.parametrize("edge", ["minimum", "maximum"])
def test_each_limit_accepts_inclusive_bounds(
    field: str, minimum: int, maximum: int, edge: str
) -> None:
    module = _module()
    value = minimum if edge == "minimum" else maximum
    module._validate_limits(_limits(module, **{field: value}))


@pytest.mark.parametrize("field,minimum,maximum", _BOUNDS)
@pytest.mark.parametrize("bad", ["below", "above", True, 1.0, "1", None, _Int(1)])
def test_each_limit_rejects_wrong_types_or_overflow(
    field: str, minimum: int, maximum: int, bad: Any
) -> None:
    module = _module()
    value = minimum - 1 if bad == "below" else maximum + 1 if bad == "above" else bad
    with pytest.raises(
        module.HistoryReconciliationInputError, match=r"^invalid history reconciliation input$"
    ):
        module._validate_limits(_limits(module, **{field: value}))


@pytest.mark.parametrize("value", [65_537, 1_073_741_823])
def test_spool_limit_must_be_a_whole_sqlite_page(value: int) -> None:
    module = _module()
    with pytest.raises(module.HistoryReconciliationInputError):
        module._validate_limits(_limits(module, maximum_spool_bytes=value))


@pytest.mark.parametrize(
    "field,path,bad",
    [
        ("enrollment", (), None),
        ("enrollment", ("stream",), {}),
        ("enrollment", ("stream", "org_id"), "not-uuid"),
        ("enrollment", ("stream", "stream_id"), _UUID(_EXTERNAL["stream_id"])),
        ("enrollment", ("stream", "bootstrap"), {}),
        ("enrollment", ("stream", "bootstrap", "commitment_hash"), "f" * 63),
        ("enrollment", ("stream", "bootstrap", "initial_key_id"), "ed25519-sha256:" + "0" * 64),
        ("enrollment", ("stream", "bootstrap", "initial_public_key"), bytearray(32)),
        ("enrollment", ("stream", "bootstrap", "initial_public_key"), bytes(31)),
        ("enrollment", ("stream", "bootstrap", "initial_key_epoch"), True),
        ("enrollment", ("stream", "bootstrap", "initial_key_epoch"), -1),
        ("enrollment", ("stream", "bootstrap", "initial_key_epoch"), 2**63),
        ("enrollment", ("stream", "bootstrap", "audit_boundary"), None),
        ("enrollment", ("stream", "bootstrap", "audit_boundary", "latest_id"), 0),
        ("enrollment", ("stream", "bootstrap", "audit_boundary", "latest_id"), True),
        ("enrollment", ("stream", "bootstrap", "audit_boundary", "latest_id"), 2**63),
        ("enrollment", ("stream", "bootstrap", "audit_boundary", "latest_row_hash"), "F" * 64),
        ("enrollment", ("stream", "required_checkpoint"), {}),
        (
            "enrollment",
            ("stream", "required_checkpoint"),
            lineage.RequiredCheckpointPin("f" * 64, True),
        ),
        (
            "enrollment",
            ("stream", "required_checkpoint"),
            lineage.RequiredCheckpointPin("f" * 64, 0),
        ),
        (
            "enrollment",
            ("stream", "required_checkpoint"),
            lineage.RequiredCheckpointPin("f" * 64, 2**63),
        ),
        (
            "enrollment",
            ("stream", "required_checkpoint"),
            lineage.RequiredCheckpointPin("invalid", 1),
        ),
        ("enrollment", ("witnesses",), ()),
        ("enrollment", ("witnesses",), []),
        ("enrollment", ("witnesses", 0), object()),
        ("enrollment", ("witnesses", 0, "witness_id"), "not-uuid"),
        ("enrollment", ("witnesses", 0, "namespace_hash"), "0" * 64),
        ("enrollment", ("legacy_keys",), []),
        ("enrollment", ("legacy_keys", 0), object()),
        ("enrollment", ("legacy_keys", 0, "public_key"), _Bytes(bytes(32))),
        ("enrollment", ("legacy_keys", 0, "public_key"), bytes(31)),
        ("enrollment", ("legacy_keys", 0, "key_id"), "ed25519-sha256:" + "0" * 64),
        ("root_body", (), bytearray(b"{}")),
        ("root_body", (), _Bytes(b"{}")),
        ("pages", (), []),
        ("pages", (), _Tuple(())),
        ("pages", (), (object(),)),
        ("pages", (), (bridge.BridgePageObservation(bytearray(b"{}")),)),
        ("readers", (), ()),
        ("readers", (), []),
        ("readers", (0,), object()),
        ("readers", (0, "witness_id"), "not-uuid"),
        ("readers", (0, "reader"), {}),
        ("readers", (0, "reader", "endpoint"), "https://changed.invalid"),
        ("readers", (0, "reader", "bucket"), "changed-bucket"),
        ("readers", (0, "reader", "region"), "us-west-2"),
        ("readers", (0, "reader", "region"), ""),
        ("readers", (0, "reader", "endpoint"), "http://witness-1.invalid"),
        ("readers", (0, "reader", "access_key"), ""),
        ("readers", (0, "reader", "secret_key"), ""),
        ("limits", (), {}),
    ],
)
def test_invalid_complete_request_rejects_before_io(
    field: str, path: tuple[str | int, ...], bad: Any
) -> None:
    module = _module()
    inputs = _inputs(module)
    inputs[field] = _replace(inputs[field], path, bad)
    with pytest.raises(module.HistoryReconciliationInputError) as raised:
        module.collect_and_reconcile_checkpoint_history(**inputs)
    assert str(raised.value) == "invalid history reconciliation input"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("cancel", [False, object(), _Event()])
def test_nonexact_cancellation_is_rejected(cancel: Any) -> None:
    module = _module()
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**_inputs(module), cancel=cancel)


@pytest.mark.parametrize("field", ["readers", "witnesses", "legacy_keys"])
@pytest.mark.parametrize("mode", ["duplicate", "excess"])
def test_external_inventory_rejects_duplicates_and_excess(field: str, mode: str) -> None:
    module = _module()
    inputs = _inputs(module)
    original = inputs["readers"] if field == "readers" else getattr(inputs["enrollment"], field)
    items = (original[0], original[0]) if mode == "duplicate" else original * 5
    if field == "readers":
        inputs["readers"] = items
    else:
        inputs["enrollment"] = dataclasses.replace(inputs["enrollment"], **{field: items})
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**inputs)


@pytest.mark.parametrize("missing", [0, 1])
def test_missing_required_reader_cannot_shrink_inventory(missing: int) -> None:
    module = _module()
    inputs = _inputs(module)
    inputs["readers"] = (inputs["readers"][1 - missing],)
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**inputs)


def test_valid_admission_sorts_the_complete_reader_set_without_modifying_inputs() -> None:
    module = _module()
    inputs = _inputs(module)
    original = inputs["readers"]
    inputs["readers"] = tuple(reversed(original))
    assert module._admit_reconciliation(**inputs, cancel=threading.Event()) == original
    assert inputs["readers"] == tuple(reversed(original))
    assert "fixture-secret" not in repr(inputs["readers"])


def test_large_package_admission_does_not_inherit_old_evaluator_capacities() -> None:
    module = _module()
    inputs = _inputs(module)
    inputs["pages"] = (bridge.BridgePageObservation(b"{}"),) * 1024
    inputs["limits"] = _limits(module, maximum_bridge_pages=1024, maximum_manifest_entries=100_000)
    assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


def test_page_count_overflow_precedes_element_inspection() -> None:
    module = _module()
    inputs = _inputs(module)
    inputs["pages"] = (object(),) * 17
    with pytest.raises(module.HistoryReconciliationError) as raised:
        module.collect_and_reconcile_checkpoint_history(**inputs)
    assert raised.value.code == "RESOURCE_LIMIT"


def test_page_member_validation_precedes_aggregate_bytes() -> None:
    module = _module()
    inputs = _inputs(module)
    inputs["limits"] = _limits(module, maximum_total_bytes=1)
    inputs["pages"] = (bridge.BridgePageObservation(b"oversized"), object())
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**inputs)


@pytest.mark.parametrize("budget", [6, 7])
def test_package_bytes_count_original_root_and_repeated_page_deliveries(budget: int) -> None:
    module = _module()
    inputs = _inputs(module)
    inputs.update(root_body=b"r", pages=(bridge.BridgePageObservation(b"abc"),) * 2)
    inputs["limits"] = _limits(module, maximum_total_bytes=budget)
    if budget == 6:
        with pytest.raises(module.HistoryReconciliationError) as raised:
            module.collect_and_reconcile_checkpoint_history(**inputs)
        assert raised.value.code == "RESOURCE_LIMIT"
    else:
        assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


@pytest.mark.parametrize("root", [None, b"", b"malformed", bytes(262_145)])
def test_package_wire_content_is_evidence_not_argument_authority(
    root: bytes | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit import bootstrap_bridge_codec

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("pure input admission decoded untrusted package contents")

    monkeypatch.setattr(bootstrap_bridge_codec, "_decode_root", forbidden)
    monkeypatch.setattr(bootstrap_bridge_codec, "_decode_page", forbidden)
    module = _module()
    inputs = _inputs(module)
    inputs.update(root_body=root, pages=(bridge.BridgePageObservation(bytes(2_097_153)),))
    assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


@pytest.mark.parametrize("count", [0, 8])
def test_missing_or_historical_legacy_material_is_not_reinterpreted_as_v2(count: int) -> None:
    module = _module()
    inputs = _inputs(module)
    materials = tuple(
        bridge.LegacyPublicMaterial("ed25519-sha256:" + hashlib.sha256(raw).hexdigest(), raw)
        for raw in (bytes([value]) + bytes(31) for value in range(count))
    )
    inputs["enrollment"] = dataclasses.replace(inputs["enrollment"], legacy_keys=materials)
    assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


def test_initial_material_still_requires_v2_admission() -> None:
    module = _module()
    inputs = _inputs(module)
    raw = b"\x01" + bytes(31)
    pin = dataclasses.replace(
        inputs["enrollment"].stream.bootstrap,
        initial_public_key=raw,
        initial_key_id="ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    inputs["enrollment"] = _replace(inputs["enrollment"], ("stream", "bootstrap"), pin)
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**inputs)


def test_valid_public_call_cannot_fabricate_a_report_before_worker_is_implemented() -> None:
    module = _module()
    with pytest.raises(module.HistoryReconciliationError) as raised:
        module.collect_and_reconcile_checkpoint_history(**_inputs(module))
    assert raised.value.code == "RUNTIME_UNSUPPORTED"


@pytest.mark.parametrize(
    "code",
    [
        "RESOURCE_LIMIT",
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "STORAGE_FAILED",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    ],
)
def test_controlled_errors_have_a_fixed_safe_vocabulary(code: str) -> None:
    module = _module()
    error = module.HistoryReconciliationError(code)
    assert error.code == code
    assert str(error) == f"history reconciliation failed: {code}"
    assert not isinstance(module.HistoryReconciliationCancelled(), Exception)
    assert str(module.HistoryReconciliationCancelled()) == "history reconciliation cancelled"


@pytest.mark.parametrize("code", ["fixture-secret", _String("RESOURCE_LIMIT"), None, True])
def test_error_constructor_cannot_echo_arbitrary_input(code: Any) -> None:
    module = _module()
    with pytest.raises(ValueError) as raised:
        module.HistoryReconciliationError(code)
    assert str(raised.value) == "invalid history reconciliation error code"


def test_admitted_limits_cannot_be_mutated_after_validation() -> None:
    module = _module()
    limits = _limits(module)
    module._validate_limits(limits)
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.maximum_observations = 1_000_000
    assert not hasattr(limits, "__dict__")


@pytest.mark.parametrize(
    "field,path",
    [
        ("enrollment", ()),
        ("enrollment", ("stream",)),
        ("enrollment", ("stream", "bootstrap")),
        ("enrollment", ("stream", "bootstrap", "audit_boundary")),
        ("enrollment", ("witnesses", 0)),
        ("enrollment", ("legacy_keys", 0)),
        ("readers", (0,)),
        ("readers", (0, "reader")),
        ("limits", ()),
    ],
)
def test_record_subclasses_cannot_cross_exact_admission(
    field: str, path: tuple[str | int, ...]
) -> None:
    module = _module()
    inputs = _inputs(module)
    original = inputs[field]
    for part in path:
        original = original[part] if isinstance(part, int) else getattr(original, part)
    derived = type("Derived", (type(original),), {})
    replacement = derived(
        **{f.name: getattr(original, f.name) for f in dataclasses.fields(original)}
    )
    inputs[field] = _replace(inputs[field], path, replacement)
    with pytest.raises(module.HistoryReconciliationInputError):
        module.collect_and_reconcile_checkpoint_history(**inputs)


def test_required_pin_and_positive_bigint_boundary_are_external_not_package_derived() -> None:
    module = _module()
    inputs = _inputs(module)
    stream = inputs["enrollment"].stream
    pin = dataclasses.replace(
        stream.bootstrap,
        initial_key_epoch=2**63 - 1,
        audit_boundary=lineage.AuditHead(2**63 - 1, "f" * 64),
    )
    inputs["enrollment"] = dataclasses.replace(
        inputs["enrollment"],
        stream=dataclasses.replace(
            stream,
            bootstrap=pin,
            required_checkpoint=lineage.RequiredCheckpointPin("e" * 64, 2**63 - 1),
        ),
    )
    assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


@pytest.mark.parametrize("count", [1, 4])
def test_entire_external_inventory_is_admitted_at_each_cardinality_edge(count: int) -> None:
    module = _module()
    inputs = _inputs(module)
    # Multiple independent witness identities may pin the same exact namespace. The
    # collector binds identity and namespace separately and must not invent uniqueness.
    witness = inputs["enrollment"].witnesses[0]
    reader = inputs["readers"][0].reader
    identifiers = tuple(UUID(int=value) for value in range(1, count + 1))
    inputs["enrollment"] = dataclasses.replace(
        inputs["enrollment"],
        witnesses=tuple(
            bridge.BridgeWitnessPin(identity, witness.namespace_hash) for identity in identifiers
        ),
    )
    inputs["readers"] = tuple(
        collection.RequiredHistoryWitness(identity, reader) for identity in identifiers
    )
    assert module._admit_reconciliation(**inputs, cancel=None) == inputs["readers"]


def test_page_count_overflow_performs_no_key_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    inputs = _inputs(module)
    inputs["pages"] = (object(),) * 17

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("over-limit page tuple reached cryptographic admission")

    monkeypatch.setattr(bridge, "_admit", forbidden)
    with pytest.raises(module.HistoryReconciliationError) as raised:
        module.collect_and_reconcile_checkpoint_history(**inputs)
    assert raised.value.code == "RESOURCE_LIMIT"


@pytest.mark.parametrize("boundary", ["keys", "readers"])
@pytest.mark.parametrize("kind", ["unexpected", "fatal", "group", "subclass"])
def test_admission_does_not_launder_unexpected_or_fatal_identity(
    boundary: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    inputs = _inputs(module)

    class Fatal(BaseException):
        pass

    class DerivedBridgeInput(bridge.BridgeInputError):
        pass

    class DerivedCollectionInput(collection.HistoryCollectionInputError):
        pass

    error = {
        "unexpected": RuntimeError("unexpected"),
        "fatal": Fatal(),
        "group": BaseExceptionGroup("fatal group", [RuntimeError("unexpected"), Fatal()]),
        "subclass": DerivedBridgeInput() if boundary == "keys" else DerivedCollectionInput(),
    }[kind]

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    target, name = (
        (bridge, "_admit") if boundary == "keys" else (collection, "_validate_collection_inputs")
    )
    monkeypatch.setattr(target, name, fail)
    with pytest.raises(type(error)) as raised:
        module.collect_and_reconcile_checkpoint_history(**inputs)
    assert raised.value is error
