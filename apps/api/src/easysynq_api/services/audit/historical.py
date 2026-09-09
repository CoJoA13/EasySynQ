"""Explicit historical-target verification over enrolled legacy audit evidence."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import uuid
from collections.abc import Callable
from typing import Any, Literal, TypeGuard

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ...db.models.audit_event import AuditEvent
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from .checkpoint import (
    AuthenticatedCheckpoint,
    CheckpointComparisonUnavailable,
    WitnessHistoryResult,
    _compare_offhost_checkpoint,
    scan_offhost_history,
)
from .external import (
    ExternalVerificationReport,
    HistoricalOrganizationDetails,
    HistoricalWitnessDetails,
    OrganizationVerificationResult,
    VerificationReason,
    WitnessVerificationResult,
    _create_external_database,
    _record_reason,
)
from .sink import ExplicitHistoryReader
from .trust import (
    ExternalCredentials,
    TrustDescriptor,
    TrustedOrganization,
    TrustedWitness,
    legacy_verifier,
)
from .verify import ChainBreak, CheckpointStatus, VerifyResult, verify_chain

_COMMAND_SECONDS = 900
_BREAK_LIMIT = 20
_SUPPORTED_CANONICAL_VERSIONS = frozenset({1})


def _supported_canonical_version(value: object) -> TypeGuard[int]:
    return type(value) is int and value in _SUPPORTED_CANONICAL_VERSIONS


def _command_timeout() -> asyncio.Timeout:
    return asyncio.timeout(_COMMAND_SECONDS)


@dataclasses.dataclass(slots=True)
class _SnapshotState:
    usable: bool = False


@dataclasses.dataclass(slots=True)
class _HistoricalWitnessState:
    witness_id: uuid.UUID
    attempted: bool = False
    complete: bool = False
    scan_valid: bool = False
    status: Literal["passed", "failed", "incomplete"] = "incomplete"
    verified: bool = False
    sinks_read: int = 0
    read_failed: bool = False
    attest_failures: int = 0
    comparison_unavailable: bool = False
    applicable_checkpoints: int | None = None
    ahead_checkpoints: int | None = None
    highest_ahead_id: int | None = None
    matched_through_id: int | None = None
    reasons: list[VerificationReason] = dataclasses.field(default_factory=list)
    reasons_omitted: int = 0


@dataclasses.dataclass(slots=True)
class _HistoricalOrganizationState:
    org_id: uuid.UUID
    present: bool | None = None
    canonical_serialize_version: int | None = None
    linked_head_known: bool = False
    linked_head_id: int | None = None
    checked: int = 0
    pending: int = 0
    local_checkpoint: CheckpointStatus | None = None
    chain_and_local_valid: bool = False
    break_count: int = 0
    breaks: list[ChainBreak] = dataclasses.field(default_factory=list)
    breaks_omitted: int = 0
    covered_through_id: int | None = None
    covered_rows: int | None = None
    uncovered_linked_rows: int | None = None
    witnesses: list[_HistoricalWitnessState] = dataclasses.field(default_factory=list)
    reasons: list[VerificationReason] = dataclasses.field(default_factory=list)
    reasons_omitted: int = 0


def _reason(
    state: _HistoricalOrganizationState | _HistoricalWitnessState,
    code: str,
    message: str,
) -> None:
    state.reasons_omitted = _record_reason(state.reasons, state.reasons_omitted, code, message)


def _database_incomplete(state: _HistoricalOrganizationState, message: str) -> None:
    _reason(state, "DATABASE_UNAVAILABLE", message)
    _reason(state, "CHECK_INCOMPLETE", "organization verification did not complete")


async def _snapshot_execute(
    session: AsyncSession,
    snapshot: _SnapshotState,
    statement: Any,
) -> Any:
    if not snapshot.usable:
        raise CheckpointComparisonUnavailable
    try:
        return await session.execute(statement)
    except asyncio.CancelledError:
        raise
    except Exception:
        snapshot.usable = False
        raise


def _apply_walk(state: _HistoricalOrganizationState, walk: VerifyResult) -> None:
    state.checked = walk.checked
    state.pending = walk.pending
    state.local_checkpoint = walk.checkpoint
    state.break_count = len(walk.breaks)
    state.breaks = list(walk.breaks[:_BREAK_LIMIT])
    state.breaks_omitted = max(0, len(walk.breaks) - _BREAK_LIMIT)
    if not walk.verified:
        _reason(state, "CHAIN_INVALID", "audit chain verification failed")
    checkpoint = walk.checkpoint
    if checkpoint is None or not checkpoint.present:
        _reason(
            state,
            "LOCAL_CHECKPOINT_MISSING",
            "local checkpoint is required for external verification",
        )
    elif checkpoint.signature_ok is not True or checkpoint.hash_match is not True:
        _reason(
            state,
            "LOCAL_CHECKPOINT_INVALID",
            "local checkpoint attestation failed",
        )
    if walk.pending:
        _reason(
            state,
            "PENDING_ROWS_UNVERIFIED",
            "pending audit rows are outside the linked historical chain",
        )
    state.chain_and_local_valid = bool(
        walk.verified
        and checkpoint is not None
        and checkpoint.present
        and checkpoint.signature_ok is True
        and checkpoint.hash_match is True
    )


async def _observe_organization(
    session: AsyncSession,
    snapshot: _SnapshotState,
    organization: TrustedOrganization,
    state: _HistoricalOrganizationState,
) -> None:
    try:
        presence = await _snapshot_execute(
            session,
            snapshot,
            select(Organization.id).where(Organization.id == organization.org_id).limit(1),
        )
        state.present = presence.first() is not None
        if state.present is False:
            _reason(
                state,
                "MISSING_ORGANIZATION",
                "enrolled organization is missing from the database",
            )

        configured = await _snapshot_execute(
            session,
            snapshot,
            select(
                SystemConfig.org_id,
                SystemConfig.canonical_serialize_version,
            ).where(SystemConfig.org_id == organization.org_id),
        )
        row = configured.one_or_none()
        if row is None:
            _reason(
                state,
                "CANONICAL_VERSION_MISSING",
                "canonical serialization version is missing for the organization",
            )
        else:
            configured_version = row.canonical_serialize_version
            if type(configured_version) is int:
                state.canonical_serialize_version = configured_version
            if not _supported_canonical_version(configured_version):
                _reason(
                    state,
                    "CANONICAL_VERSION_UNSUPPORTED",
                    "canonical serialization version is unsupported",
                )

        canonical_version = state.canonical_serialize_version
        if _supported_canonical_version(canonical_version):
            walk = await verify_chain(
                session,
                organization.org_id,
                version=canonical_version,
                checkpoint_verifier=legacy_verifier(organization.public_keys),
            )
            _apply_walk(state, walk)

        linked_head = await _snapshot_execute(
            session,
            snapshot,
            select(func.max(AuditEvent.id)).where(
                AuditEvent.org_id == organization.org_id,
                AuditEvent.chained_at.is_not(None),
            ),
        )
        state.linked_head_id = linked_head.scalar_one_or_none()
        state.linked_head_known = True

        if not _supported_canonical_version(state.canonical_serialize_version):
            measured = await _snapshot_execute(
                session,
                snapshot,
                select(
                    func.count().filter(AuditEvent.chained_at.is_not(None)),
                    func.count().filter(AuditEvent.chained_at.is_(None)),
                ).where(AuditEvent.org_id == organization.org_id),
            )
            state.checked, state.pending = map(int, measured.one())
            if state.pending:
                _reason(
                    state,
                    "PENDING_ROWS_UNVERIFIED",
                    "pending audit rows are outside the linked historical chain",
                )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - collapse DB/chain failures to bounded safe reasons
        snapshot.usable = False
        _database_incomplete(state, "organization database checks unavailable")


async def _scan_witness(
    session: AsyncSession | None,
    snapshot: _SnapshotState,
    organization: TrustedOrganization,
    state: _HistoricalOrganizationState,
    witness: TrustedWitness,
    credentials: ExternalCredentials,
    partial_result_observer: Callable[[WitnessHistoryResult], None] | None = None,
) -> WitnessHistoryResult:
    reader = ExplicitHistoryReader(
        endpoint=witness.endpoint,
        bucket=witness.bucket,
        region=witness.region,
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
    )

    async def compare(checkpoint: AuthenticatedCheckpoint) -> str | None:
        if session is None or not snapshot.usable:
            raise CheckpointComparisonUnavailable
        try:
            return await _compare_offhost_checkpoint(session, organization.org_id, checkpoint)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - comparisons fail closed without DB detail
            snapshot.usable = False
            raise CheckpointComparisonUnavailable from None

    return await scan_offhost_history(
        organization.org_id,
        kind=witness.kind,
        connection=None,
        reader=reader,
        verify_signature=legacy_verifier(organization.public_keys),
        compare_checkpoint=compare,
        now=datetime.datetime.now(datetime.UTC),
        freshness_policy="historical-target",
        historical_target_id=state.linked_head_id,
        historical_target_known=state.linked_head_known,
        partial_result_observer=partial_result_observer,
    )


def _apply_scan(
    state: _HistoricalWitnessState,
    scanned: WitnessHistoryResult,
    *,
    linked_head_id: int | None,
) -> None:
    state.complete = True
    state.sinks_read = 1 if scanned.parsed_any else 0
    state.read_failed = scanned.read_failed
    state.attest_failures = 1 if scanned.attestation_failed else 0
    state.comparison_unavailable = scanned.comparison_unavailable
    state.applicable_checkpoints = scanned.historical_applicable_checkpoints
    state.ahead_checkpoints = scanned.historical_ahead_checkpoints
    state.highest_ahead_id = scanned.historical_highest_ahead_id
    state.matched_through_id = scanned.historical_covered_through_id
    state.reasons_omitted += scanned.reasons_omitted
    if scanned.comparison_unavailable:
        _reason(
            state,
            "DATABASE_UNAVAILABLE",
            "database checkpoint comparison unavailable",
        )
    for message in scanned.reasons:
        if message == "database checkpoint comparison unavailable":
            continue
        code = "WITNESS_UNAVAILABLE" if scanned.read_failed else "WITNESS_INVALID"
        _reason(state, code, message)
    incomplete = not scanned.scan_complete or scanned.read_failed or scanned.comparison_unavailable
    if incomplete:
        _reason(state, "CHECK_INCOMPLETE", "required witness verification did not complete")
    state.scan_valid = bool(
        scanned.scan_complete
        and not scanned.read_failed
        and not scanned.attestation_failed
        and not scanned.comparison_unavailable
        and scanned.reasons_omitted == 0
    )
    if scanned.attestation_failed:
        state.status = "failed"
    elif incomplete:
        state.status = "incomplete"
    elif (
        linked_head_id is None
        or not state.applicable_checkpoints
        or state.matched_through_id != linked_head_id
    ):
        state.status = "incomplete"
        _reason(
            state,
            "HISTORICAL_COVERAGE_INCOMPLETE",
            "witness does not cover the historical linked head",
        )
    else:
        state.status = "passed"
        state.verified = True


def _mark_witness_incomplete(
    state: _HistoricalWitnessState,
    *,
    budget_expired: bool,
) -> None:
    state.status = "incomplete"
    state.verified = False
    _reason(
        state,
        "CHECK_INCOMPLETE",
        (
            "required witness verification did not complete within the command budget"
            if budget_expired
            else "required witness verification did not complete"
        ),
    )


async def _finish_coverage(
    session: AsyncSession | None,
    snapshot: _SnapshotState,
    state: _HistoricalOrganizationState,
) -> None:
    if not state.chain_and_local_valid:
        for witness in state.witnesses:
            witness.matched_through_id = None
            witness.verified = False
            if witness.status != "failed":
                witness.status = "incomplete"
                _reason(
                    witness,
                    "CHECK_INCOMPLETE",
                    "organization chain or local checkpoint attestation is incomplete",
                )
        return
    if not state.linked_head_known or session is None or not snapshot.usable:
        return
    if not all(witness.scan_valid for witness in state.witnesses):
        return

    matched: list[int] = [
        witness.matched_through_id
        for witness in state.witnesses
        if witness.applicable_checkpoints and witness.matched_through_id is not None
    ]
    if len(matched) != len(state.witnesses):
        state.covered_through_id = None
        state.covered_rows = 0
        state.uncovered_linked_rows = state.checked
        _reason(
            state,
            "HISTORICAL_COVERAGE_INCOMPLETE",
            "required witnesses do not cover the historical linked head",
        )
        return

    coverage = min(matched)
    state.covered_through_id = coverage
    try:
        covered = await _snapshot_execute(
            session,
            snapshot,
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.org_id == state.org_id,
                AuditEvent.chained_at.is_not(None),
                AuditEvent.id <= coverage,
            ),
        )
        covered_rows = int(covered.scalar_one())
        state.covered_rows = covered_rows
        state.uncovered_linked_rows = state.checked - covered_rows
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - coverage counting fails closed without DB detail
        state.covered_through_id = None
        state.covered_rows = None
        state.uncovered_linked_rows = None
        _database_incomplete(state, "historical coverage count unavailable")

    if state.uncovered_linked_rows:
        _reason(
            state,
            "HISTORICAL_COVERAGE_INCOMPLETE",
            "required witnesses do not cover the historical linked head",
        )


def _sanitize_uncertified_coverage(state: _HistoricalOrganizationState) -> None:
    """Remove coverage that an incomplete controller path could not certify."""
    chain_invalid = not state.chain_and_local_valid
    required_witness_gap = any(
        not witness.complete or not witness.scan_valid for witness in state.witnesses
    )
    aggregate_incomplete = (
        chain_invalid
        or required_witness_gap
        or (
            state.covered_through_id is not None
            and (state.covered_rows is None or state.uncovered_linked_rows is None)
        )
    )
    if aggregate_incomplete:
        state.covered_through_id = None
        state.covered_rows = None
        state.uncovered_linked_rows = None
    for witness in state.witnesses:
        if chain_invalid or not witness.complete or not witness.scan_valid:
            witness.matched_through_id = None
            witness.verified = False
            if witness.status != "failed":
                witness.status = "incomplete"


def _finish_report(
    descriptor: TrustDescriptor,
    states: list[_HistoricalOrganizationState],
    *,
    unenrolled: bool | None,
    root_reasons: list[VerificationReason],
    root_reasons_omitted: int,
) -> ExternalVerificationReport:
    organizations: list[OrganizationVerificationResult] = []
    for state in states:
        witnesses = tuple(
            WitnessVerificationResult(
                witness_id=witness.witness_id,
                attempted=witness.attempted,
                status=witness.status,
                verified=witness.verified,
                sinks_read=witness.sinks_read,
                read_failed=witness.read_failed,
                attest_failures=witness.attest_failures,
                comparison_unavailable=witness.comparison_unavailable,
                reasons=tuple(witness.reasons),
                reasons_omitted=witness.reasons_omitted,
                historical=HistoricalWitnessDetails(
                    applicable_checkpoints=witness.applicable_checkpoints,
                    ahead_checkpoints=witness.ahead_checkpoints,
                    highest_ahead_id=witness.highest_ahead_id,
                    covered_through_id=witness.matched_through_id,
                ),
            )
            for witness in state.witnesses
        )
        verified = bool(
            state.present is True
            and _supported_canonical_version(state.canonical_serialize_version)
            and state.chain_and_local_valid
            and state.pending == 0
            and not state.reasons
            and state.reasons_omitted == 0
            and state.covered_through_id == state.linked_head_id
            and all(witness.verified for witness in witnesses)
        )
        organizations.append(
            OrganizationVerificationResult(
                org_id=state.org_id,
                present=state.present,
                verified=verified,
                checked=state.checked,
                pending=state.pending,
                local_checkpoint=state.local_checkpoint,
                break_count=state.break_count,
                breaks=tuple(state.breaks),
                breaks_omitted=state.breaks_omitted,
                witnesses=witnesses,
                reasons=tuple(state.reasons),
                reasons_omitted=state.reasons_omitted,
                historical=HistoricalOrganizationDetails(
                    canonical_serialize_version=state.canonical_serialize_version,
                    linked_head_id=(state.linked_head_id if state.linked_head_known else None),
                    covered_through_id=state.covered_through_id,
                    covered_rows=state.covered_rows,
                    uncovered_linked_rows=state.uncovered_linked_rows,
                ),
            )
        )
    organization_results = tuple(organizations)
    verified = bool(
        unenrolled is False
        and not root_reasons
        and root_reasons_omitted == 0
        and all(organization.verified for organization in organization_results)
    )
    return ExternalVerificationReport(
        descriptor_id=descriptor.descriptor_id,
        descriptor_sha256=descriptor.sha256,
        verified=verified,
        checked=sum(organization.checked for organization in organization_results),
        pending=sum(organization.pending for organization in organization_results),
        sinks_read=sum(
            witness.sinks_read
            for organization in organization_results
            for witness in organization.witnesses
        ),
        unenrolled_orgs_present=unenrolled,
        organizations=organization_results,
        reasons=tuple(root_reasons),
        reasons_omitted=root_reasons_omitted,
        mode="external-historical-legacy-v1",
    )


async def _verify_historical_external(
    descriptor: TrustDescriptor,
    credentials: ExternalCredentials,
) -> ExternalVerificationReport:
    """Verify one closed historical target without weakening the live consumer."""
    states = [
        _HistoricalOrganizationState(
            org_id=organization.org_id,
            witnesses=[
                _HistoricalWitnessState(witness.witness_id) for witness in organization.witnesses
            ],
        )
        for organization in descriptor.organizations
    ]
    root_reasons: list[VerificationReason] = []
    root_reasons_omitted = 0
    engine: AsyncEngine | None = None
    session: AsyncSession | None = None
    snapshot = _SnapshotState()
    unenrolled: bool | None = None
    try:
        try:
            engine, sessions = _create_external_database(credentials)
            session = sessions()
        except Exception:  # noqa: BLE001 - redact engine/session construction failures
            root_reasons_omitted = _record_reason(
                root_reasons,
                root_reasons_omitted,
                "DATABASE_UNAVAILABLE",
                "external audit database is unavailable",
            )

        async with _command_timeout():
            if session is not None:
                try:
                    await session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    await session.execute(text("SET LOCAL statement_timeout = 300000"))
                    inventory = await session.execute(
                        select(Organization.id)
                        .order_by(Organization.id)
                        .limit(len(descriptor.organizations) + 1)
                    )
                    snapshot.usable = True
                    enrolled_ids = {
                        organization.org_id for organization in descriptor.organizations
                    }
                    unenrolled = any(org_id not in enrolled_ids for org_id in inventory.scalars())
                    if unenrolled:
                        root_reasons_omitted = _record_reason(
                            root_reasons,
                            root_reasons_omitted,
                            "UNENROLLED_ORGANIZATIONS",
                            "database contains an organization absent from the trust descriptor",
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - snapshot establishment fails closed
                    snapshot.usable = False
                    root_reasons_omitted = _record_reason(
                        root_reasons,
                        root_reasons_omitted,
                        "DATABASE_UNAVAILABLE",
                        "historical database snapshot is unavailable",
                    )

            for organization, state in zip(descriptor.organizations, states, strict=True):
                if session is not None and snapshot.usable:
                    await _observe_organization(session, snapshot, organization, state)
                else:
                    _database_incomplete(state, "organization database checks unavailable")

                for witness, witness_state in zip(
                    organization.witnesses, state.witnesses, strict=True
                ):
                    witness_state.attempted = True
                    partial_scan: WitnessHistoryResult | None = None

                    def observe_partial(result: WitnessHistoryResult) -> None:
                        nonlocal partial_scan
                        partial_scan = result

                    try:
                        scanned = await _scan_witness(
                            session,
                            snapshot,
                            organization,
                            state,
                            witness,
                            credentials,
                            observe_partial,
                        )
                    except asyncio.CancelledError:
                        if partial_scan is not None:
                            _apply_scan(
                                witness_state,
                                partial_scan,
                                linked_head_id=(
                                    state.linked_head_id if state.linked_head_known else None
                                ),
                            )
                        raise
                    except Exception:  # noqa: BLE001 - redact provider/runtime boundary failures
                        witness_state.complete = True
                        witness_state.read_failed = True
                        _reason(
                            witness_state,
                            "WITNESS_UNAVAILABLE",
                            "required witness verification failed",
                        )
                        _mark_witness_incomplete(witness_state, budget_expired=False)
                    else:
                        _apply_scan(
                            witness_state,
                            scanned,
                            linked_head_id=(
                                state.linked_head_id if state.linked_head_known else None
                            ),
                        )
                await _finish_coverage(session, snapshot, state)
    except TimeoutError:
        root_reasons_omitted = _record_reason(
            root_reasons,
            root_reasons_omitted,
            "CHECK_INCOMPLETE",
            "external verification command budget expired",
        )
        for state in states:
            for witness_state in state.witnesses:
                if not witness_state.complete:
                    _mark_witness_incomplete(witness_state, budget_expired=True)
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - cleanup failure must not mask report emission
                root_reasons_omitted = _record_reason(
                    root_reasons,
                    root_reasons_omitted,
                    "DATABASE_UNAVAILABLE",
                    "external audit database cleanup failed",
                )
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001 - cleanup failure must not mask report emission
                root_reasons_omitted = _record_reason(
                    root_reasons,
                    root_reasons_omitted,
                    "DATABASE_UNAVAILABLE",
                    "external audit database cleanup failed",
                )

    for state in states:
        _sanitize_uncertified_coverage(state)

    return _finish_report(
        descriptor,
        states,
        unenrolled=unenrolled,
        root_reasons=root_reasons,
        root_reasons_omitted=root_reasons_omitted,
    )
