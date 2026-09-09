"""Focused unit evidence for the explicit historical-target consumer."""

from __future__ import annotations

import asyncio
import base64
import datetime
import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from easysynq_api.services.audit import checkpoint, historical, sink
from easysynq_api.services.audit.checkpoint import WitnessHistoryResult
from easysynq_api.services.audit.trust import (
    ExternalCredentials,
    TrustDescriptor,
    TrustedLegacyKey,
    TrustedOrganization,
    TrustedWitness,
)
from easysynq_api.services.audit.verify import CheckpointStatus

pytestmark = pytest.mark.unit


def _descriptor(*, witnesses: int = 2) -> TrustDescriptor:
    public_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32).public_key()
    organization = TrustedOrganization(
        org_id=uuid.UUID(int=1_000),
        public_keys=(TrustedLegacyKey("ed25519-sha256:" + "a" * 64, public_key),),
        witnesses=tuple(
            TrustedWitness(
                witness_id=uuid.UUID(int=10_000 + index),
                kind="worm_bucket",
                endpoint=f"https://witness-{index}.example.test",
                bucket=f"audit-{index}",
                region="us-central-1",
            )
            for index in range(witnesses)
        ),
    )
    return TrustDescriptor(
        descriptor_id=uuid.UUID(int=99),
        sha256="d" * 64,
        organizations=(organization,),
    )


def _state(*, witnesses: int = 2) -> historical._HistoricalOrganizationState:
    state = historical._HistoricalOrganizationState(
        org_id=uuid.UUID(int=1_000),
        present=True,
        canonical_serialize_version=1,
        linked_head_known=True,
        linked_head_id=10,
        checked=10,
        pending=0,
        local_checkpoint=CheckpointStatus(True, True, True, 10, None),
        chain_and_local_valid=True,
        witnesses=[
            historical._HistoricalWitnessState(uuid.UUID(int=10_000 + index))
            for index in range(witnesses)
        ],
    )
    return state


def _unobserved_state(*, witnesses: int = 1) -> historical._HistoricalOrganizationState:
    return historical._HistoricalOrganizationState(
        org_id=uuid.UUID(int=1_000),
        witnesses=[
            historical._HistoricalWitnessState(uuid.UUID(int=10_000 + index))
            for index in range(witnesses)
        ],
    )


def _scan(
    covered_through_id: int | None,
    *,
    applicable: int = 1,
    ahead: int = 0,
) -> WitnessHistoryResult:
    return WitnessHistoryResult(
        parsed_any=True,
        scan_complete=True,
        read_failed=False,
        attestation_failed=False,
        comparison_unavailable=False,
        reasons=[],
        reasons_omitted=0,
        historical_applicable_checkpoints=applicable,
        historical_ahead_checkpoints=ahead,
        historical_highest_ahead_id=12 if ahead else None,
        historical_covered_through_id=covered_through_id,
    )


class _CountResult:
    def __init__(self, count: int) -> None:
        self.count = count

    def scalar_one(self) -> int:
        return self.count


class _CountSession:
    def __init__(self, count: int) -> None:
        self.count = count
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _CountResult:
        self.statements.append(statement)
        return _CountResult(self.count)


class _ControllerResult:
    def __init__(self, value: Any = None, *, values: list[Any] | None = None) -> None:
        self.value = value
        self.values = values or []

    def scalars(self) -> list[Any]:
        return self.values

    def scalar_one(self) -> Any:
        return self.value


class _ControllerSession:
    def __init__(
        self,
        *,
        inventory_failure: bool = False,
        count_failure: bool = False,
        suspend_count: bool = False,
        close_failure: bool = False,
    ) -> None:
        self.inventory_failure = inventory_failure
        self.count_failure = count_failure
        self.suspend_count = suspend_count
        self.close_failure = close_failure
        self.calls = 0
        self.closed = False

    async def execute(self, _statement: Any) -> _ControllerResult:
        self.calls += 1
        if self.calls <= 2:
            return _ControllerResult()
        if self.calls == 3:
            if self.inventory_failure:
                raise RuntimeError("synthetic inventory failure")
            return _ControllerResult(values=[uuid.UUID(int=1_000)])
        if self.suspend_count:
            await asyncio.Event().wait()
        if self.count_failure:
            raise RuntimeError("synthetic coverage count failure")
        return _ControllerResult(10)

    async def close(self) -> None:
        self.closed = True
        if self.close_failure:
            raise RuntimeError("synthetic session cleanup failure")


class _ControllerEngine:
    def __init__(self, *, dispose_failure: bool = False) -> None:
        self.dispose_failure = dispose_failure
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True
        if self.dispose_failure:
            raise RuntimeError("synthetic engine cleanup failure")


def _install_controller(
    monkeypatch: pytest.MonkeyPatch,
    session: _ControllerSession,
    *,
    chain_valid: bool = True,
    head_known: bool = True,
    dispose_failure: bool = False,
) -> _ControllerEngine:
    engine = _ControllerEngine(dispose_failure=dispose_failure)
    factories = 0

    def create(_credentials: object) -> tuple[_ControllerEngine, Any]:
        nonlocal factories
        factories += 1
        assert factories == 1
        return engine, lambda: session

    async def observe(
        supplied_session: object,
        snapshot: historical._SnapshotState,
        _organization: TrustedOrganization,
        state: historical._HistoricalOrganizationState,
    ) -> None:
        assert supplied_session is session
        assert snapshot.usable is True
        state.present = True
        state.canonical_serialize_version = 1
        state.checked = 10
        state.pending = 0
        state.local_checkpoint = CheckpointStatus(True, True, True, 10, None)
        state.chain_and_local_valid = chain_valid
        state.linked_head_known = head_known
        state.linked_head_id = 10 if head_known else None

    monkeypatch.setattr(historical, "_create_external_database", create)
    monkeypatch.setattr(historical, "_observe_organization", observe)
    return engine


def _credentials() -> ExternalCredentials:
    return ExternalCredentials(
        database_url="postgresql+psycopg://reader:secret@db.example.test/audit",
        access_key="explicit-access",
        secret_key="explicit-secret",
    )


async def test_required_witnesses_certify_their_minimum_valid_lower_prefix() -> None:
    state = _state()
    historical._apply_scan(state.witnesses[0], _scan(8), linked_head_id=10)
    historical._apply_scan(state.witnesses[1], _scan(9), linked_head_id=10)
    session = _CountSession(8)

    await historical._finish_coverage(session, historical._SnapshotState(usable=True), state)

    assert [witness.status for witness in state.witnesses] == [
        "incomplete",
        "incomplete",
    ]
    assert all(witness.scan_valid for witness in state.witnesses)
    assert state.covered_through_id == 8
    assert state.covered_rows == 8
    assert state.uncovered_linked_rows == 2
    assert len(session.statements) == 1


async def test_later_only_history_reports_zero_rows_without_claiming_coverage() -> None:
    state = _state(witnesses=1)
    historical._apply_scan(
        state.witnesses[0],
        _scan(None, applicable=0, ahead=1),
        linked_head_id=10,
    )
    session = _CountSession(99)

    await historical._finish_coverage(session, historical._SnapshotState(usable=True), state)

    assert state.witnesses[0].ahead_checkpoints == 1
    assert state.witnesses[0].highest_ahead_id == 12
    assert state.covered_through_id is None
    assert state.covered_rows == 0
    assert state.uncovered_linked_rows == 10
    assert session.statements == []


def test_invalid_chain_with_later_timeout_clears_every_certification() -> None:
    state = _state()
    historical._apply_scan(state.witnesses[0], _scan(10), linked_head_id=10)
    state.chain_and_local_valid = False
    state.covered_through_id = 10
    state.covered_rows = 10
    state.uncovered_linked_rows = 0

    historical._sanitize_uncertified_coverage(state)

    assert state.covered_through_id is None
    assert state.covered_rows is None
    assert state.uncovered_linked_rows is None
    assert all(witness.matched_through_id is None for witness in state.witnesses)
    assert all(witness.verified is False for witness in state.witnesses)


def test_later_witness_timeout_clears_only_aggregate_coverage() -> None:
    state = _state()
    historical._apply_scan(state.witnesses[0], _scan(10), linked_head_id=10)
    state.covered_through_id = 10
    state.covered_rows = 10
    state.uncovered_linked_rows = 0

    historical._sanitize_uncertified_coverage(state)

    assert state.covered_through_id is None
    assert state.covered_rows is None
    assert state.uncovered_linked_rows is None
    assert state.witnesses[0].matched_through_id == 10
    assert state.witnesses[0].verified is True
    assert state.witnesses[1].matched_through_id is None
    assert state.witnesses[1].verified is False


def test_interrupted_coverage_count_cannot_leave_a_certified_id() -> None:
    state = _state(witnesses=1)
    historical._apply_scan(state.witnesses[0], _scan(10), linked_head_id=10)
    state.covered_through_id = 10

    historical._sanitize_uncertified_coverage(state)

    assert state.covered_through_id is None
    assert state.witnesses[0].matched_through_id == 10
    assert state.witnesses[0].verified is True


async def test_controller_count_failure_retains_witness_head_without_reusing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession(count_failure=True)
    engine = _install_controller(monkeypatch, session)

    async def scan(*_args: Any) -> WitnessHistoryResult:
        return _scan(10)

    monkeypatch.setattr(historical, "_scan_witness", scan)

    report = await historical._verify_historical_external(_descriptor(witnesses=1), _credentials())

    organization = report.organizations[0]
    witness = organization.witnesses[0]
    assert report.verified is False
    assert organization.verified is False
    assert organization.historical is not None
    assert organization.historical.covered_through_id is None
    assert organization.historical.covered_rows is None
    assert organization.historical.uncovered_linked_rows is None
    assert [reason.code for reason in organization.reasons] == [
        "DATABASE_UNAVAILABLE",
        "CHECK_INCOMPLETE",
    ]
    assert witness.verified is True
    assert witness.historical is not None
    assert witness.historical.covered_through_id == 10
    assert session.calls == 4
    assert session.closed is True
    assert engine.disposed is True


async def test_controller_unknown_head_cannot_pass_empty_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession()
    engine = _install_controller(monkeypatch, session, head_known=False)

    async def empty_scan(*_args: Any) -> WitnessHistoryResult:
        return WitnessHistoryResult(
            parsed_any=False,
            scan_complete=True,
            read_failed=False,
            attestation_failed=False,
            comparison_unavailable=False,
            reasons=[],
            reasons_omitted=0,
        )

    monkeypatch.setattr(historical, "_scan_witness", empty_scan)

    report = await historical._verify_historical_external(_descriptor(witnesses=1), _credentials())

    witness = report.organizations[0].witnesses[0]
    assert report.verified is False
    assert witness.status == "incomplete"
    assert witness.verified is False
    assert witness.sinks_read == 0
    assert witness.historical is not None
    assert witness.historical.applicable_checkpoints is None
    assert witness.historical.covered_through_id is None
    assert session.calls == 3
    assert session.closed is True
    assert engine.disposed is True


async def test_controller_snapshot_failure_continues_witnesses_without_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession(inventory_failure=True)
    engine = _ControllerEngine()
    factories = 0

    def create(_credentials: object) -> tuple[_ControllerEngine, Any]:
        nonlocal factories
        factories += 1
        return engine, lambda: session

    scanned: list[uuid.UUID] = []

    async def scan(
        supplied_session: object,
        snapshot: historical._SnapshotState,
        _organization: TrustedOrganization,
        _state: historical._HistoricalOrganizationState,
        witness: TrustedWitness,
        _credentials: ExternalCredentials,
        _observer: object,
    ) -> WitnessHistoryResult:
        assert supplied_session is session
        assert snapshot.usable is False
        scanned.append(witness.witness_id)
        return WitnessHistoryResult(
            parsed_any=True,
            scan_complete=True,
            read_failed=False,
            attestation_failed=False,
            comparison_unavailable=True,
            reasons=["database checkpoint comparison unavailable"],
            reasons_omitted=0,
        )

    monkeypatch.setattr(historical, "_create_external_database", create)
    monkeypatch.setattr(historical, "_scan_witness", scan)

    descriptor = _descriptor(witnesses=2)
    report = await historical._verify_historical_external(descriptor, _credentials())

    assert factories == 1
    assert session.calls == 3
    assert session.closed is True
    assert engine.disposed is True
    assert scanned == [witness.witness_id for witness in descriptor.organizations[0].witnesses]
    assert report.unenrolled_orgs_present is None
    for witness in report.organizations[0].witnesses:
        assert witness.status == "incomplete"
        assert witness.comparison_unavailable is True
        assert witness.historical is not None
        assert witness.historical.applicable_checkpoints is None
        assert witness.historical.covered_through_id is None


@pytest.mark.parametrize(
    ("chain_valid", "expected_first_head"),
    [(True, 10), (False, None)],
)
async def test_controller_later_witness_timeout_obeys_chain_certification(
    monkeypatch: pytest.MonkeyPatch,
    chain_valid: bool,
    expected_first_head: int | None,
) -> None:
    session = _ControllerSession()
    engine = _install_controller(monkeypatch, session, chain_valid=chain_valid)
    monkeypatch.setattr(historical, "_COMMAND_SECONDS", 0.01)
    calls = 0

    async def scan(*_args: Any) -> WitnessHistoryResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _scan(10)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(historical, "_scan_witness", scan)

    report = await historical._verify_historical_external(_descriptor(witnesses=2), _credentials())

    first, second = report.organizations[0].witnesses
    assert report.verified is False
    assert first.historical is not None
    assert first.historical.covered_through_id == expected_first_head
    assert first.verified is (chain_valid is True)
    assert second.status == "incomplete"
    assert second.verified is False
    assert report.organizations[0].historical is not None
    assert report.organizations[0].historical.covered_through_id is None
    assert session.closed is True
    assert engine.disposed is True


async def test_controller_aggregate_count_timeout_keeps_valid_witness_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession(suspend_count=True)
    engine = _install_controller(monkeypatch, session)
    monkeypatch.setattr(historical, "_COMMAND_SECONDS", 0.01)

    async def scan(*_args: Any) -> WitnessHistoryResult:
        return _scan(10)

    monkeypatch.setattr(historical, "_scan_witness", scan)

    report = await historical._verify_historical_external(_descriptor(witnesses=1), _credentials())

    organization = report.organizations[0]
    witness = organization.witnesses[0]
    assert report.verified is False
    assert organization.historical is not None
    assert organization.historical.covered_through_id is None
    assert organization.historical.covered_rows is None
    assert witness.verified is True
    assert witness.historical is not None
    assert witness.historical.covered_through_id == 10
    assert session.calls == 4
    assert session.closed is True
    assert engine.disposed is True


@pytest.mark.parametrize("cleanup", ["session", "engine"])
async def test_controller_cleanup_failure_prevents_success_without_erasing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    cleanup: str,
) -> None:
    session = _ControllerSession(close_failure=cleanup == "session")
    engine = _install_controller(monkeypatch, session, dispose_failure=cleanup == "engine")

    async def scan(*_args: Any) -> WitnessHistoryResult:
        return _scan(10)

    monkeypatch.setattr(historical, "_scan_witness", scan)

    report = await historical._verify_historical_external(_descriptor(witnesses=1), _credentials())

    assert report.verified is False
    assert report.reasons[0].code == "DATABASE_UNAVAILABLE"
    assert report.organizations[0].verified is True
    witness = report.organizations[0].witnesses[0]
    assert witness.verified is True
    assert witness.historical is not None
    assert witness.historical.covered_through_id == 10
    assert session.closed is True
    assert engine.disposed is True


async def test_controller_cancellation_propagates_after_exact_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession()
    engine = _install_controller(monkeypatch, session)
    started = asyncio.Event()

    async def scan(*_args: Any) -> WitnessHistoryResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(historical, "_scan_witness", scan)
    task = asyncio.create_task(
        historical._verify_historical_external(_descriptor(witnesses=1), _credentials())
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.closed is True
    assert engine.disposed is True


async def test_controller_timeout_retains_attestation_observed_by_real_scanner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ControllerSession()
    engine = _install_controller(monkeypatch, session)
    loop = asyncio.get_running_loop()
    real_timeout = asyncio.timeout
    command_timeouts: list[asyncio.Timeout] = []

    def command_timeout() -> asyncio.Timeout:
        timeout = real_timeout(5)
        command_timeouts.append(timeout)
        return timeout

    monkeypatch.setattr(historical, "_command_timeout", command_timeout)
    refs = (
        sink.CheckpointVersionRef("checkpoints/org/10-invalid.json", "v1"),
        sink.CheckpointVersionRef("checkpoints/org/11-blocked.json", "v2"),
    )
    second_started = threading.Event()
    second_finished = threading.Event()
    release = threading.Event()
    invalid = {
        "checkpoint": {
            "org_id": str(uuid.UUID(int=1_000)),
            "latest_id": 10,
            "latest_row_hash": (b"\xaa" * 32).hex(),
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        },
        "signature": base64.b64encode(b"x" * 64).decode("ascii"),
    }

    monkeypatch.setattr(
        checkpoint,
        "list_offhost_checkpoint_versions_page",
        lambda *_args, **_kwargs: sink.CheckpointVersionsPage(refs, (), False, None, None),
    )

    def read(
        _kind: str,
        _connection: object,
        ref: sink.CheckpointVersionRef,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if ref.version_id == "v2":
            second_started.set()
            loop.call_soon_threadsafe(
                command_timeouts[0].reschedule,
                loop.time(),
            )
            try:
                release.wait(timeout=1)
            finally:
                second_finished.set()
        return invalid

    monkeypatch.setattr(checkpoint, "read_offhost_checkpoint_version", read)
    try:
        report = await historical._verify_historical_external(
            _descriptor(witnesses=1), _credentials()
        )
    finally:
        release.set()

    witness = report.organizations[0].witnesses[0]
    assert second_started.is_set()
    assert second_finished.wait(timeout=1)
    assert report.verified is False
    assert any(
        reason.code == "CHECK_INCOMPLETE"
        and reason.message == "external verification command budget expired"
        for reason in report.reasons
    )
    assert witness.status == "failed"
    assert witness.attest_failures == 1
    assert witness.verified is False
    assert any("signature invalid" in reason.message for reason in witness.reasons)
    assert any(reason.code == "CHECK_INCOMPLETE" for reason in witness.reasons)
    assert witness.historical is not None
    assert witness.historical.covered_through_id is None
    assert session.closed is True
    assert engine.disposed is True


class _ObservationResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def first(self) -> Any:
        return self.value

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value

    def one(self) -> Any:
        return self.value


class _ObservationSession:
    def __init__(self, version: object = 1) -> None:
        self.calls = 0
        self.version = version

    async def execute(self, _statement: Any) -> _ObservationResult:
        self.calls += 1
        if self.calls == 1:
            return _ObservationResult((uuid.UUID(int=1_000),))
        if self.calls == 2:
            return _ObservationResult(
                SimpleNamespace(
                    org_id=uuid.UUID(int=1_000),
                    canonical_serialize_version=self.version,
                )
            )
        if self.calls == 3:
            return _ObservationResult(10)
        if self.calls == 4:
            return _ObservationResult((10, 0))
        raise AssertionError("database observation continued after failed chain walk")


async def test_failed_chain_walk_invalidates_snapshot_before_later_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(witnesses=1)
    state = _unobserved_state()
    snapshot = historical._SnapshotState(usable=True)
    session = _ObservationSession()

    async def failed_walk(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic snapshot failure")

    monkeypatch.setattr(historical, "verify_chain", failed_walk)

    await historical._observe_organization(session, snapshot, descriptor.organizations[0], state)

    assert snapshot.usable is False
    assert session.calls == 2
    assert state.chain_and_local_valid is False
    assert [reason.code for reason in state.reasons] == [
        "DATABASE_UNAVAILABLE",
        "CHECK_INCOMPLETE",
    ]


async def test_boolean_canonical_version_is_rejected_without_entering_chain_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(witnesses=1)
    state = _unobserved_state()
    snapshot = historical._SnapshotState(usable=True)
    session = _ObservationSession(version=True)
    monkeypatch.setattr(
        historical,
        "verify_chain",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("malformed canonical version reached chain verification")
        ),
    )

    await historical._observe_organization(session, snapshot, descriptor.organizations[0], state)

    assert snapshot.usable is True
    assert state.canonical_serialize_version is None
    assert state.chain_and_local_valid is False
    assert [reason.code for reason in state.reasons] == ["CANONICAL_VERSION_UNSUPPORTED"]


def test_historical_report_adds_typed_fields_without_changing_live_shape() -> None:
    descriptor = _descriptor(witnesses=1)
    state = _state(witnesses=1)
    historical._apply_scan(state.witnesses[0], _scan(10), linked_head_id=10)
    state.covered_through_id = 10
    state.covered_rows = 10
    state.uncovered_linked_rows = 0

    rendered = historical._finish_report(
        descriptor,
        [state],
        unenrolled=False,
        root_reasons=[],
        root_reasons_omitted=0,
    ).to_dict()

    assert rendered["mode"] == "external-historical-legacy-v1"
    assert rendered["verified"] is True
    organization = rendered["organizations"][0]
    assert organization["historical"] == {
        "canonical_serialize_version": 1,
        "linked_head_id": 10,
        "covered_through_id": 10,
        "covered_rows": 10,
        "uncovered_linked_rows": 0,
    }
    assert organization["witnesses"][0]["historical"] == {
        "applicable_checkpoints": 1,
        "ahead_checkpoints": 0,
        "highest_ahead_id": None,
        "covered_through_id": 10,
    }
