"""Explicit external-trust consumer for legacy audit chains and retained witnesses."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ...db.models.organization import Organization
from .checkpoint import (
    AuthenticatedCheckpoint,
    CheckpointComparisonUnavailable,
    WitnessHistoryResult,
    _compare_offhost_checkpoint,
    scan_offhost_history,
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
_REASON_LIMIT = 20
_BREAK_LIMIT = 20

type SessionFactory = async_sessionmaker[AsyncSession]


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationReason:
    code: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class WitnessVerificationResult:
    witness_id: uuid.UUID
    attempted: bool
    status: Literal["passed", "failed", "incomplete"]
    verified: bool
    sinks_read: int
    read_failed: bool
    attest_failures: int
    comparison_unavailable: bool
    reasons: tuple[VerificationReason, ...]
    reasons_omitted: int


@dataclasses.dataclass(frozen=True, slots=True)
class OrganizationVerificationResult:
    org_id: uuid.UUID
    present: bool | None
    verified: bool
    checked: int
    pending: int
    local_checkpoint: CheckpointStatus | None
    break_count: int
    breaks: tuple[ChainBreak, ...]
    breaks_omitted: int
    witnesses: tuple[WitnessVerificationResult, ...]
    reasons: tuple[VerificationReason, ...]
    reasons_omitted: int


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalVerificationReport:
    descriptor_id: uuid.UUID | None
    descriptor_sha256: str | None
    verified: bool
    checked: int
    pending: int
    sinks_read: int
    unenrolled_orgs_present: bool | None
    organizations: tuple[OrganizationVerificationResult, ...]
    reasons: tuple[VerificationReason, ...]
    reasons_omitted: int
    mode: str = "external-legacy-v1"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete bounded public report at one controlled boundary."""

        def reason(value: VerificationReason) -> dict[str, str]:
            return {"code": value.code, "message": value.message}

        def checkpoint(value: CheckpointStatus | None) -> dict[str, Any] | None:
            if value is None:
                return None
            return {
                "present": value.present,
                "signature_ok": value.signature_ok,
                "hash_match": value.hash_match,
                "latest_id": value.latest_id,
                "reason": value.reason,
            }

        organizations: list[dict[str, Any]] = []
        for organization in self.organizations:
            witnesses = [
                {
                    "witness_id": str(witness.witness_id),
                    "attempted": witness.attempted,
                    "status": witness.status,
                    "verified": witness.verified,
                    "sinks_read": witness.sinks_read,
                    "read_failed": witness.read_failed,
                    "attest_failures": witness.attest_failures,
                    "comparison_unavailable": witness.comparison_unavailable,
                    "reasons": [reason(item) for item in witness.reasons],
                    "reasons_omitted": witness.reasons_omitted,
                }
                for witness in organization.witnesses
            ]
            organizations.append(
                {
                    "org_id": str(organization.org_id),
                    "present": organization.present,
                    "verified": organization.verified,
                    "checked": organization.checked,
                    "pending": organization.pending,
                    "local_checkpoint": checkpoint(organization.local_checkpoint),
                    "break_count": organization.break_count,
                    "breaks": [
                        {"at_id": item.at_id, "reason": item.reason} for item in organization.breaks
                    ],
                    "breaks_omitted": organization.breaks_omitted,
                    "witnesses": witnesses,
                    "reasons": [reason(item) for item in organization.reasons],
                    "reasons_omitted": organization.reasons_omitted,
                }
            )
        return {
            "mode": self.mode,
            "descriptor_id": None if self.descriptor_id is None else str(self.descriptor_id),
            "descriptor_sha256": self.descriptor_sha256,
            "verified": self.verified,
            "checked": self.checked,
            "pending": self.pending,
            "sinks_read": self.sinks_read,
            "unenrolled_orgs_present": self.unenrolled_orgs_present,
            "organizations": organizations,
            "reasons": [reason(item) for item in self.reasons],
            "reasons_omitted": self.reasons_omitted,
        }


@dataclasses.dataclass(slots=True)
class _WitnessState:
    witness_id: uuid.UUID
    attempted: bool = False
    complete: bool = False
    status: Literal["passed", "failed", "incomplete"] = "incomplete"
    verified: bool = False
    sinks_read: int = 0
    read_failed: bool = False
    attest_failures: int = 0
    comparison_unavailable: bool = False
    reasons: list[VerificationReason] = dataclasses.field(default_factory=list)
    reasons_omitted: int = 0


@dataclasses.dataclass(slots=True)
class _OrganizationState:
    org_id: uuid.UUID
    present: bool | None = None
    checked: int = 0
    pending: int = 0
    local_checkpoint: CheckpointStatus | None = None
    break_count: int = 0
    breaks: list[ChainBreak] = dataclasses.field(default_factory=list)
    breaks_omitted: int = 0
    witnesses: list[_WitnessState] = dataclasses.field(default_factory=list)
    reasons: list[VerificationReason] = dataclasses.field(default_factory=list)
    reasons_omitted: int = 0


def _record_reason(reasons: list[VerificationReason], omitted: int, code: str, message: str) -> int:
    if len(reasons) < _REASON_LIMIT:
        reasons.append(VerificationReason(code, message))
        return omitted
    return omitted + 1


def _create_external_database(
    credentials: ExternalCredentials,
) -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(
        credentials.database_url,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=300000"},
    )
    sessions: SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


@asynccontextmanager
async def _read_only_session(sessions: SessionFactory) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = 300000"))
        yield session


async def _database_has_unenrolled(
    sessions: SessionFactory, enrolled: tuple[uuid.UUID, ...]
) -> bool:
    async with _read_only_session(sessions) as session:
        statement = select(Organization.id).where(Organization.id.not_in(enrolled)).limit(1)
        return (await session.execute(statement)).first() is not None


async def _database_org_present(sessions: SessionFactory, org_id: uuid.UUID) -> bool:
    async with _read_only_session(sessions) as session:
        statement = select(Organization.id).where(Organization.id == org_id).limit(1)
        return (await session.execute(statement)).first() is not None


async def _database_verify_chain(
    sessions: SessionFactory, organization: TrustedOrganization
) -> VerifyResult:
    async with _read_only_session(sessions) as session:
        return await verify_chain(
            session,
            organization.org_id,
            checkpoint_verifier=legacy_verifier(organization.public_keys),
        )


async def _database_compare_checkpoint(
    sessions: SessionFactory,
    org_id: uuid.UUID,
    checkpoint: AuthenticatedCheckpoint,
) -> str | None:
    async with _read_only_session(sessions) as session:
        return await _compare_offhost_checkpoint(session, org_id, checkpoint)


async def _scan_witness(
    sessions: SessionFactory | None,
    organization: TrustedOrganization,
    witness: TrustedWitness,
    credentials: ExternalCredentials,
    now: datetime.datetime,
) -> WitnessHistoryResult:
    reader = ExplicitHistoryReader(
        endpoint=witness.endpoint,
        bucket=witness.bucket,
        region=witness.region,
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
    )

    async def compare(checkpoint: AuthenticatedCheckpoint) -> str | None:
        if sessions is None:
            raise CheckpointComparisonUnavailable
        try:
            return await _database_compare_checkpoint(sessions, organization.org_id, checkpoint)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - redact DB errors and keep scanning retained versions
            raise CheckpointComparisonUnavailable from None

    return await scan_offhost_history(
        organization.org_id,
        kind=witness.kind,
        connection=None,
        reader=reader,
        verify_signature=legacy_verifier(organization.public_keys),
        compare_checkpoint=compare,
        now=now,
    )


def _apply_walk(state: _OrganizationState, walk: VerifyResult) -> None:
    state.checked = walk.checked
    state.pending = walk.pending
    state.local_checkpoint = walk.checkpoint
    state.break_count = len(walk.breaks)
    state.breaks = list(walk.breaks[:_BREAK_LIMIT])
    state.breaks_omitted = max(0, len(walk.breaks) - _BREAK_LIMIT)
    if not walk.verified:
        state.reasons_omitted = _record_reason(
            state.reasons, state.reasons_omitted, "CHAIN_INVALID", "audit chain verification failed"
        )
    checkpoint = walk.checkpoint
    if checkpoint is None or not checkpoint.present:
        state.reasons_omitted = _record_reason(
            state.reasons,
            state.reasons_omitted,
            "LOCAL_CHECKPOINT_MISSING",
            "local checkpoint is required for external verification",
        )
    elif checkpoint.signature_ok is not True or checkpoint.hash_match is not True:
        state.reasons_omitted = _record_reason(
            state.reasons,
            state.reasons_omitted,
            "LOCAL_CHECKPOINT_INVALID",
            "local checkpoint attestation failed",
        )


def _apply_scan(state: _WitnessState, scanned: WitnessHistoryResult) -> None:
    state.complete = True
    state.sinks_read = 1 if scanned.parsed_any else 0
    state.read_failed = scanned.read_failed
    state.attest_failures = 1 if scanned.attestation_failed else 0
    state.comparison_unavailable = scanned.comparison_unavailable
    state.reasons_omitted += scanned.reasons_omitted
    if scanned.comparison_unavailable:
        state.reasons_omitted = _record_reason(
            state.reasons,
            state.reasons_omitted,
            "DATABASE_UNAVAILABLE",
            "database checkpoint comparison unavailable",
        )
    for message in scanned.reasons:
        if message == "database checkpoint comparison unavailable":
            continue
        code = "WITNESS_UNAVAILABLE" if scanned.read_failed else "WITNESS_INVALID"
        state.reasons_omitted = _record_reason(state.reasons, state.reasons_omitted, code, message)
    incomplete = not scanned.scan_complete or scanned.read_failed or scanned.comparison_unavailable
    if incomplete:
        state.reasons_omitted = _record_reason(
            state.reasons,
            state.reasons_omitted,
            "CHECK_INCOMPLETE",
            "required witness verification did not complete",
        )
    if scanned.attestation_failed:
        state.status = "failed"
    elif incomplete:
        state.status = "incomplete"
    elif not scanned.parsed_any:
        state.status = "failed"
        state.reasons_omitted = _record_reason(
            state.reasons,
            state.reasons_omitted,
            "WITNESS_INVALID",
            "enrolled witness returned no checkpoint objects",
        )
    elif scanned.reasons or scanned.reasons_omitted:
        state.status = "failed"
    else:
        state.status = "passed"
        state.verified = True


def _mark_incomplete(state: _WitnessState, *, budget_expired: bool) -> None:
    state.status = "incomplete"
    state.verified = False
    state.reasons_omitted = _record_reason(
        state.reasons,
        state.reasons_omitted,
        "CHECK_INCOMPLETE",
        (
            "required witness verification did not complete within the command budget"
            if budget_expired
            else "required witness verification did not complete"
        ),
    )


def _finish_report(
    descriptor: TrustDescriptor,
    states: list[_OrganizationState],
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
            )
            for witness in state.witnesses
        )
        verified = (
            state.present is True
            and not state.reasons
            and state.reasons_omitted == 0
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
            )
        )
    organization_results = tuple(organizations)
    verified = (
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
    )


async def _verify_external(
    descriptor: TrustDescriptor, credentials: ExternalCredentials
) -> ExternalVerificationReport:
    """Verify enrolled evidence inside the controlled external-audit worker runtime."""
    states = [
        _OrganizationState(
            org_id=organization.org_id,
            witnesses=[_WitnessState(witness.witness_id) for witness in organization.witnesses],
        )
        for organization in descriptor.organizations
    ]
    root_reasons: list[VerificationReason] = []
    root_reasons_omitted = 0
    engine: AsyncEngine | None = None
    sessions: SessionFactory | None = None
    unenrolled: bool | None = None
    try:
        try:
            engine, sessions = _create_external_database(credentials)
        except Exception:  # noqa: BLE001 - redact driver/DSN errors into a controlled report
            root_reasons_omitted = _record_reason(
                root_reasons,
                root_reasons_omitted,
                "DATABASE_UNAVAILABLE",
                "external audit database is unavailable",
            )

        async with asyncio.timeout(_COMMAND_SECONDS):
            if sessions is not None:
                try:
                    unenrolled = await _database_has_unenrolled(
                        sessions, tuple(item.org_id for item in descriptor.organizations)
                    )
                    if unenrolled:
                        root_reasons_omitted = _record_reason(
                            root_reasons,
                            root_reasons_omitted,
                            "UNENROLLED_ORGANIZATIONS",
                            "database contains an organization absent from the trust descriptor",
                        )
                except Exception:  # noqa: BLE001 - redact DB detail and continue enrolled checks
                    root_reasons_omitted = _record_reason(
                        root_reasons,
                        root_reasons_omitted,
                        "DATABASE_UNAVAILABLE",
                        "organization completeness probe unavailable",
                    )

            for organization, state in zip(descriptor.organizations, states, strict=True):
                if sessions is None:
                    state.reasons_omitted = _record_reason(
                        state.reasons,
                        state.reasons_omitted,
                        "DATABASE_UNAVAILABLE",
                        "organization database checks unavailable",
                    )
                else:
                    try:
                        state.present = await _database_org_present(sessions, organization.org_id)
                    except Exception:  # noqa: BLE001 - redact DB detail and continue walk/witnesses
                        state.reasons_omitted = _record_reason(
                            state.reasons,
                            state.reasons_omitted,
                            "DATABASE_UNAVAILABLE",
                            "organization presence check unavailable",
                        )
                    if state.present is False:
                        state.reasons_omitted = _record_reason(
                            state.reasons,
                            state.reasons_omitted,
                            "MISSING_ORGANIZATION",
                            "enrolled organization is missing from the database",
                        )
                    try:
                        _apply_walk(state, await _database_verify_chain(sessions, organization))
                    except Exception:  # noqa: BLE001 - redact DB detail and continue witnesses
                        state.reasons_omitted = _record_reason(
                            state.reasons,
                            state.reasons_omitted,
                            "DATABASE_UNAVAILABLE",
                            "organization chain verification unavailable",
                        )
                        state.reasons_omitted = _record_reason(
                            state.reasons,
                            state.reasons_omitted,
                            "CHECK_INCOMPLETE",
                            "organization verification did not complete",
                        )

                for witness, witness_state in zip(
                    organization.witnesses, state.witnesses, strict=True
                ):
                    witness_state.attempted = True
                    try:
                        scanned = await _scan_witness(
                            sessions,
                            organization,
                            witness,
                            credentials,
                            datetime.datetime.now(datetime.UTC),
                        )
                    except Exception:  # noqa: BLE001 - redact provider detail and try later witnesses
                        witness_state.complete = True
                        witness_state.read_failed = True
                        witness_state.reasons_omitted = _record_reason(
                            witness_state.reasons,
                            witness_state.reasons_omitted,
                            "WITNESS_UNAVAILABLE",
                            "required witness verification failed",
                        )
                        _mark_incomplete(witness_state, budget_expired=False)
                    else:
                        _apply_scan(witness_state, scanned)
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
                    _mark_incomplete(witness_state, budget_expired=True)
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001 - record cleanup failure without leaking driver detail
                root_reasons_omitted = _record_reason(
                    root_reasons,
                    root_reasons_omitted,
                    "DATABASE_UNAVAILABLE",
                    "external audit database cleanup failed",
                )

    return _finish_report(
        descriptor,
        states,
        unenrolled=unenrolled,
        root_reasons=root_reasons,
        root_reasons_omitted=root_reasons_omitted,
    )
