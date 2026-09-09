from __future__ import annotations

import asyncio
import datetime
import uuid
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from easysynq_api.services.audit import external
from easysynq_api.services.audit.checkpoint import (
    AuthenticatedCheckpoint,
    CheckpointComparisonUnavailable,
    WitnessHistoryResult,
)
from easysynq_api.services.audit.trust import (
    ExternalCredentials,
    TrustDescriptor,
    TrustedLegacyKey,
    TrustedOrganization,
    TrustedWitness,
)
from easysynq_api.services.audit.verify import ChainBreak, CheckpointStatus, VerifyResult

pytestmark = pytest.mark.unit


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def _descriptor(*, organizations: int = 1, witnesses: int = 1) -> TrustDescriptor:
    public_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32).public_key()
    enrolled: list[TrustedOrganization] = []
    for org_index in range(organizations):
        org_id = uuid.UUID(int=1_000 + org_index)
        enrolled.append(
            TrustedOrganization(
                org_id=org_id,
                public_keys=(TrustedLegacyKey("ed25519-sha256:" + "a" * 64, public_key),),
                witnesses=tuple(
                    TrustedWitness(
                        witness_id=uuid.UUID(int=10_000 + org_index * 10 + witness_index),
                        kind="worm_bucket",
                        endpoint=f"https://witness-{org_index}-{witness_index}.example.test",
                        bucket=f"audit-{org_index}-{witness_index}",
                        region="us-central-1",
                    )
                    for witness_index in range(witnesses)
                ),
            )
        )
    return TrustDescriptor(
        descriptor_id=uuid.UUID(int=99),
        sha256="d" * 64,
        organizations=tuple(enrolled),
    )


def _credentials() -> ExternalCredentials:
    return ExternalCredentials(
        database_url="postgresql+psycopg://reader:secret@db.example.test/audit",
        access_key="explicit-access",
        secret_key="explicit-secret",
    )


def _healthy_walk() -> VerifyResult:
    return VerifyResult(
        verified=True,
        checked=3,
        pending=0,
        breaks=[],
        checkpoint=CheckpointStatus(True, True, True, 3, None),
    )


def _healthy_scan() -> WitnessHistoryResult:
    return WitnessHistoryResult(
        parsed_any=True,
        scan_complete=True,
        read_failed=False,
        attestation_failed=False,
        comparison_unavailable=False,
        reasons=[],
        reasons_omitted=0,
    )


def _install_database(
    monkeypatch: pytest.MonkeyPatch,
    *,
    present: dict[uuid.UUID, bool] | None = None,
    walk: VerifyResult | None = None,
    unenrolled: bool = False,
) -> _Engine:
    engine = _Engine()
    monkeypatch.setattr(
        external, "_create_external_database", lambda _credentials: (engine, object())
    )

    async def has_unenrolled(_sessions: object, _org_ids: tuple[uuid.UUID, ...]) -> bool:
        return unenrolled

    async def org_present(_sessions: object, org_id: uuid.UUID) -> bool:
        return True if present is None else present[org_id]

    async def verify_walk(_sessions: object, _organization: TrustedOrganization) -> VerifyResult:
        return walk or _healthy_walk()

    monkeypatch.setattr(external, "_database_has_unenrolled", has_unenrolled)
    monkeypatch.setattr(external, "_database_org_present", org_present)
    monkeypatch.setattr(external, "_database_verify_chain", verify_walk)
    return engine


async def test_scan_witness_builds_only_explicit_reader_and_maps_database_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = _descriptor().organizations[0]
    witness = organization.witnesses[0]
    captured: dict[str, Any] = {}

    async def compare_failed(*_args: Any) -> str | None:
        raise RuntimeError("reader:secret@sentinel-db.invalid")

    async def scan(org_id: uuid.UUID, **kwargs: Any) -> WitnessHistoryResult:
        captured["org_id"] = org_id
        captured.update(kwargs)
        with pytest.raises(CheckpointComparisonUnavailable):
            await kwargs["compare_checkpoint"](
                AuthenticatedCheckpoint(1, b"\x01" * 32, datetime.datetime.now(datetime.UTC))
            )
        return _healthy_scan()

    monkeypatch.setattr(external, "_database_compare_checkpoint", compare_failed)
    monkeypatch.setattr(external, "scan_offhost_history", scan)

    await external._scan_witness(
        object(), organization, witness, _credentials(), datetime.datetime.now(datetime.UTC)
    )

    reader = captured["reader"]
    assert captured["org_id"] == organization.org_id
    assert captured["connection"] is None
    assert reader.endpoint == witness.endpoint
    assert reader.bucket == witness.bucket
    assert reader.region == witness.region
    assert reader.access_key == "explicit-access"
    assert reader.secret_key == "explicit-secret"


async def test_external_verification_keeps_descriptor_order_and_missing_org_witnesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(organizations=2)
    present = {
        descriptor.organizations[0].org_id: False,
        descriptor.organizations[1].org_id: True,
    }
    engine = _install_database(monkeypatch, present=present)
    seen: list[uuid.UUID] = []
    walked: list[uuid.UUID] = []

    async def verify_walk(_sessions: object, organization: TrustedOrganization) -> VerifyResult:
        walked.append(organization.org_id)
        return _healthy_walk()

    async def scan_witness(
        _sessions: object,
        _organization: TrustedOrganization,
        witness: TrustedWitness,
        _credentials: ExternalCredentials,
        _now: datetime.datetime,
    ) -> WitnessHistoryResult:
        seen.append(witness.witness_id)
        return _healthy_scan()

    monkeypatch.setattr(external, "_scan_witness", scan_witness)
    monkeypatch.setattr(external, "_database_verify_chain", verify_walk)

    report = await external._verify_external(descriptor, _credentials())

    assert [result.org_id for result in report.organizations] == [
        organization.org_id for organization in descriptor.organizations
    ]
    assert report.organizations[0].present is False
    assert report.organizations[0].reasons[0].code == "MISSING_ORGANIZATION"
    assert seen == [
        organization.witnesses[0].witness_id for organization in descriptor.organizations
    ]
    assert walked == [organization.org_id for organization in descriptor.organizations]
    assert report.organizations[0].checked == 3
    assert report.verified is False
    assert engine.disposed is True


async def test_external_mode_requires_local_checkpoint_and_enrolled_witness_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    walk = VerifyResult(
        verified=True,
        checked=0,
        pending=0,
        breaks=[],
        checkpoint=CheckpointStatus(False, None, None, None, "no checkpoint anchored yet"),
    )
    _install_database(monkeypatch, walk=walk)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(
            0,
            result=WitnessHistoryResult(False, True, False, False, False, [], 0),
        ),
    )

    report = await external._verify_external(descriptor, _credentials())

    codes = [reason.code for reason in report.organizations[0].reasons]
    assert "LOCAL_CHECKPOINT_MISSING" in codes
    assert report.organizations[0].witnesses[0].status == "failed"
    assert report.organizations[0].witnesses[0].attempted is True
    assert report.organizations[0].witnesses[0].verified is False
    assert report.organizations[0].witnesses[0].reasons[0].code == "WITNESS_INVALID"
    assert report.verified is False


async def test_external_mode_maps_invalid_local_checkpoint_and_unenrolled_orgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    walk = VerifyResult(
        verified=False,
        checked=1,
        pending=0,
        breaks=[ChainBreak(1, "checkpoint signature invalid")],
        checkpoint=CheckpointStatus(True, False, None, 1, "checkpoint signature invalid"),
    )
    _install_database(monkeypatch, walk=walk, unenrolled=True)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(0, result=_healthy_scan()),
    )

    report = await external._verify_external(descriptor, _credentials())

    assert report.unenrolled_orgs_present is True
    assert report.reasons[0].code == "UNENROLLED_ORGANIZATIONS"
    codes = [reason.code for reason in report.organizations[0].reasons]
    assert "CHAIN_INVALID" in codes
    assert "LOCAL_CHECKPOINT_INVALID" in codes
    assert report.verified is False


async def test_database_failures_do_not_erase_independent_witness_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(organizations=1, witnesses=2)
    engine = _Engine()
    monkeypatch.setattr(
        external, "_create_external_database", lambda _credentials: (engine, object())
    )

    async def unavailable(*_args: Any) -> Any:
        raise RuntimeError("reader:password@sentinel-db.invalid")

    monkeypatch.setattr(external, "_database_has_unenrolled", unavailable)
    monkeypatch.setattr(external, "_database_org_present", unavailable)
    monkeypatch.setattr(external, "_database_verify_chain", unavailable)
    seen: list[uuid.UUID] = []

    async def scan_witness(
        _sessions: object,
        _organization: TrustedOrganization,
        witness: TrustedWitness,
        _credentials: ExternalCredentials,
        _now: datetime.datetime,
    ) -> WitnessHistoryResult:
        seen.append(witness.witness_id)
        return WitnessHistoryResult(
            True,
            True,
            False,
            False,
            True,
            ["database checkpoint comparison unavailable"],
            0,
        )

    monkeypatch.setattr(external, "_scan_witness", scan_witness)

    report = await external._verify_external(descriptor, _credentials())

    assert seen == [witness.witness_id for witness in descriptor.organizations[0].witnesses]
    assert report.verified is False
    assert report.organizations[0].present is None
    assert all(witness.status == "incomplete" for witness in report.organizations[0].witnesses)
    rendered = str(report.to_dict())
    assert "password" not in rendered
    assert "sentinel-db" not in rendered
    assert "DATABASE_UNAVAILABLE" in rendered
    assert "CHECK_INCOMPLETE" in rendered


async def test_chain_query_failure_still_attempts_every_enrolled_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(witnesses=2)
    _install_database(monkeypatch)

    async def failed_walk(*_args: Any) -> VerifyResult:
        raise RuntimeError("sentinel database detail")

    monkeypatch.setattr(external, "_database_verify_chain", failed_walk)
    seen: list[uuid.UUID] = []

    async def scan_witness(
        _sessions: object,
        _organization: TrustedOrganization,
        witness: TrustedWitness,
        _credentials: ExternalCredentials,
        _now: datetime.datetime,
    ) -> WitnessHistoryResult:
        seen.append(witness.witness_id)
        return _healthy_scan()

    monkeypatch.setattr(external, "_scan_witness", scan_witness)

    report = await external._verify_external(descriptor, _credentials())

    assert seen == [witness.witness_id for witness in descriptor.organizations[0].witnesses]
    assert [reason.code for reason in report.organizations[0].reasons] == [
        "DATABASE_UNAVAILABLE",
        "CHECK_INCOMPLETE",
    ]
    assert "sentinel database detail" not in str(report.to_dict())


async def test_presence_failure_does_not_prevent_independent_chain_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    _install_database(monkeypatch)
    walked: list[uuid.UUID] = []

    async def failed_presence(*_args: Any) -> bool:
        raise RuntimeError("sentinel presence detail")

    async def verify_walk(_sessions: object, organization: TrustedOrganization) -> VerifyResult:
        walked.append(organization.org_id)
        return _healthy_walk()

    monkeypatch.setattr(external, "_database_org_present", failed_presence)
    monkeypatch.setattr(external, "_database_verify_chain", verify_walk)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(0, result=_healthy_scan()),
    )

    report = await external._verify_external(descriptor, _credentials())

    assert walked == [descriptor.organizations[0].org_id]
    assert report.organizations[0].present is None
    assert report.organizations[0].checked == 3
    assert report.organizations[0].local_checkpoint == _healthy_walk().checkpoint
    assert [reason.code for reason in report.organizations[0].reasons] == ["DATABASE_UNAVAILABLE"]
    assert "sentinel presence detail" not in str(report.to_dict())


async def test_false_walk_without_break_objects_is_still_chain_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    _install_database(
        monkeypatch,
        walk=VerifyResult(
            verified=False,
            checked=2,
            pending=0,
            breaks=[],
            checkpoint=CheckpointStatus(True, True, True, 2, None),
        ),
    )
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(0, result=_healthy_scan()),
    )

    report = await external._verify_external(descriptor, _credentials())

    assert [reason.code for reason in report.organizations[0].reasons] == ["CHAIN_INVALID"]
    assert report.organizations[0].verified is False


async def test_witness_timeout_is_contained_and_later_witnesses_are_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(witnesses=2)
    _install_database(monkeypatch)
    seen: list[uuid.UUID] = []

    async def scan_witness(
        _sessions: object,
        _organization: TrustedOrganization,
        witness: TrustedWitness,
        _credentials: ExternalCredentials,
        _now: datetime.datetime,
    ) -> WitnessHistoryResult:
        seen.append(witness.witness_id)
        if len(seen) == 1:
            raise TimeoutError("sentinel per-witness timeout")
        return _healthy_scan()

    monkeypatch.setattr(external, "_scan_witness", scan_witness)

    report = await external._verify_external(descriptor, _credentials())

    assert seen == [witness.witness_id for witness in descriptor.organizations[0].witnesses]
    first, second = report.organizations[0].witnesses
    assert first.attempted is True
    assert first.status == "incomplete"
    assert [reason.code for reason in first.reasons] == [
        "WITNESS_UNAVAILABLE",
        "CHECK_INCOMPLETE",
    ]
    assert "command budget" not in first.reasons[-1].message
    assert second.attempted is True
    assert second.status == "passed"
    assert second.verified is True
    assert "sentinel" not in str(report.to_dict())


async def test_proven_witness_failure_remains_failed_when_a_comparison_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    _install_database(monkeypatch)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(
            0,
            result=WitnessHistoryResult(
                True,
                True,
                False,
                True,
                True,
                [
                    "off-host checkpoint signature invalid (forged/corrupt)",
                    "database checkpoint comparison unavailable",
                ],
                0,
            ),
        ),
    )

    report = await external._verify_external(descriptor, _credentials())

    witness = report.organizations[0].witnesses[0]
    assert witness.status == "failed"
    assert witness.attest_failures == 1
    assert witness.comparison_unavailable is True
    assert {reason.code for reason in witness.reasons} == {
        "DATABASE_UNAVAILABLE",
        "WITNESS_INVALID",
        "CHECK_INCOMPLETE",
    }


async def test_total_deadline_preserves_started_and_unattempted_witness_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor(witnesses=3)
    _install_database(monkeypatch)
    monkeypatch.setattr(external, "_COMMAND_SECONDS", 0.01)
    calls = 0
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def suspended(*_args: Any) -> WitnessHistoryResult:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(external, "_scan_witness", suspended)

    report = await asyncio.wait_for(
        external._verify_external(descriptor, _credentials()), timeout=1
    )

    witnesses = report.organizations[0].witnesses
    assert calls == 1
    assert started.is_set()
    assert cancelled.is_set()
    assert witnesses[0].attempted is True
    assert witnesses[0].status == "incomplete"
    assert witnesses[1].attempted is False
    assert witnesses[2].attempted is False
    assert all(witness.verified is False for witness in witnesses)
    assert all(witness.reasons[0].code == "CHECK_INCOMPLETE" for witness in witnesses)


async def test_external_cancellation_propagates_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    engine = _install_database(monkeypatch)

    async def cancelled(*_args: Any) -> WitnessHistoryResult:
        raise asyncio.CancelledError

    monkeypatch.setattr(external, "_scan_witness", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await external._verify_external(descriptor, _credentials())
    assert engine.disposed is True


async def test_report_serialization_has_exact_bounded_public_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    _install_database(monkeypatch)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(0, result=_healthy_scan()),
    )

    rendered = (await external._verify_external(descriptor, _credentials())).to_dict()

    assert rendered["verified"] is True
    assert set(rendered) == {
        "mode",
        "descriptor_id",
        "descriptor_sha256",
        "verified",
        "checked",
        "pending",
        "sinks_read",
        "unenrolled_orgs_present",
        "organizations",
        "reasons",
        "reasons_omitted",
    }
    organization = rendered["organizations"][0]
    assert set(organization) == {
        "org_id",
        "present",
        "verified",
        "checked",
        "pending",
        "local_checkpoint",
        "break_count",
        "breaks",
        "breaks_omitted",
        "witnesses",
        "reasons",
        "reasons_omitted",
    }
    assert set(organization["witnesses"][0]) == {
        "witness_id",
        "attempted",
        "status",
        "verified",
        "sinks_read",
        "read_failed",
        "attest_failures",
        "comparison_unavailable",
        "reasons",
        "reasons_omitted",
    }


async def test_report_serialization_bounds_real_chain_breaks_and_records_omissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _descriptor()
    breaks = [ChainBreak(index, f"synthetic break {index}") for index in range(25)]
    walk = VerifyResult(
        verified=False,
        checked=25,
        pending=0,
        breaks=breaks,
        checkpoint=CheckpointStatus(True, True, True, 25, None),
    )
    _install_database(monkeypatch, walk=walk)
    monkeypatch.setattr(
        external,
        "_scan_witness",
        lambda *_args: asyncio.sleep(0, result=_healthy_scan()),
    )

    report = await external._verify_external(descriptor, _credentials())
    rendered = report.to_dict()["organizations"][0]

    assert rendered["break_count"] == 25
    assert len(rendered["breaks"]) == 20
    assert rendered["breaks_omitted"] == 5
    assert rendered["verified"] is False


class _ScalarResult:
    def first(self) -> tuple[uuid.UUID] | None:
        return None


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult()


class _SessionContext(AbstractAsyncContextManager[_Session]):
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_args: Any) -> None:
        return None


async def test_unenrolled_probe_is_read_only_and_bounded_to_one_row() -> None:
    session = _Session()

    await external._database_has_unenrolled(
        lambda: _SessionContext(session), (uuid.UUID(int=1), uuid.UUID(int=2))
    )

    assert str(session.statements[0]) == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
    assert str(session.statements[1]) == "SET LOCAL statement_timeout = 300000"
    probe = session.statements[2]
    assert probe._limit_clause.value == 1
    assert "NOT IN" in str(probe)
