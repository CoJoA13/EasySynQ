"""Closed WORM-owner requirements and protection-before-visibility boundary."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from easysynq_api.config import get_settings
from easysynq_api.domain.records.retention import PERMANENT, retention_until
from easysynq_api.services.packs.locks import lock_pack_build_shared
from easysynq_api.services.records import repository as records_repository

from .staged_identity import PromotionOutcome, PromotionResult
from .storage import apply_worm_protection, read_worm_state
from .worm import (
    VerifiedWormAssertion,
    WormObjectLocator,
    WormObjectState,
    WormRequirement,
)

__all__ = (
    "ExistingVersionProposal",
    "NewCopyProposal",
    "ProposedDocumentSource",
    "ProposedRecordEvidence",
    "ProposedSealedPack",
    "ProposedWormOwner",
    "ProtectedPromotion",
    "ResolvedDocumentAuthority",
    "ResolvedRecordEvidenceAuthority",
    "ResolvedSealedPackAuthority",
    "ResolvedWormOwnerAuthority",
    "WormOwner",
    "WormOwnerIntegrityError",
    "WormOwnerKind",
    "aggregate_requirements",
    "list_live_worm_owners",
    "owner_requirement",
    "protect_existing_owner",
    "protect_proposed_owner",
    "protect_proposed_owners",
    "reconcile_exact_version",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LAST_UTC_MILLISECOND = datetime.time(23, 59, 59, 999000, tzinfo=datetime.UTC)


class WormOwnerIntegrityError(Exception):
    """A bounded failure for an invalid or contradictory WORM-owner authority."""

    def __init__(self) -> None:
        super().__init__("invalid WORM owner state")


class WormOwnerKind(enum.StrEnum):
    DOCUMENT_VERSION = "DOCUMENT_VERSION"
    RECORD_EVIDENCE = "RECORD_EVIDENCE"
    SEALED_PACK = "SEALED_PACK"


def _valid_uuid(value: object) -> bool:
    return isinstance(value, uuid.UUID)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _validate_period(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        retention_until(datetime.date(2000, 1, 1), value)
    except ValueError:
        return False
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class WormOwner:
    kind: WormOwnerKind
    owner_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str
    basis_date: datetime.date
    duration: str
    domain_hold: bool
    permanent: bool
    worm_lock_period: str | None = None

    def __post_init__(self) -> None:
        selected_period = self.worm_lock_period or self.duration
        valid = (
            isinstance(self.kind, WormOwnerKind)
            and _valid_uuid(self.owner_id)
            and _valid_uuid(self.org_id)
            and _valid_sha256(self.blob_sha256)
            and type(self.basis_date) is datetime.date
            and _validate_period(self.duration)
            and (self.worm_lock_period is None or _validate_period(self.worm_lock_period))
            and isinstance(self.domain_hold, bool)
            and isinstance(self.permanent, bool)
            and self.permanent == (selected_period.strip().upper() == PERMANENT)
        )
        if not valid:
            raise WormOwnerIntegrityError


@dataclasses.dataclass(frozen=True, slots=True)
class ProposedRecordEvidence:
    owner_id: uuid.UUID
    record_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str

    def __post_init__(self) -> None:
        if not all(_valid_uuid(value) for value in (self.owner_id, self.record_id, self.org_id)):
            raise WormOwnerIntegrityError
        if not _valid_sha256(self.blob_sha256):
            raise WormOwnerIntegrityError


@dataclasses.dataclass(frozen=True, slots=True)
class ProposedDocumentSource:
    owner_id: uuid.UUID
    document_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str
    authority_kind: str
    authority_id: uuid.UUID

    def __post_init__(self) -> None:
        if not all(
            _valid_uuid(value)
            for value in (self.owner_id, self.document_id, self.org_id, self.authority_id)
        ):
            raise WormOwnerIntegrityError
        if not _valid_sha256(self.blob_sha256) or self.authority_kind not in {
            "POLICY",
            "INSTALLATION_MINIMUM",
        }:
            raise WormOwnerIntegrityError


@dataclasses.dataclass(frozen=True, slots=True)
class ProposedSealedPack:
    owner_id: uuid.UUID
    pack_record_id: uuid.UUID
    evidence_blob_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str

    def __post_init__(self) -> None:
        if not all(
            _valid_uuid(value)
            for value in (
                self.owner_id,
                self.pack_record_id,
                self.evidence_blob_id,
                self.org_id,
            )
        ):
            raise WormOwnerIntegrityError
        if not _valid_sha256(self.blob_sha256):
            raise WormOwnerIntegrityError


type ProposedWormOwner = ProposedRecordEvidence | ProposedDocumentSource | ProposedSealedPack


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedRecordEvidenceAuthority:
    evidence_blob_id: uuid.UUID
    record_id: uuid.UUID
    retention_policy_id: uuid.UUID


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedDocumentAuthority:
    document_version_id: uuid.UUID
    document_id: uuid.UUID
    authority_kind: str
    authority_id: uuid.UUID
    basis_date: datetime.date


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedSealedPackAuthority:
    evidence_pack_id: uuid.UUID
    pack_record_id: uuid.UUID
    evidence_blob_id: uuid.UUID
    retention_policy_id: uuid.UUID


type ResolvedWormOwnerAuthority = (
    ResolvedRecordEvidenceAuthority | ResolvedDocumentAuthority | ResolvedSealedPackAuthority
)

type _Promote = Callable[[], Awaitable[PromotionResult]]


@dataclasses.dataclass(frozen=True, slots=True)
class NewCopyProposal:
    owner: ProposedWormOwner
    target_bucket: str
    target_key: str
    promote: _Promote


@dataclasses.dataclass(frozen=True, slots=True)
class ExistingVersionProposal:
    owner: ProposedWormOwner


@dataclasses.dataclass(frozen=True, slots=True)
class ProtectedPromotion:
    promotion: PromotionResult | None
    assertion: VerifiedWormAssertion
    owner: WormOwner
    authority: ResolvedWormOwnerAuthority


def owner_requirement(owner: WormOwner) -> WormRequirement:
    """Calculate one owner's physical retention and legal-hold obligation."""
    selected_period = owner.worm_lock_period or owner.duration
    try:
        retention_date = retention_until(owner.basis_date, selected_period)
    except (OverflowError, ValueError) as exc:
        raise WormOwnerIntegrityError from exc
    retain_until = (
        None
        if retention_date is None
        else datetime.datetime.combine(retention_date, _LAST_UTC_MILLISECOND)
    )
    return WormRequirement(
        retain_until=retain_until,
        legal_hold=owner.domain_hold or owner.permanent,
    )


def aggregate_requirements(
    current: WormObjectState,
    owners: Sequence[WormOwner],
) -> WormRequirement:
    """Return the monotone maximum of current physical state and every live owner."""
    retain_until = current.retain_until
    legal_hold = current.legal_hold
    for owner in owners:
        requirement = owner_requirement(owner)
        if requirement.retain_until is not None:
            retain_until = max(retain_until, requirement.retain_until)
        legal_hold = legal_hold or requirement.legal_hold
    return WormRequirement(retain_until=retain_until, legal_hold=legal_hold)


@dataclasses.dataclass(frozen=True, slots=True)
class _LockedBlob:
    blob_sha256: str
    org_id: uuid.UUID
    bucket: str
    object_key: str
    object_version_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class _ResolvedProposal:
    owner: WormOwner
    authority: ResolvedWormOwnerAuthority


def _policy_is_permanent(duration: str, worm_lock_period: str | None) -> bool:
    return duration.strip().upper() == PERMANENT or (
        worm_lock_period is not None and worm_lock_period.strip().upper() == PERMANENT
    )


def _expected_bucket(owner: ProposedWormOwner) -> str:
    settings = get_settings()
    if isinstance(owner, ProposedDocumentSource):
        return settings.s3_bucket_documents
    return settings.s3_bucket_records


def _is_document_config_lock_refusal(exc: sa.exc.DBAPIError) -> bool:
    diagnostics = getattr(exc.orig, "diag", None)
    return (
        getattr(exc.orig, "sqlstate", None) == "P0001"
        and getattr(diagnostics, "message_primary", None) == "document_worm_config_lock_refused"
    )


def _is_blob_lock_refusal(exc: sa.exc.DBAPIError) -> bool:
    diagnostics = getattr(exc.orig, "diag", None)
    return (
        getattr(exc.orig, "sqlstate", None) == "P0001"
        and getattr(diagnostics, "message_primary", None) == "worm_blob_lock_refused"
    )


def _is_proposed_owner_liveness_refusal(exc: sa.exc.DBAPIError) -> bool:
    diagnostics = getattr(exc.orig, "diag", None)
    return (
        getattr(exc.orig, "sqlstate", None) == "P0001"
        and getattr(diagnostics, "message_primary", None) == "worm_proposed_owner_liveness_refused"
    )


async def _assert_proposed_record_live(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    record_id: uuid.UUID,
) -> None:
    try:
        async with session.begin_nested():
            await session.execute(
                sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                {"org": org_id, "record": record_id},
            )
    except sa.exc.DBAPIError as exc:
        if not _is_proposed_owner_liveness_refusal(exc):
            raise
        raise WormOwnerIntegrityError from exc


async def _lock_document_worm_config(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    config_id: uuid.UUID,
) -> RowMapping | None:
    try:
        async with session.begin_nested():
            return (
                (
                    await session.execute(
                        sa.text("SELECT * FROM easysynq_lock_document_worm_config(:org,:config)"),
                        {"org": org_id, "config": config_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
    except sa.exc.DBAPIError as exc:
        if not _is_document_config_lock_refusal(exc):
            raise
        raise WormOwnerIntegrityError from exc


async def _lock_blob(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    blob_sha256: str,
) -> _LockedBlob | None:
    try:
        async with session.begin_nested():
            result = await session.execute(
                sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                {"org": org_id, "sha": blob_sha256},
            )
    except sa.exc.DBAPIError as exc:
        if not _is_blob_lock_refusal(exc):
            raise
        raise WormOwnerIntegrityError from exc
    row = result.mappings().one_or_none()
    if row is None:
        return None
    complete = (
        row["blob_sha256"] == blob_sha256
        and row["org_id"] == org_id
        and isinstance(row["bucket"], str)
        and bool(row["bucket"].strip())
        and isinstance(row["object_key"], str)
        and bool(row["object_key"].strip())
        and isinstance(row["object_version_id"], str)
        and bool(row["object_version_id"].strip())
        and row["object_version_id"] != "null"
        and row["worm_locked"] is True
        and row["worm_enforced_mode"] == "GOVERNANCE"
        and row["worm_asserted_retain_until"] is not None
        and row["worm_asserted_at"] is not None
        and row["worm_retain_until"] is not None
        and row["worm_retention_verified_at"] is not None
        and isinstance(row["worm_legal_hold"], bool)
        and row["worm_legal_hold_verified_at"] is not None
        and row["purged_at"] is None
        and row["purge_execution_id"] is None
    )
    if not complete:
        raise WormOwnerIntegrityError
    return _LockedBlob(
        blob_sha256=row["blob_sha256"],
        org_id=row["org_id"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        object_version_id=row["object_version_id"],
    )


async def _resolve_record_proposal(
    session: AsyncSession,
    proposal: ProposedRecordEvidence,
) -> _ResolvedProposal:
    occupied = (
        await session.execute(
            sa.text("SELECT id FROM evidence_blob WHERE id=:owner"),
            {"owner": proposal.owner_id},
        )
    ).first()
    if occupied is not None:
        raise WormOwnerIntegrityError
    row = (
        (
            await session.execute(
                sa.text(
                    """
                SELECT r.id AS record_id,r.org_id,r.retention_basis_date,r.legal_hold,
                       parent.org_id AS parent_org_id,parent.kind::text AS parent_kind,
                       policy.id AS policy_id,policy.org_id AS policy_org_id,
                       policy.duration,policy.worm_lock_period
                FROM record r
                JOIN documented_information parent ON parent.id=r.id
                JOIN retention_policy policy ON policy.id=r.retention_policy_id
                WHERE r.id=:record
                FOR UPDATE OF r,parent,policy
                """
                ),
                {"record": proposal.record_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["record_id"] != proposal.record_id
        or row["org_id"] != proposal.org_id
        or row["parent_org_id"] != proposal.org_id
        or row["policy_org_id"] != proposal.org_id
        or row["parent_kind"] != "RECORD"
        or type(row["retention_basis_date"]) is not datetime.date
    ):
        raise WormOwnerIntegrityError
    await _assert_proposed_record_live(
        session,
        org_id=proposal.org_id,
        record_id=proposal.record_id,
    )
    owner = WormOwner(
        kind=WormOwnerKind.RECORD_EVIDENCE,
        owner_id=proposal.owner_id,
        org_id=proposal.org_id,
        blob_sha256=proposal.blob_sha256,
        basis_date=row["retention_basis_date"],
        duration=row["duration"],
        worm_lock_period=row["worm_lock_period"],
        domain_hold=row["legal_hold"],
        permanent=_policy_is_permanent(row["duration"], row["worm_lock_period"]),
    )
    return _ResolvedProposal(
        owner=owner,
        authority=ResolvedRecordEvidenceAuthority(
            evidence_blob_id=proposal.owner_id,
            record_id=proposal.record_id,
            retention_policy_id=row["policy_id"],
        ),
    )


async def _resolve_document_proposal(
    session: AsyncSession,
    proposal: ProposedDocumentSource,
) -> _ResolvedProposal:
    occupied = (
        await session.execute(
            sa.text("SELECT id FROM document_version WHERE id=:owner FOR UPDATE"),
            {"owner": proposal.owner_id},
        )
    ).first()
    if occupied is not None:
        raise WormOwnerIntegrityError
    document = (
        (
            await session.execute(
                sa.text(
                    """
                SELECT document.id,document.org_id,document.kind::text AS kind,
                       document.document_type_id,type.org_id AS type_org_id,
                       type.default_retention_policy_id
                FROM documented_information document
                JOIN document_type type ON type.id=document.document_type_id
                WHERE document.id=:document
                FOR UPDATE OF document,type
                """
                ),
                {"document": proposal.document_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        document is None
        or document["org_id"] != proposal.org_id
        or document["type_org_id"] != proposal.org_id
        or document["kind"] != "DOCUMENT"
    ):
        raise WormOwnerIntegrityError

    policy_id = document["default_retention_policy_id"]
    if policy_id is not None:
        authority_kind = "POLICY"
        authority = (
            (
                await session.execute(
                    sa.text(
                        "SELECT id,org_id,duration,worm_lock_period FROM retention_policy "
                        "WHERE id=:id FOR UPDATE"
                    ),
                    {"id": policy_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if authority is None or authority["org_id"] != proposal.org_id:
            raise WormOwnerIntegrityError
        duration = authority["duration"]
        worm_lock_period = authority["worm_lock_period"]
        authority_id = authority["id"]
    else:
        authority_kind = "INSTALLATION_MINIMUM"
        authority = await _lock_document_worm_config(
            session,
            org_id=proposal.org_id,
            config_id=proposal.authority_id,
        )
        if (
            authority is None
            or authority["id"] != proposal.authority_id
            or authority["org_id"] != proposal.org_id
            or not isinstance(authority["active_revision_no"], int)
            or authority["active_revision_no"] < 1
        ):
            raise WormOwnerIntegrityError
        duration = authority["active_period"]
        worm_lock_period = None
        authority_id = authority["id"]
    if proposal.authority_kind != authority_kind or proposal.authority_id != authority_id:
        raise WormOwnerIntegrityError
    basis_date = await session.scalar(
        sa.text("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date")
    )
    if type(basis_date) is not datetime.date:
        raise WormOwnerIntegrityError
    owner = WormOwner(
        kind=WormOwnerKind.DOCUMENT_VERSION,
        owner_id=proposal.owner_id,
        org_id=proposal.org_id,
        blob_sha256=proposal.blob_sha256,
        basis_date=basis_date,
        duration=duration,
        worm_lock_period=worm_lock_period,
        domain_hold=False,
        permanent=_policy_is_permanent(duration, worm_lock_period),
    )
    return _ResolvedProposal(
        owner=owner,
        authority=ResolvedDocumentAuthority(
            document_version_id=proposal.owner_id,
            document_id=proposal.document_id,
            authority_kind=authority_kind,
            authority_id=authority_id,
            basis_date=basis_date,
        ),
    )


async def _resolve_pack_proposal(
    session: AsyncSession,
    proposal: ProposedSealedPack,
) -> _ResolvedProposal:
    occupied = (
        await session.execute(
            sa.text("SELECT id FROM evidence_blob WHERE id=:edge"),
            {"edge": proposal.evidence_blob_id},
        )
    ).first()
    if occupied is not None:
        raise WormOwnerIntegrityError
    row = (
        (
            await session.execute(
                sa.text(
                    """
                SELECT pack.id AS pack_id,pack.org_id,pack.pack_record_id,
                       pack.status::text AS status,pack.zip_blob_sha256,pack.invalidated_at,
                       record.org_id AS record_org_id,record.retention_basis_date,
                       record.legal_hold,parent.org_id AS parent_org_id,
                       parent.kind::text AS parent_kind,policy.id AS policy_id,
                       policy.org_id AS policy_org_id,policy.duration,policy.worm_lock_period
                FROM evidence_pack pack
                JOIN record ON record.id=pack.pack_record_id
                JOIN documented_information parent ON parent.id=record.id
                JOIN retention_policy policy ON policy.id=record.retention_policy_id
                WHERE pack.id=:pack
                FOR UPDATE OF pack,record,parent,policy
                """
                ),
                {"pack": proposal.owner_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["pack_record_id"] != proposal.pack_record_id
        or row["org_id"] != proposal.org_id
        or row["record_org_id"] != proposal.org_id
        or row["parent_org_id"] != proposal.org_id
        or row["policy_org_id"] != proposal.org_id
        or row["parent_kind"] != "RECORD"
        or row["status"] == "UNAVAILABLE"
        or row["invalidated_at"] is not None
        or row["zip_blob_sha256"] is not None
        or type(row["retention_basis_date"]) is not datetime.date
        or not _policy_is_permanent(row["duration"], row["worm_lock_period"])
    ):
        raise WormOwnerIntegrityError
    await _assert_proposed_record_live(
        session,
        org_id=proposal.org_id,
        record_id=proposal.pack_record_id,
    )
    owner = WormOwner(
        kind=WormOwnerKind.SEALED_PACK,
        owner_id=proposal.owner_id,
        org_id=proposal.org_id,
        blob_sha256=proposal.blob_sha256,
        basis_date=row["retention_basis_date"],
        duration=row["duration"],
        worm_lock_period=row["worm_lock_period"],
        domain_hold=row["legal_hold"],
        permanent=True,
    )
    return _ResolvedProposal(
        owner=owner,
        authority=ResolvedSealedPackAuthority(
            evidence_pack_id=proposal.owner_id,
            pack_record_id=proposal.pack_record_id,
            evidence_blob_id=proposal.evidence_blob_id,
            retention_policy_id=row["policy_id"],
        ),
    )


async def _resolve_proposal(
    session: AsyncSession,
    proposal: ProposedWormOwner,
) -> _ResolvedProposal:
    if isinstance(proposal, ProposedRecordEvidence):
        return await _resolve_record_proposal(session, proposal)
    if isinstance(proposal, ProposedDocumentSource):
        return await _resolve_document_proposal(session, proposal)
    if isinstance(proposal, ProposedSealedPack):
        return await _resolve_pack_proposal(session, proposal)
    raise WormOwnerIntegrityError


def _proposal_order(proposal: NewCopyProposal | ExistingVersionProposal) -> tuple[str, str, str]:
    return (
        str(proposal.owner.org_id),
        proposal.owner.blob_sha256,
        str(proposal.owner.owner_id),
    )


def _preflight_proposals(
    proposals: Sequence[NewCopyProposal | ExistingVersionProposal],
) -> None:
    association_identities: set[tuple[type[ProposedWormOwner], uuid.UUID]] = set()
    new_copy_shas: set[str] = set()
    new_copy_destinations: set[tuple[str, str]] = set()
    for item in proposals:
        if not isinstance(item, (NewCopyProposal, ExistingVersionProposal)):
            raise WormOwnerIntegrityError
        owner = item.owner
        association_identity = (type(owner), owner.owner_id)
        if association_identity in association_identities:
            raise WormOwnerIntegrityError
        association_identities.add(association_identity)
        if isinstance(item, ExistingVersionProposal):
            continue
        destination = (item.target_bucket, item.target_key)
        if (
            item.owner.blob_sha256 in new_copy_shas
            or destination in new_copy_destinations
            or item.target_bucket != _expected_bucket(owner)
            or item.target_key != owner.blob_sha256
        ):
            raise WormOwnerIntegrityError
        new_copy_shas.add(owner.blob_sha256)
        new_copy_destinations.add(destination)


def _validate_promotion(
    item: NewCopyProposal,
    promotion: PromotionResult,
    *,
    allow_adopted_existing: bool,
) -> WormObjectLocator:
    if (
        (
            promotion.outcome is not PromotionOutcome.COPIED
            and not (
                allow_adopted_existing and promotion.outcome is PromotionOutcome.ADOPTED_EXISTING
            )
        )
        or promotion.verified_sha256 != item.owner.blob_sha256
        or promotion.target_bucket != item.target_bucket
        or promotion.target_key != item.target_key
    ):
        from .worm import WormIdentityMismatch

        raise WormIdentityMismatch
    return WormObjectLocator(
        promotion.target_bucket,
        promotion.target_key,
        promotion.target_version_id,
    )


def _require_active_root_transaction(
    session: AsyncSession,
    root_transaction: AsyncSessionTransaction,
) -> None:
    if not root_transaction.is_active or session.get_transaction() is not root_transaction:
        raise WormOwnerIntegrityError


async def _capture_root_transaction_identity(
    session: AsyncSession,
) -> tuple[AsyncSessionTransaction, int]:
    root_transaction = session.get_transaction()
    if root_transaction is None:
        raise WormOwnerIntegrityError
    _require_active_root_transaction(session, root_transaction)
    transaction_id = await session.scalar(sa.text("SELECT txid_current()"))
    _require_active_root_transaction(session, root_transaction)
    if type(transaction_id) is not int:
        raise WormOwnerIntegrityError
    return root_transaction, transaction_id


async def _require_same_root_transaction(
    session: AsyncSession,
    *,
    root_transaction: AsyncSessionTransaction,
    transaction_id: int,
) -> None:
    # This in-memory check must precede SQL so a callback cannot trigger autobegin here.
    _require_active_root_transaction(session, root_transaction)
    current_transaction_id = await session.scalar(sa.text("SELECT txid_current()"))
    _require_active_root_transaction(session, root_transaction)
    if current_transaction_id != transaction_id:
        raise WormOwnerIntegrityError


async def _record_assertion(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    blob_sha256: str,
    assertion: VerifiedWormAssertion,
) -> None:
    locator = assertion.locator
    await session.execute(
        sa.text(
            "SELECT easysynq_record_worm_assertion"
            "(:org,:sha,:bucket,:key,:version,:retain,:hold,:verified)"
        ),
        {
            "org": org_id,
            "sha": blob_sha256,
            "bucket": locator.bucket,
            "key": locator.object_key,
            "version": locator.object_version_id,
            "retain": assertion.verified.retain_until,
            "hold": assertion.verified.legal_hold,
            "verified": assertion.verified.read_at,
        },
    )


async def list_live_worm_owners(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    blob_sha256: str,
) -> list[WormOwner]:
    """Load every validated current WORM owner from the locked registry seam."""
    if not _valid_uuid(org_id) or not _valid_sha256(blob_sha256):
        raise WormOwnerIntegrityError
    try:
        rows = (
            await session.execute(
                sa.text("SELECT * FROM easysynq_lock_worm_owners(:org,:sha)"),
                {"org": org_id, "sha": blob_sha256},
            )
        ).mappings()
        return [
            WormOwner(
                kind=WormOwnerKind(row["owner_kind"]),
                owner_id=row["owner_id"],
                org_id=row["org_id"],
                blob_sha256=row["blob_sha256"],
                basis_date=row["basis_date"],
                duration=row["duration"],
                domain_hold=row["domain_hold"],
                permanent=row["permanent"],
                worm_lock_period=row["worm_lock_period"],
            )
            for row in rows
        ]
    except sa.exc.DBAPIError as exc:
        if "invalid_worm_owner_state" not in str(exc):
            raise
        raise WormOwnerIntegrityError from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise WormOwnerIntegrityError from exc


async def protect_proposed_owners(
    session: AsyncSession,
    *,
    proposals: Sequence[NewCopyProposal | ExistingVersionProposal],
) -> list[ProtectedPromotion]:
    """Protect a deterministic batch before any proposed owner becomes visible."""
    if not proposals:
        return []
    _preflight_proposals(proposals)
    indexed = list(enumerate(proposals))
    org_ids = {item.owner.org_id for _, item in indexed}
    if len(org_ids) != 1:
        raise WormOwnerIntegrityError
    org_id = next(iter(org_ids))
    await lock_pack_build_shared(session, org_id)

    new_items = [(index, item) for index, item in indexed if isinstance(item, NewCopyProposal)]
    await records_repository.lock_destination_allocations(
        session,
        ((new_item.target_bucket, new_item.target_key) for _, new_item in new_items),
    )

    locked_blobs: dict[str, _LockedBlob | None] = {}
    for blob_sha256 in sorted({item.owner.blob_sha256 for _, item in indexed}):
        locked_blobs[blob_sha256] = await _lock_blob(
            session,
            org_id=org_id,
            blob_sha256=blob_sha256,
        )
    for _index, item in indexed:
        blob = locked_blobs[item.owner.blob_sha256]
        if isinstance(item, NewCopyProposal):
            if blob is not None:
                raise WormOwnerIntegrityError
        elif (
            blob is None
            or blob.org_id != item.owner.org_id
            or blob.bucket != _expected_bucket(item.owner)
        ):
            raise WormOwnerIntegrityError

    resolved: dict[int, _ResolvedProposal] = {}
    for index, item in sorted(indexed, key=lambda pair: _proposal_order(pair[1])):
        resolved[index] = await _resolve_proposal(session, item.owner)
    root_transaction, transaction_id = await _capture_root_transaction_identity(session)

    promotions: dict[int, PromotionResult | None] = {
        index: None for index, item in indexed if isinstance(item, ExistingVersionProposal)
    }
    locators: dict[int, WormObjectLocator] = {}
    for index, item in indexed:
        if isinstance(item, ExistingVersionProposal):
            blob = locked_blobs[item.owner.blob_sha256]
            assert blob is not None  # noqa: S101 - established by the fail-closed check above
            locators[index] = WormObjectLocator(
                blob.bucket,
                blob.object_key,
                blob.object_version_id,
            )
    for index, item in sorted(new_items, key=lambda pair: _proposal_order(pair[1])):
        promotion = await item.promote()
        await _require_same_root_transaction(
            session,
            root_transaction=root_transaction,
            transaction_id=transaction_id,
        )
        promotions[index] = promotion
        locators[index] = _validate_promotion(
            item,
            promotion,
            allow_adopted_existing=locked_blobs[item.owner.blob_sha256] is None,
        )

    await _require_same_root_transaction(
        session,
        root_transaction=root_transaction,
        transaction_id=transaction_id,
    )
    await records_repository.lock_exact_worm_objects(session, locators.values())

    current_owner_cache: dict[tuple[uuid.UUID, str], list[WormOwner]] = {}
    proposed_by_blob: dict[tuple[uuid.UUID, str], list[WormOwner]] = {}
    for index, item in indexed:
        key = (item.owner.org_id, item.owner.blob_sha256)
        proposed_by_blob.setdefault(key, []).append(resolved[index].owner)

    assertions: dict[int, VerifiedWormAssertion] = {}
    protection_order = sorted(
        indexed,
        key=lambda pair: records_repository.exact_version_lock_key(locators[pair[0]]),
    )
    for index, protected_item in protection_order:
        locator = locators[index]
        current = await read_worm_state(locator)
        key = (protected_item.owner.org_id, protected_item.owner.blob_sha256)
        current_owners: list[WormOwner]
        if isinstance(protected_item, ExistingVersionProposal):
            if key not in current_owner_cache:
                current_owner_cache[key] = await list_live_worm_owners(
                    session,
                    org_id=key[0],
                    blob_sha256=key[1],
                )
            current_owners = current_owner_cache[key]
        else:
            current_owners = []
        requirement = aggregate_requirements(
            current,
            [*current_owners, *proposed_by_blob[key]],
        )
        assertions[index] = await apply_worm_protection(locator, requirement)

    for index, protected_item in protection_order:
        if isinstance(protected_item, ExistingVersionProposal):
            await _record_assertion(
                session,
                org_id=protected_item.owner.org_id,
                blob_sha256=protected_item.owner.blob_sha256,
                assertion=assertions[index],
            )

    return [
        ProtectedPromotion(
            promotion=promotions[index],
            assertion=assertions[index],
            owner=resolved[index].owner,
            authority=resolved[index].authority,
        )
        for index, _item in indexed
    ]


async def protect_proposed_owner(
    session: AsyncSession,
    *,
    owner: ProposedWormOwner,
    target_bucket: str,
    target_key: str,
    promote: _Promote,
) -> ProtectedPromotion:
    """Delegate one new-copy proposal to the batch-first boundary."""
    results = await protect_proposed_owners(
        session,
        proposals=[
            NewCopyProposal(
                owner=owner,
                target_bucket=target_bucket,
                target_key=target_key,
                promote=promote,
            )
        ],
    )
    return results[0]


async def protect_existing_owner(
    session: AsyncSession,
    *,
    owner: ProposedWormOwner,
) -> ProtectedPromotion:
    """Delegate one persisted exact-version proposal to the batch-first boundary."""
    results = await protect_proposed_owners(
        session,
        proposals=[ExistingVersionProposal(owner=owner)],
    )
    return results[0]


async def reconcile_exact_version(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    blob_sha256: str,
    locator: WormObjectLocator,
) -> VerifiedWormAssertion:
    """Repair one complete persisted exact version without listing provider versions."""
    if not _valid_uuid(org_id) or not _valid_sha256(blob_sha256):
        raise WormOwnerIntegrityError
    await lock_pack_build_shared(session, org_id)
    blob = await _lock_blob(session, org_id=org_id, blob_sha256=blob_sha256)
    if (
        blob is None
        or blob.org_id != org_id
        or blob.blob_sha256 != blob_sha256
        or (blob.bucket, blob.object_key, blob.object_version_id)
        != (locator.bucket, locator.object_key, locator.object_version_id)
    ):
        raise WormOwnerIntegrityError
    await records_repository.lock_exact_worm_objects(session, [locator])
    owners = await list_live_worm_owners(
        session,
        org_id=org_id,
        blob_sha256=blob_sha256,
    )
    current = await read_worm_state(locator)
    assertion = await apply_worm_protection(
        locator,
        aggregate_requirements(current, owners),
    )
    await _record_assertion(
        session,
        org_id=org_id,
        blob_sha256=blob_sha256,
        assertion=assertion,
    )
    return assertion
