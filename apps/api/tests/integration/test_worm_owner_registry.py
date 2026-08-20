"""Real-PostgreSQL proofs for the closed WORM-owner registry and orchestration boundary."""

from __future__ import annotations

import datetime
import importlib
import json
import uuid
from asyncio import Event, create_task, to_thread, wait_for
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from easysynq_api.services.records import repository as records_repo
from easysynq_api.services.vault import storage as vault_storage
from easysynq_api.services.vault.staged_identity import (
    PromotionOutcome,
    PromotionResult,
    StagedObjectRef,
    StagedVersionLocator,
    StagingDomain,
)
from easysynq_api.services.vault.worm import (
    VerifiedWormAssertion,
    WormIdentityMismatch,
    WormObjectLocator,
    WormObjectState,
    WormRequirement,
)
from tests.integration.test_ordinary_authority_transitions import (
    OrdinarySeed,
    _add_owner,
    _add_sealed_pack_pointer,
    _run_named_owner_update,
    _seed_ordinary_owner,
    _wait_for_named_lock,
)
from tests.integration.test_r27_authority_transitions import (
    _add_r27_evidence_owner,
    _add_terminal_historical_r27_destroy_owner,
    _seed_source_execution,
)
from tests.integration.test_r27_database_authority import _add_permanent_document_owner

_BASIS = datetime.date(2026, 1, 15)


def _subject() -> ModuleType:
    try:
        return importlib.import_module("easysynq_api.services.vault.retention")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 4 WORM retention module is absent: {exc}")


@asynccontextmanager
async def _session(
    database_authority_dsns: dict[str, str], role: str = "easysynq_app"
) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_authority_dsns[role])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _list_owners(
    database_authority_dsns: dict[str, str],
    *,
    org_id: uuid.UUID,
    blob_sha256: str,
    role: str = "easysynq_app",
) -> list[Any]:
    subject = _subject()
    async with _session(database_authority_dsns, role) as session:
        return list(
            await subject.list_live_worm_owners(
                session,
                org_id=org_id,
                blob_sha256=blob_sha256,
            )
        )


def _evidence_id(connection: sa.Connection, record_id: uuid.UUID) -> uuid.UUID:
    return connection.execute(
        sa.text("SELECT id FROM evidence_blob WHERE record_id=:record"),
        {"record": record_id},
    ).scalar_one()


def _set_record_basis(connection: sa.Connection, record_id: uuid.UUID) -> None:
    connection.execute(
        sa.text(
            "UPDATE record SET retention_basis_date=:basis,"
            "retention_basis_provisional=false WHERE id=:record"
        ),
        {"basis": _BASIS, "record": record_id},
    )


def _set_blob_domain(
    connection: sa.Connection,
    seed: OrdinarySeed,
    *,
    bucket: str = "records",
) -> OrdinarySeed:
    object_key = f"{bucket}/{seed.blob_sha256}"
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    connection.execute(
        sa.text("UPDATE blob SET bucket=:bucket,object_key=:key WHERE sha256=:sha"),
        {"bucket": bucket, "key": object_key, "sha": seed.blob_sha256},
    )
    return replace(seed, bucket=bucket, object_key=object_key)


def _assert_proposal_liveness_lock_order(
    executed_sql: list[str],
    *,
    owner_family: str,
) -> None:
    """Pin the client-visible prefix around the definer's tested ESOR/ESSH prefix."""
    statements = [" ".join(statement.lower().split()) for statement in executed_sql]
    pack_lock = next(
        index
        for index, statement in enumerate(statements)
        if "pg_advisory_xact_lock_shared(" in statement
    )
    allocation_lock = next(
        index for index, statement in enumerate(statements) if "pg_advisory_xact_lock(" in statement
    )
    blob_lock = next(
        index
        for index, statement in enumerate(statements)
        if "easysynq_lock_worm_blob" in statement
    )
    authority_marker = (
        "from record r join documented_information parent"
        if owner_family == "RECORD"
        else "from evidence_pack pack join record"
    )
    authority_lock = next(
        index
        for index, statement in enumerate(statements)
        if authority_marker in statement and "for update" in statement
    )
    liveness_calls = [
        index
        for index, statement in enumerate(statements)
        if "easysynq_assert_worm_record_live" in statement
    ]
    assert len(liveness_calls) == 1
    assert pack_lock < allocation_lock < blob_lock < authority_lock < liveness_calls[0]


def _add_disposition(
    connection: sa.Connection,
    seed: OrdinarySeed,
    record_id: uuid.UUID,
    *,
    action: str,
    exact_policy: bool = True,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    record_policy_id = connection.execute(
        sa.text("SELECT retention_policy_id FROM record WHERE id=:record"),
        {"record": record_id},
    ).scalar_one()
    connection.execute(
        sa.text(
            """
            INSERT INTO disposition_event
                (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                 is_worm_destroy,legal_basis)
            VALUES
                (:id,:org,:record,CAST(:action AS disposition_action),true,:policy,:user,
                 false,'registry liveness fixture')
            """
        ),
        {
            "id": event_id,
            "org": seed.org_id,
            "record": record_id,
            "action": action,
            "policy": record_policy_id if exact_policy else seed.policy_id,
            "user": seed.user_id,
        },
    )
    return event_id


@dataclass(frozen=True)
class OwnerMatrix:
    seed: OrdinarySeed
    evidence_ids: dict[str, uuid.UUID]


@dataclass(frozen=True)
class ProposedRecordSeed:
    seed: OrdinarySeed
    record_id: uuid.UUID
    evidence_blob_id: uuid.UUID
    blob_sha256: str


@dataclass(frozen=True)
class ProposedDocumentSeed:
    seed: OrdinarySeed
    document_id: uuid.UUID
    document_type_id: uuid.UUID
    document_version_id: uuid.UUID
    authority_kind: str
    authority_id: uuid.UUID
    blob_sha256: str


@dataclass(frozen=True)
class ProposedPackSeed:
    seed: OrdinarySeed
    evidence_pack_id: uuid.UUID
    pack_record_id: uuid.UUID
    evidence_blob_id: uuid.UUID
    retention_policy_id: uuid.UUID
    blob_sha256: str


def _seed_proposed_record(
    connection: sa.Connection,
    *,
    duration: str = "P10Y",
) -> ProposedRecordSeed:
    seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
    record_id = _add_owner(
        connection,
        seed,
        logical_hold=False,
        policy_duration=duration,
    )
    _set_record_basis(connection, record_id)
    evidence_blob_id = _evidence_id(connection, record_id)
    blob_sha256 = uuid.uuid4().hex * 2
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    connection.execute(
        sa.text("DELETE FROM evidence_blob WHERE id=:id"),
        {"id": evidence_blob_id},
    )
    return ProposedRecordSeed(
        seed=seed,
        record_id=record_id,
        evidence_blob_id=evidence_blob_id,
        blob_sha256=blob_sha256,
    )


def _add_proposed_record(
    connection: sa.Connection,
    seed: OrdinarySeed,
    *,
    blob_sha256: str,
    duration: str = "P10Y",
) -> ProposedRecordSeed:
    record_id = _add_owner(
        connection,
        seed,
        logical_hold=False,
        policy_duration=duration,
    )
    _set_record_basis(connection, record_id)
    evidence_blob_id = _evidence_id(connection, record_id)
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    connection.execute(
        sa.text("DELETE FROM evidence_blob WHERE id=:id"),
        {"id": evidence_blob_id},
    )
    return ProposedRecordSeed(
        seed=seed,
        record_id=record_id,
        evidence_blob_id=evidence_blob_id,
        blob_sha256=blob_sha256,
    )


def _proposed_record(subject: ModuleType, seeded: ProposedRecordSeed) -> Any:
    return subject.ProposedRecordEvidence(
        owner_id=seeded.evidence_blob_id,
        record_id=seeded.record_id,
        org_id=seeded.seed.org_id,
        blob_sha256=seeded.blob_sha256,
    )


def _seed_proposed_document(
    connection: sa.Connection,
    *,
    authority_kind: str,
) -> ProposedDocumentSeed:
    seed = _set_blob_domain(
        connection,
        _seed_ordinary_owner(connection),
        bucket="documents",
    )
    document_id = uuid.uuid4()
    document_version_id = uuid.uuid4()
    authority_id = uuid.uuid4()
    document_type_id = uuid.uuid4()
    blob_sha256 = uuid.uuid4().hex * 2
    if authority_kind == "POLICY":
        connection.execute(
            sa.text(
                "INSERT INTO retention_policy "
                "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                "VALUES (:id,:org,:name,'P10Y','P10Y','DESTROY')"
            ),
            {
                "id": authority_id,
                "org": seed.org_id,
                "name": f"previsible-document-{authority_id}",
            },
        )
    else:
        connection.execute(
            sa.text(
                "INSERT INTO document_worm_config (id,org_id,active_period) "
                "VALUES (:id,:org,'P10Y')"
            ),
            {"id": authority_id, "org": seed.org_id},
        )
    connection.execute(
        sa.text(
            """
            INSERT INTO document_type
                (id,org_id,code,name,document_level,is_singleton,
                 default_retention_policy_id)
            VALUES
                (:id,:org,:code,'Previsible document type','L2_PROCEDURE',false,:policy)
            """
        ),
        {
            "id": document_type_id,
            "org": seed.org_id,
            "code": f"PD-{document_type_id.hex[:12]}",
            "policy": authority_id if authority_kind == "POLICY" else None,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,document_type_id,
                 owner_user_id,current_state,is_singleton,classification,
                 acknowledgement_required,created_by)
            VALUES
                (:id,:org,:framework,'DOCUMENT',:identifier,'Previsible document',:type,
                 :user,'Draft',false,'Internal',false,:user)
            """
        ),
        {
            "id": document_id,
            "org": seed.org_id,
            "framework": seed.framework_id,
            "identifier": f"PREVISIBLE-DOC-{document_id}",
            "type": document_type_id,
            "user": seed.user_id,
        },
    )
    assert not connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM document_version WHERE id=:id)"),
        {"id": document_version_id},
    ).scalar_one()
    return ProposedDocumentSeed(
        seed=seed,
        document_id=document_id,
        document_type_id=document_type_id,
        document_version_id=document_version_id,
        authority_kind=authority_kind,
        authority_id=authority_id,
        blob_sha256=blob_sha256,
    )


def _seed_proposed_pack(connection: sa.Connection) -> ProposedPackSeed:
    seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
    pack_record_id = _add_owner(
        connection,
        seed,
        logical_hold=False,
        policy_duration="PERMANENT",
    )
    _set_record_basis(connection, pack_record_id)
    evidence_blob_id = _evidence_id(connection, pack_record_id)
    retention_policy_id = connection.execute(
        sa.text("SELECT retention_policy_id FROM record WHERE id=:id"),
        {"id": pack_record_id},
    ).scalar_one()
    evidence_pack_id = uuid.uuid4()
    blob_sha256 = uuid.uuid4().hex * 2
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    connection.execute(
        sa.text("DELETE FROM evidence_blob WHERE id=:id"),
        {"id": evidence_blob_id},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_pack
                (id,org_id,framework_id,title,scope_kind,scope_selector,status,
                 pack_record_id,created_by)
            VALUES
                (:id,:org,:framework,'Previsible pack','CLAUSE','{}'::jsonb,'DRAFT',
                 :record,:user)
            """
        ),
        {
            "id": evidence_pack_id,
            "org": seed.org_id,
            "framework": seed.framework_id,
            "record": pack_record_id,
            "user": seed.user_id,
        },
    )
    return ProposedPackSeed(
        seed=seed,
        evidence_pack_id=evidence_pack_id,
        pack_record_id=pack_record_id,
        evidence_blob_id=evidence_blob_id,
        retention_policy_id=retention_policy_id,
        blob_sha256=blob_sha256,
    )


def _add_previsible_pack_for_record(
    connection: sa.Connection,
    *,
    org_id: uuid.UUID,
    record_id: uuid.UUID,
    created_by: uuid.UUID,
) -> ProposedPackSeed:
    parent = connection.execute(
        sa.text("SELECT framework_id FROM documented_information WHERE id=:record AND org_id=:org"),
        {"record": record_id, "org": org_id},
    ).one()
    policy_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO retention_policy "
            "(id,org_id,name,duration,worm_lock_period,disposition_action) "
            "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
        ),
        {"id": policy_id, "org": org_id, "name": f"proposal-pack-{policy_id}"},
    )
    connection.execute(
        sa.text(
            "UPDATE record SET retention_policy_id=:policy,"
            "retention_basis_date=coalesce(retention_basis_date,current_date) WHERE id=:record"
        ),
        {"policy": policy_id, "record": record_id},
    )
    evidence_pack_id = uuid.uuid4()
    evidence_blob_id = uuid.uuid4()
    blob_sha256 = uuid.uuid4().hex * 2
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_pack
                (id,org_id,framework_id,title,scope_kind,scope_selector,status,
                 pack_record_id,created_by)
            VALUES
                (:id,:org,:framework,'Destroyed proposal pack','CLAUSE','{}'::jsonb,'DRAFT',
                 :record,:user)
            """
        ),
        {
            "id": evidence_pack_id,
            "org": org_id,
            "framework": parent.framework_id,
            "record": record_id,
            "user": created_by,
        },
    )
    seed = OrdinarySeed(
        org_id=org_id,
        user_id=created_by,
        framework_id=parent.framework_id,
        policy_id=policy_id,
        record_id=record_id,
        blob_sha256=blob_sha256,
        bucket="records",
        object_key=blob_sha256,
        object_version_id="unused-previsible-version",
        disposition_event_id=uuid.uuid4(),
    )
    return ProposedPackSeed(
        seed=seed,
        evidence_pack_id=evidence_pack_id,
        pack_record_id=record_id,
        evidence_blob_id=evidence_blob_id,
        retention_policy_id=policy_id,
        blob_sha256=blob_sha256,
    )


def _promotion_for_sha(
    blob_sha256: str,
    *,
    bucket: str,
    key: str,
    version: str,
) -> PromotionResult:
    source = StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=blob_sha256,
            version_id=f"staged-{uuid.uuid4()}",
        ),
        expected_sha256=blob_sha256,
        content_type="application/octet-stream",
        expected_size=1,
    )
    return PromotionResult(
        outcome=PromotionOutcome.COPIED,
        verified_sha256=blob_sha256,
        size=1,
        content_type="application/octet-stream",
        retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        source=source,
        source_etag="staged-etag",
        target_bucket=bucket,
        target_key=key,
        target_version_id=version,
    )


def _promotion(
    seeded: ProposedRecordSeed,
    *,
    bucket: str = "records",
    key: str | None = None,
    version: str = "provider-version-1",
) -> PromotionResult:
    source = StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=seeded.blob_sha256,
            version_id=f"staged-{uuid.uuid4()}",
        ),
        expected_sha256=seeded.blob_sha256,
        content_type="application/octet-stream",
        expected_size=1,
    )
    return PromotionResult(
        outcome=PromotionOutcome.COPIED,
        verified_sha256=seeded.blob_sha256,
        size=1,
        content_type="application/octet-stream",
        retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        source=source,
        source_etag="staged-etag",
        target_bucket=bucket,
        target_key=key or seeded.blob_sha256,
        target_version_id=version,
    )


def _verified(
    locator: WormObjectLocator,
    requirement: WormRequirement,
    *,
    read_at: datetime.datetime,
) -> VerifiedWormAssertion:
    retain_until = requirement.retain_until or datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
    state = WormObjectState(
        locator=locator,
        mode="GOVERNANCE",
        retain_until=retain_until,
        legal_hold=requirement.legal_hold,
        read_at=read_at,
    )
    return VerifiedWormAssertion(
        locator=locator,
        asserted_retain_until=retain_until,
        asserted_at=read_at,
        verified=state,
    )


class _ExactOnlyWormClient:
    """Provider fake that makes any version listing/latest inference an immediate failure."""

    def __init__(self, locator: WormObjectLocator) -> None:
        self.locator = locator
        self.retain_until = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        self.legal_hold = False
        self.exact_calls: list[tuple[str, dict[str, object]]] = []
        self.listing_attempted = False

    def _exact(self, operation: str, arguments: dict[str, object]) -> None:
        coordinates = {
            "Bucket": self.locator.bucket,
            "Key": self.locator.object_key,
            "VersionId": self.locator.object_version_id,
        }
        assert {key: arguments.get(key) for key in coordinates} == coordinates
        self.exact_calls.append((operation, dict(arguments)))

    def get_object_retention(self, **arguments: object) -> dict[str, object]:
        self._exact("get_object_retention", arguments)
        return {
            "Retention": {
                "Mode": "GOVERNANCE",
                "RetainUntilDate": self.retain_until,
            }
        }

    def get_object_legal_hold(self, **arguments: object) -> dict[str, object]:
        self._exact("get_object_legal_hold", arguments)
        return {"LegalHold": {"Status": "ON" if self.legal_hold else "OFF"}}

    def put_object_retention(self, **arguments: object) -> dict[str, object]:
        self._exact("put_object_retention", arguments)
        retention = arguments["Retention"]
        assert isinstance(retention, dict)
        retain_until = retention["RetainUntilDate"]
        assert isinstance(retain_until, datetime.datetime)
        self.retain_until = retain_until
        return {}

    def put_object_legal_hold(self, **arguments: object) -> dict[str, object]:
        self._exact("put_object_legal_hold", arguments)
        assert arguments["LegalHold"] == {"Status": "ON"}
        self.legal_hold = True
        return {}

    def get_paginator(self, operation: str) -> None:
        self.listing_attempted = True
        raise AssertionError(f"reconciliation attempted forbidden listing API: {operation}")

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"reconciliation attempted non-exact provider API: {name}")


def _seed_owner_matrix(database_authority_dsns: dict[str, str]) -> OwnerMatrix:
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            evidence_ids: dict[str, uuid.UUID] = {}
            shapes = {
                "p3": ("ACTIVE", "P3Y", None, True),
                "p10_disposed": ("DISPOSED", "P10Y", None, True),
                "archive": ("DISPOSED", "P4Y", "ARCHIVE_COLD", True),
                "transfer": ("DISPOSED", "P5Y", "TRANSFER", True),
                "destroyed": ("DISPOSED", "P6Y", "DESTROY", True),
                "forged_destroy": ("DISPOSED", "P7Y", "DESTROY", False),
            }
            for name, (state, duration, action, exact_policy) in shapes.items():
                record_id = _add_owner(
                    connection,
                    seed,
                    logical_hold=False,
                    policy_duration=duration,
                )
                _set_record_basis(connection, record_id)
                connection.execute(
                    sa.text("UPDATE record SET disposition_state=:state WHERE id=:record"),
                    {"state": state, "record": record_id},
                )
                evidence_ids[name] = _evidence_id(connection, record_id)
                if action is not None:
                    _add_disposition(
                        connection,
                        seed,
                        record_id,
                        action=action,
                        exact_policy=exact_policy,
                    )
            return OwnerMatrix(seed=seed, evidence_ids=evidence_ids)
    finally:
        engine.dispose()


async def test_registry_uses_exact_association_ids_and_destructive_event_liveness(
    database_authority_dsns: dict[str, str],
) -> None:
    subject = _subject()
    matrix = _seed_owner_matrix(database_authority_dsns)

    owners = await _list_owners(
        database_authority_dsns,
        org_id=matrix.seed.org_id,
        blob_sha256=matrix.seed.blob_sha256,
    )

    expected = {
        matrix.evidence_ids[name]
        for name in ("p3", "p10_disposed", "archive", "transfer", "forged_destroy")
    }
    assert {owner.owner_id for owner in owners} == expected
    assert {owner.kind for owner in owners} == {subject.WormOwnerKind.RECORD_EVIDENCE}
    assert matrix.evidence_ids["destroyed"] not in expected

    current = WormObjectState(
        locator=WormObjectLocator(
            matrix.seed.bucket,
            matrix.seed.object_key,
            matrix.seed.object_version_id,
        ),
        mode="GOVERNANCE",
        retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        legal_hold=False,
        read_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    requirement = subject.aggregate_requirements(current, owners)
    assert requirement.retain_until == datetime.datetime(
        2036, 1, 15, 23, 59, 59, 999000, tzinfo=datetime.UTC
    )


@pytest.mark.parametrize("terminal_state", ("EXECUTED", "FAILED"))
async def test_valid_r27_destroy_remains_historical_after_terminal_execution_state(
    database_authority_dsns: dict[str, str], terminal_state: str
) -> None:
    _subject()
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text("UPDATE r27_execution SET state=:state WHERE id=:id"),
                {"state": terminal_state, "id": source.internal_execution_id},
            )
        owners = await _list_owners(
            database_authority_dsns,
            org_id=source.actors.org_id,
            blob_sha256=physical.sha256,
        )
        assert owners == []
    finally:
        owner.dispose()


@pytest.mark.parametrize(
    "authority_mismatch",
    (
        "REQUEST_BINDING",
        "EXECUTION_BINDING",
        "REQUESTED_ACTOR",
        "APPROVED_ACTOR",
        "LEGAL_BASIS",
        "SOURCE_COMMITTED_AT",
    ),
)
async def test_historical_r27_destroy_requires_every_immutable_authority_binding(
    database_authority_dsns: dict[str, str], authority_mismatch: str
) -> None:
    _subject()
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            evidence_id = connection.execute(
                sa.text(
                    "SELECT id FROM evidence_blob WHERE record_id=:record AND blob_sha256=:sha"
                ),
                {"record": source.actors.record_id, "sha": physical.sha256},
            ).scalar_one()
            _set_record_basis(connection, source.actors.record_id)
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if authority_mismatch == "REQUEST_BINDING":
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_request_id=:other WHERE id=:id"),
                    {"other": uuid.uuid4(), "id": source.disposition_event_id},
                )
            elif authority_mismatch == "EXECUTION_BINDING":
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_execution_id=:other WHERE id=:id"),
                    {"other": uuid.uuid4(), "id": source.disposition_event_id},
                )
            elif authority_mismatch == "REQUESTED_ACTOR":
                connection.execute(
                    sa.text("UPDATE disposition_event SET requested_by=:other WHERE id=:id"),
                    {"other": source.actors.canceller_id, "id": source.disposition_event_id},
                )
            elif authority_mismatch == "APPROVED_ACTOR":
                connection.execute(
                    sa.text("UPDATE disposition_event SET approved_by=:other WHERE id=:id"),
                    {"other": source.actors.canceller_id, "id": source.disposition_event_id},
                )
            elif authority_mismatch == "LEGAL_BASIS":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET legal_basis='mismatched legal basis' "
                        "WHERE id=:id"
                    ),
                    {"id": source.disposition_event_id},
                )
            else:
                connection.execute(
                    sa.text("UPDATE r27_execution SET source_committed_at=NULL WHERE id=:id"),
                    {"id": source.internal_execution_id},
                )

        owners = await _list_owners(
            database_authority_dsns,
            org_id=source.actors.org_id,
            blob_sha256=physical.sha256,
        )
        assert {worm_owner.owner_id for worm_owner in owners} == {evidence_id}
    finally:
        owner.dispose()


@pytest.mark.parametrize("terminal_state", ("EXECUTED", "FAILED"))
@pytest.mark.parametrize("break_lineage", (False, True))
async def test_one_hop_derived_r27_destroy_remains_historical_after_source_commit(
    database_authority_dsns: dict[str, str], terminal_state: str, break_lineage: bool
) -> None:
    _subject()
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            derived_record_id, derived_evidence_id = _add_r27_evidence_owner(
                connection,
                source,
                physical,
                state="DISPOSED",
            )
            _set_record_basis(connection, derived_record_id)
            derived_event_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO disposition_event
                        (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                         requested_by,is_worm_destroy,legal_basis,
                         derived_from_disposition_event_id,r27_request_id,r27_execution_id)
                    SELECT
                        :id,event.org_id,:record,'DESTROY',true,NULL,event.approved_by,
                        event.requested_by,true,event.legal_basis,event.id,
                        event.r27_request_id,event.r27_execution_id
                    FROM disposition_event event WHERE event.id=:source_event
                    """
                ),
                {
                    "id": derived_event_id,
                    "record": derived_record_id,
                    "source_event": source.disposition_event_id,
                },
            )
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if break_lineage:
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET derived_from_disposition_event_id=NULL "
                        "WHERE id=:id"
                    ),
                    {"id": derived_event_id},
                )
            connection.execute(
                sa.text("UPDATE r27_execution SET state=:state WHERE id=:id"),
                {"state": terminal_state, "id": source.internal_execution_id},
            )

        owners = await _list_owners(
            database_authority_dsns,
            org_id=source.actors.org_id,
            blob_sha256=physical.sha256,
        )
        assert {owner.owner_id for owner in owners} == (
            {derived_evidence_id} if break_lineage else set()
        )
    finally:
        owner.dispose()


async def test_document_and_validated_sealed_pack_legs_return_exact_association_ids(
    database_authority_dsns: dict[str, str],
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            document_seed = _set_blob_domain(
                connection,
                _seed_ordinary_owner(connection),
                bucket="documents",
            )
            _add_permanent_document_owner(connection, document_seed, "POLICY")
            document_version_id = connection.execute(
                sa.text("SELECT id FROM document_version WHERE source_blob_sha256=:sha"),
                {"sha": document_seed.blob_sha256},
            ).scalar_one()

            pack_seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            pack_record_id = _add_owner(
                connection,
                pack_seed,
                logical_hold=False,
                policy_duration="PERMANENT",
            )
            _set_record_basis(connection, pack_record_id)
            pack_evidence_id = _evidence_id(connection, pack_record_id)
            pack_id = _add_sealed_pack_pointer(
                connection,
                pack_seed,
                pack_record_id=pack_record_id,
            )

        document_owners = await _list_owners(
            database_authority_dsns,
            org_id=document_seed.org_id,
            blob_sha256=document_seed.blob_sha256,
        )
        pack_owners = await _list_owners(
            database_authority_dsns,
            org_id=pack_seed.org_id,
            blob_sha256=pack_seed.blob_sha256,
        )

        assert {(owner.kind, owner.owner_id) for owner in document_owners} == {
            (subject.WormOwnerKind.DOCUMENT_VERSION, document_version_id)
        }
        assert {(owner.kind, owner.owner_id) for owner in pack_owners} == {
            (subject.WormOwnerKind.RECORD_EVIDENCE, pack_evidence_id),
            (subject.WormOwnerKind.SEALED_PACK, pack_id),
        }
    finally:
        engine.dispose()


async def test_every_nonworm_derivative_pointer_is_excluded_from_owner_membership(
    database_authority_dsns: dict[str, str],
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="P3Y",
            )
            _set_record_basis(connection, record_id)
            evidence_id = _evidence_id(connection, record_id)
            source_sha = uuid.uuid4().hex * 2
            connection.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                         worm_locked,worm_enforced_mode,worm_asserted_retain_until,
                         worm_asserted_at,worm_retain_until,worm_retention_verified_at,
                         worm_legal_hold,worm_legal_hold_verified_at,sse)
                    SELECT
                        :source_sha,org_id,size_bytes,mime_type,'documents',:source_key,
                        :source_version,worm_locked,worm_enforced_mode,worm_asserted_retain_until,
                        worm_asserted_at,worm_retain_until,worm_retention_verified_at,
                        worm_legal_hold,worm_legal_hold_verified_at,sse
                    FROM blob WHERE sha256=:target_sha
                    """
                ),
                {
                    "source_sha": source_sha,
                    "source_key": f"documents/{source_sha}",
                    "source_version": f"version-{uuid.uuid4()}",
                    "target_sha": seed.blob_sha256,
                },
            )
            _add_permanent_document_owner(connection, seed, "POLICY")
            first_version = connection.execute(
                sa.text("SELECT id FROM document_version WHERE source_blob_sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
            second_version = uuid.uuid4()
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text(
                    "UPDATE document_version SET source_blob_sha256=:source,"
                    "rendition_blob_sha256=:target WHERE id=:id"
                ),
                {"source": source_sha, "target": seed.blob_sha256, "id": first_version},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_version
                        (id,org_id,document_id,version_seq,revision_label,change_significance,
                         change_reason,version_state,retention_authority_kind,retention_policy_id,
                         document_worm_config_id,retention_basis_date,source_blob_sha256,
                         rendition_blob_sha256,metadata_snapshot,imported,author_user_id,created_by)
                    SELECT
                        :id,org_id,document_id,2,'B',change_significance,'visual diff fixture',
                        version_state,retention_authority_kind,retention_policy_id,
                        document_worm_config_id,retention_basis_date,:source,:target,
                        metadata_snapshot,imported,author_user_id,created_by
                    FROM document_version WHERE id=:first
                    """
                ),
                {
                    "id": second_version,
                    "source": source_sha,
                    "target": seed.blob_sha256,
                    "first": first_version,
                },
            )
            connection.execute(
                sa.text("UPDATE record SET structured_pdf_blob_sha256=:sha WHERE id=:record"),
                {"sha": seed.blob_sha256, "record": record_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO evidence_pack
                        (id,org_id,framework_id,title,scope_kind,scope_selector,status,
                         portfolio_blob_sha256,created_by)
                    VALUES
                        (:id,:org,:framework,'Derivative pack','CLAUSE','{}'::jsonb,'DRAFT',
                         :sha,:user)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "org": seed.org_id,
                    "framework": seed.framework_id,
                    "sha": seed.blob_sha256,
                    "user": seed.user_id,
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO visual_diff
                        (id,org_id,document_id,from_version_id,to_version_id,status,pages)
                    SELECT
                        :id,org_id,document_id,:first,:second,'Ready',CAST(:pages AS jsonb)
                    FROM document_version WHERE id=:first
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "first": first_version,
                    "second": second_version,
                    "pages": json.dumps(
                        [
                            {
                                "page": 1,
                                "from_blob_sha": seed.blob_sha256,
                                "to_blob_sha": seed.blob_sha256,
                                "diff_blob_sha": seed.blob_sha256,
                            }
                        ]
                    ),
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO mirror_build
                        (id,org_id,build_name,manifest,manifest_sha256,documents,files,symlinks)
                    VALUES
                        (:id,:org,:name,CAST(:manifest AS jsonb),:digest,1,1,0)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "org": seed.org_id,
                    "name": f"registry-{uuid.uuid4().hex}",
                    "manifest": json.dumps(
                        [{"kind": "rendition", "blob_sha256": seed.blob_sha256}]
                    ),
                    "digest": uuid.uuid4().hex * 2,
                },
            )

        owners = await _list_owners(
            database_authority_dsns,
            org_id=seed.org_id,
            blob_sha256=seed.blob_sha256,
        )
        assert {(owner.kind, owner.owner_id) for owner in owners} == {
            (subject.WormOwnerKind.RECORD_EVIDENCE, evidence_id)
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "broken_link",
    (
        "MISSING_PACK_RECORD",
        "CROSS_ORG_PACK_RECORD",
        "MISSING_EVIDENCE_EDGE",
        "CROSS_ORG_EVIDENCE_EDGE",
        "NONPERMANENT_POLICY",
        "DESTROYED_EVIDENCE",
    ),
)
async def test_still_sealed_pack_validates_every_backing_link_before_returning(
    database_authority_dsns: dict[str, str], broken_link: str
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            pack_record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration=("P3Y" if broken_link == "NONPERMANENT_POLICY" else "PERMANENT"),
            )
            _set_record_basis(connection, pack_record_id)
            pack_id = _add_sealed_pack_pointer(
                connection,
                seed,
                pack_record_id=pack_record_id,
            )
            evidence_id = _evidence_id(connection, pack_record_id)
            if broken_link == "DESTROYED_EVIDENCE":
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:record"),
                    {"record": pack_record_id},
                )
                _add_disposition(connection, seed, pack_record_id, action="DESTROY")
            elif broken_link == "MISSING_PACK_RECORD":
                missing_record_id = uuid.uuid4()
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE evidence_pack SET pack_record_id=:record WHERE id=:id"),
                    {"record": missing_record_id, "id": pack_id},
                )
                isolated = connection.execute(
                    sa.text(
                        "SELECT original.id,parent.id FROM evidence_pack pack "
                        "JOIN evidence_blob original ON original.id=:edge "
                        "LEFT JOIN record parent ON parent.id=pack.pack_record_id "
                        "WHERE pack.id=:id"
                    ),
                    {"edge": evidence_id, "id": pack_id},
                ).one()
                assert isolated == (evidence_id, None)
            elif broken_link == "CROSS_ORG_PACK_RECORD":
                other = _seed_ordinary_owner(connection)
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE evidence_pack SET pack_record_id=:record WHERE id=:id"),
                    {"record": other.record_id, "id": pack_id},
                )
                isolated = connection.execute(
                    sa.text(
                        "SELECT original.id,original.org_id,pack.org_id,parent.org_id "
                        "FROM evidence_pack pack "
                        "JOIN evidence_blob original ON original.id=:edge "
                        "JOIN record parent ON parent.id=pack.pack_record_id "
                        "WHERE pack.id=:id"
                    ),
                    {"edge": evidence_id, "id": pack_id},
                ).one()
                assert isolated == (evidence_id, seed.org_id, seed.org_id, other.org_id)
            elif broken_link == "MISSING_EVIDENCE_EDGE":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("DELETE FROM evidence_blob WHERE id=:id"),
                    {"id": evidence_id},
                )
            elif broken_link == "CROSS_ORG_EVIDENCE_EDGE":
                other = _seed_ordinary_owner(connection)
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE evidence_blob SET org_id=:org WHERE id=:id"),
                    {"org": other.org_id, "id": evidence_id},
                )
                # Keep the generic Record-evidence leg nonowning so only the still-SEALED
                # candidate is responsible for validating this exact cross-org backing edge.
                _add_disposition(connection, seed, pack_record_id, action="DESTROY")

        with pytest.raises(subject.WormOwnerIntegrityError) as error:
            await _list_owners(
                database_authority_dsns,
                org_id=seed.org_id,
                blob_sha256=seed.blob_sha256,
            )
        assert str(error.value) == "invalid WORM owner state"
    finally:
        engine.dispose()


@pytest.mark.parametrize("terminal_shape", ("INVALIDATED_UNAVAILABLE", "CLEARED_NONSEALED"))
async def test_invalidated_unavailable_or_cleared_pack_pointer_is_not_a_live_pack_owner(
    database_authority_dsns: dict[str, str], terminal_shape: str
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            pack_record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="PERMANENT",
            )
            _set_record_basis(connection, pack_record_id)
            evidence_id = _evidence_id(connection, pack_record_id)
            pack_id = _add_sealed_pack_pointer(
                connection,
                seed,
                pack_record_id=pack_record_id,
            )
            if terminal_shape == "INVALIDATED_UNAVAILABLE":
                connection.execute(
                    sa.text(
                        "UPDATE evidence_pack SET status='UNAVAILABLE',"
                        "invalidated_at=clock_timestamp(),"
                        "invalidated_by_disposition_event_id=:event,"
                        "zip_blob_sha256=NULL,portfolio_blob_sha256=NULL WHERE id=:id"
                    ),
                    {"event": seed.disposition_event_id, "id": pack_id},
                )
            else:
                connection.execute(
                    sa.text(
                        "UPDATE evidence_pack SET status='DRAFT',zip_blob_sha256=NULL WHERE id=:id"
                    ),
                    {"id": pack_id},
                )

        owners = await _list_owners(
            database_authority_dsns,
            org_id=seed.org_id,
            blob_sha256=seed.blob_sha256,
        )
        assert {(owner.kind, owner.owner_id) for owner in owners} == {
            (subject.WormOwnerKind.RECORD_EVIDENCE, evidence_id)
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    (
        "CROSS_ORG_EDGE",
        "DANGLING_PARENT",
        "DANGLING_POLICY",
        "NON_WORM_IDENTITY",
        "CONTRADICTORY_PERMANENT_PERIOD",
    ),
)
async def test_registry_corruption_is_a_typed_bounded_failure(
    database_authority_dsns: dict[str, str], corruption: str
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="PERMANENT" if "PERMANENT" in corruption else "P3Y",
            )
            _set_record_basis(connection, record_id)
            evidence_id = _evidence_id(connection, record_id)
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if corruption == "CROSS_ORG_EDGE":
                other_org = uuid.uuid4()
                connection.execute(
                    sa.text(
                        "INSERT INTO organization(id,legal_name,short_code) "
                        "VALUES (:id,'Other registry org',:code)"
                    ),
                    {"id": other_org, "code": f"WO-{other_org.hex[:12]}"},
                )
                connection.execute(
                    sa.text("UPDATE evidence_blob SET org_id=:org WHERE id=:id"),
                    {"org": other_org, "id": evidence_id},
                )
            elif corruption == "DANGLING_PARENT":
                connection.execute(
                    sa.text("UPDATE evidence_blob SET record_id=:record WHERE id=:id"),
                    {"record": uuid.uuid4(), "id": evidence_id},
                )
            elif corruption == "DANGLING_POLICY":
                connection.execute(
                    sa.text("UPDATE record SET retention_policy_id=:policy WHERE id=:record"),
                    {"policy": uuid.uuid4(), "record": record_id},
                )
            elif corruption == "NON_WORM_IDENTITY":
                connection.execute(
                    sa.text(
                        "UPDATE blob SET worm_locked=false,object_version_id=NULL,"
                        "worm_enforced_mode=NULL,worm_asserted_retain_until=NULL,"
                        "worm_asserted_at=NULL,worm_retain_until=NULL,"
                        "worm_retention_verified_at=NULL,worm_legal_hold=NULL,"
                        "worm_legal_hold_verified_at=NULL WHERE sha256=:sha"
                    ),
                    {"sha": seed.blob_sha256},
                )
            else:
                policy_id = connection.execute(
                    sa.text("SELECT retention_policy_id FROM record WHERE id=:record"),
                    {"record": record_id},
                ).scalar_one()
                connection.execute(
                    sa.text("UPDATE retention_policy SET worm_lock_period='P3Y' WHERE id=:id"),
                    {"id": policy_id},
                )

        with pytest.raises(subject.WormOwnerIntegrityError) as error:
            await _list_owners(
                database_authority_dsns,
                org_id=seed.org_id,
                blob_sha256=seed.blob_sha256,
            )
        assert str(error.value) == "invalid WORM owner state"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    (
        "DANGLING_DOCUMENT",
        "CROSS_ORG_DOCUMENT",
        "DANGLING_POLICY",
        "CROSS_ORG_POLICY",
        "DANGLING_CONFIG",
        "CROSS_ORG_CONFIG",
    ),
)
async def test_document_owner_corruption_is_not_silently_dropped(
    database_authority_dsns: dict[str, str], corruption: str
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(
                connection,
                _seed_ordinary_owner(connection),
                bucket="documents",
            )
            authority_kind = (
                "INSTALLATION_MINIMUM"
                if corruption in {"DANGLING_CONFIG", "CROSS_ORG_CONFIG"}
                else "POLICY"
            )
            _add_permanent_document_owner(connection, seed, authority_kind)
            version = connection.execute(
                sa.text(
                    "SELECT id,document_id,retention_policy_id,document_worm_config_id "
                    "FROM document_version WHERE source_blob_sha256=:sha"
                ),
                {"sha": seed.blob_sha256},
            ).one()
            other = _seed_ordinary_owner(connection)
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if corruption == "DANGLING_DOCUMENT":
                connection.execute(
                    sa.text("UPDATE document_version SET document_id=:id WHERE id=:version"),
                    {"id": uuid.uuid4(), "version": version.id},
                )
            elif corruption == "CROSS_ORG_DOCUMENT":
                connection.execute(
                    sa.text("UPDATE documented_information SET org_id=:org WHERE id=:id"),
                    {"org": other.org_id, "id": version.document_id},
                )
            elif corruption == "DANGLING_POLICY":
                connection.execute(
                    sa.text(
                        "UPDATE document_version SET retention_policy_id=:id WHERE id=:version"
                    ),
                    {"id": uuid.uuid4(), "version": version.id},
                )
            elif corruption == "CROSS_ORG_POLICY":
                connection.execute(
                    sa.text("UPDATE retention_policy SET org_id=:org WHERE id=:id"),
                    {"org": other.org_id, "id": version.retention_policy_id},
                )
            elif corruption == "DANGLING_CONFIG":
                connection.execute(
                    sa.text(
                        "UPDATE document_version SET document_worm_config_id=:id WHERE id=:version"
                    ),
                    {"id": uuid.uuid4(), "version": version.id},
                )
            elif corruption == "CROSS_ORG_CONFIG":
                connection.execute(
                    sa.text("UPDATE document_worm_config SET org_id=:org WHERE id=:id"),
                    {"org": other.org_id, "id": version.document_worm_config_id},
                )
        with pytest.raises(subject.WormOwnerIntegrityError) as error:
            await _list_owners(
                database_authority_dsns,
                org_id=seed.org_id,
                blob_sha256=seed.blob_sha256,
            )
        assert str(error.value) == "invalid WORM owner state"
    finally:
        engine.dispose()


async def test_permanent_owner_with_logical_and_physical_hold_off_remains_repairable(
    database_authority_dsns: dict[str, str],
) -> None:
    subject = _subject()
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.begin() as connection:
            seed = _set_blob_domain(
                connection,
                _seed_ordinary_owner(connection, physical_hold=False),
            )
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="PERMANENT",
            )
            _set_record_basis(connection, record_id)
            evidence_id = _evidence_id(connection, record_id)

        owners = await _list_owners(
            database_authority_dsns,
            org_id=seed.org_id,
            blob_sha256=seed.blob_sha256,
        )
        permanent = next(owner for owner in owners if owner.owner_id == evidence_id)
        requirement = subject.owner_requirement(permanent)
        assert permanent.domain_hold is False
        assert permanent.permanent is True
        assert requirement.legal_hold is True
    finally:
        engine.dispose()


async def test_app_and_retention_roles_share_the_hardened_registry_read_seam(
    database_authority_dsns: dict[str, str],
) -> None:
    _subject()
    matrix = _seed_owner_matrix(database_authority_dsns)

    app = await _list_owners(
        database_authority_dsns,
        org_id=matrix.seed.org_id,
        blob_sha256=matrix.seed.blob_sha256,
        role="easysynq_app",
    )
    retention = await _list_owners(
        database_authority_dsns,
        org_id=matrix.seed.org_id,
        blob_sha256=matrix.seed.blob_sha256,
        role="easysynq_retention",
    )

    assert app == retention


def _advisory_locks(connection: sa.Connection, pid: int) -> set[tuple[int, int, str]]:
    return {
        (int(row.classid), int(row.objid), str(row.mode))
        for row in connection.execute(
            sa.text(
                "SELECT classid::bigint,objid::bigint,mode FROM pg_locks "
                "WHERE pid=:pid AND locktype='advisory' AND granted"
            ),
            {"pid": pid},
        )
    }


def _unsigned(value: int) -> int:
    return value & 0xFFFFFFFF


def _expired_observation(
    locator: WormObjectLocator,
    *,
    retain_until: datetime.datetime,
    read_at: datetime.datetime,
    legal_hold: bool = False,
) -> WormObjectState:
    """Build provider output while the pre-T4-30 value object still rejects expiry."""
    state = object.__new__(WormObjectState)
    object.__setattr__(state, "locator", locator)
    object.__setattr__(state, "mode", "GOVERNANCE")
    object.__setattr__(state, "retain_until", retain_until)
    object.__setattr__(state, "legal_hold", legal_hold)
    object.__setattr__(state, "read_at", read_at)
    return state


async def test_new_copy_batch_holds_pack_allocation_then_exact_locks_without_committing(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
            proposed_policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:id"),
                {"id": seeded.record_id},
            ).scalar_one()
        promotion = _promotion(seeded)
        locator = WormObjectLocator(
            promotion.target_bucket,
            promotion.target_key,
            promotion.target_version_id,
        )
        read_at = datetime.datetime.now(datetime.UTC)
        callback_locks: set[tuple[int, int, str]] = set()
        protection_locks: set[tuple[int, int, str]] = set()
        observed_requirement: WormRequirement | None = None
        pid = 0

        async def promote() -> PromotionResult:
            nonlocal callback_locks
            with owner_engine.connect() as monitor:
                callback_locks = _advisory_locks(monitor, pid)
            return promotion

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=read_at,
            )

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal observed_requirement, protection_locks
            assert observed == locator
            observed_requirement = requirement
            with owner_engine.connect() as monitor:
                protection_locks = _advisory_locks(monitor, pid)
            return _verified(locator, requirement, read_at=read_at)

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            item = subject.NewCopyProposal(
                owner=_proposed_record(subject, seeded),
                target_bucket=promotion.target_bucket,
                target_key=promotion.target_key,
                promote=promote,
            )

            results = await subject.protect_proposed_owners(session, proposals=[item])

            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            with owner_engine.connect() as monitor:
                still_held = _advisory_locks(monitor, pid)

        with owner_engine.connect() as connection:
            pack_key = int(
                connection.execute(
                    sa.text("SELECT hashtext(:org)"),
                    {"org": str(seeded.seed.org_id)},
                ).scalar_one()
            )
            registry_key = pack_key
            sha_key = int(
                connection.execute(
                    sa.text("SELECT hashtext(:sha)"),
                    {"sha": seeded.blob_sha256},
                ).scalar_one()
            )
        allocation = records_repo.destination_allocation_lock_key(
            promotion.target_bucket,
            promotion.target_key,
        )
        exact = records_repo.exact_version_lock_key(locator)
        pack_lock = (0x4553504B, _unsigned(pack_key), "ShareLock")
        allocation_lock = (allocation[0], _unsigned(allocation[1]), "ExclusiveLock")
        registry_lock = (0x45534F52, _unsigned(registry_key), "ExclusiveLock")
        sha_lock = (0x45535348, _unsigned(sha_key), "ExclusiveLock")
        exact_lock = (exact[0], _unsigned(exact[1]), "ExclusiveLock")
        assert pack_lock in callback_locks
        assert allocation_lock in callback_locks
        assert registry_lock in callback_locks
        assert sha_lock in callback_locks
        assert exact_lock not in callback_locks
        expected_locks = {pack_lock, allocation_lock, registry_lock, sha_lock, exact_lock}
        assert expected_locks <= protection_locks
        assert expected_locks <= still_held
        assert observed_requirement is not None
        assert observed_requirement.retain_until == datetime.datetime(
            2036, 1, 15, 23, 59, 59, 999000, tzinfo=datetime.UTC
        )
        assert results[0].promotion == promotion
        assert results[0].assertion.locator == locator
        assert results[0].owner.owner_id == seeded.evidence_blob_id
        assert results[0].owner.org_id == seeded.seed.org_id
        assert results[0].owner.blob_sha256 == seeded.blob_sha256
        assert results[0].authority == subject.ResolvedRecordEvidenceAuthority(
            evidence_blob_id=seeded.evidence_blob_id,
            record_id=seeded.record_id,
            retention_policy_id=proposed_policy_id,
        )
        with owner_engine.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            ).scalar_one()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "transaction_attack",
    ("COMMIT", "ROLLBACK", "COMMIT_AUTOBEGIN", "ROLLBACK_AUTOBEGIN"),
)
async def test_storage_callback_cannot_replace_the_root_database_transaction(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    transaction_attack: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    callback_calls = 0
    storage_calls = 0
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
        snapshot_sql = sa.text(
            "SELECT "
            "EXISTS (SELECT 1 FROM blob WHERE sha256=:sha),"
            "EXISTS (SELECT 1 FROM evidence_blob WHERE id=:owner),"
            "(SELECT count(*) FROM audit_event)"
        )
        parameters = {"sha": seeded.blob_sha256, "owner": seeded.evidence_blob_id}
        with owner_engine.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()

        promotion = _promotion(seeded, version=f"malicious-{transaction_attack.lower()}")

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            nonlocal storage_calls
            storage_calls += 1
            raise AssertionError("transaction replacement reached exact storage protection")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)

        async with _session(database_authority_dsns) as session:
            root_txid = int(await session.scalar(sa.text("SELECT txid_current()")))
            root_transaction = session.get_transaction()
            assert root_transaction is not None

            async def malicious_callback() -> PromotionResult:
                nonlocal callback_calls
                callback_calls += 1
                if transaction_attack.startswith("ROLLBACK"):
                    await session.rollback()
                else:
                    await session.commit()
                if transaction_attack.endswith("AUTOBEGIN"):
                    assert await session.scalar(sa.text("SELECT 1")) == 1
                return promotion

            with pytest.raises(subject.WormOwnerIntegrityError) as error:
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, seeded),
                            target_bucket=promotion.target_bucket,
                            target_key=promotion.target_key,
                            promote=malicious_callback,
                        )
                    ],
                )

            assert str(error.value) == "invalid WORM owner state"
            assert session.get_transaction() is not root_transaction
            if session.in_transaction():
                assert int(await session.scalar(sa.text("SELECT txid_current()"))) != root_txid
                await session.rollback()

        assert callback_calls == 1
        assert storage_calls == 0
        with owner_engine.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner_engine.dispose()


async def test_transaction_identity_is_rechecked_after_each_batch_callback(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    second_callback_calls = 0
    try:
        with owner_engine.begin() as connection:
            first = replace(_seed_proposed_record(connection), blob_sha256="1" * 64)
            second = _add_proposed_record(
                connection,
                first.seed,
                blob_sha256="2" * 64,
            )
        promotions = (_promotion(first), _promotion(second))

        async def first_callback() -> PromotionResult:
            await session.commit()
            return promotions[0]

        async def second_callback() -> PromotionResult:
            nonlocal second_callback_calls
            second_callback_calls += 1
            raise AssertionError("second callback ran after the first replaced the transaction")

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("transaction replacement reached storage")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)
        async with _session(database_authority_dsns) as session:
            with pytest.raises(subject.WormOwnerIntegrityError):
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, first),
                            target_bucket=promotions[0].target_bucket,
                            target_key=promotions[0].target_key,
                            promote=first_callback,
                        ),
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, second),
                            target_bucket=promotions[1].target_bucket,
                            target_key=promotions[1].target_key,
                            promote=second_callback,
                        ),
                    ],
                )
            if session.in_transaction():
                await session.rollback()

        assert second_callback_calls == 0
        with owner_engine.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id IN (:first,:second))"),
                {"first": first.evidence_blob_id, "second": second.evidence_blob_id},
            ).scalar_one()
    finally:
        owner_engine.dispose()


async def test_multi_new_batch_acquires_every_allocation_before_any_callback_and_every_exact_after(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            first = _seed_proposed_record(connection, duration="P3Y")
            second = _add_proposed_record(
                connection,
                first.seed,
                blob_sha256=uuid.uuid4().hex * 2,
                duration="P10Y",
            )
        promotions = (
            _promotion(first, key=first.blob_sha256, version="version-first"),
            _promotion(second, key=second.blob_sha256, version="version-second"),
        )
        locators = tuple(
            WormObjectLocator(
                promotion.target_bucket,
                promotion.target_key,
                promotion.target_version_id,
            )
            for promotion in promotions
        )
        with owner_engine.connect() as connection:
            pack_key = int(
                connection.execute(
                    sa.text("SELECT hashtext(:org)"),
                    {"org": str(first.seed.org_id)},
                ).scalar_one()
            )
            sha_hashes = {
                seeded.blob_sha256: int(
                    connection.execute(
                        sa.text("SELECT hashtext(:sha)"),
                        {"sha": seeded.blob_sha256},
                    ).scalar_one()
                )
                for seeded in (first, second)
            }
        pack_lock = (0x4553504B, _unsigned(pack_key), "ShareLock")
        registry_lock = (0x45534F52, _unsigned(pack_key), "ExclusiveLock")
        sha_locks = {
            (0x45535348, _unsigned(value), "ExclusiveLock") for value in sha_hashes.values()
        }
        allocation_locks = {
            (
                lock_key[0],
                _unsigned(lock_key[1]),
                "ExclusiveLock",
            )
            for lock_key in (
                records_repo.destination_allocation_lock_key(
                    promotion.target_bucket,
                    promotion.target_key,
                )
                for promotion in promotions
            )
        }
        exact_locks = {
            (lock_key[0], _unsigned(lock_key[1]), "ExclusiveLock")
            for lock_key in map(records_repo.exact_version_lock_key, locators)
        }
        completed_promotions: set[int] = set()
        callback_observations: list[set[tuple[int, int, str]]] = []
        exact_phase_observations: list[set[tuple[int, int, str]]] = []
        pid = 0
        read_at = datetime.datetime.now(datetime.UTC)

        def promote_callback(index: int) -> Any:
            async def promote() -> PromotionResult:
                with owner_engine.connect() as monitor:
                    locks = _advisory_locks(monitor, pid)
                callback_observations.append(locks)
                assert {pack_lock, registry_lock, *allocation_locks, *sha_locks} <= locks
                assert locks.isdisjoint(exact_locks)
                completed_promotions.add(index)
                return promotions[index]

            return promote

        async def read_worm_state(locator: WormObjectLocator) -> WormObjectState:
            assert completed_promotions == {0, 1}
            with owner_engine.connect() as monitor:
                locks = _advisory_locks(monitor, pid)
            exact_phase_observations.append(locks)
            assert {
                pack_lock,
                registry_lock,
                *allocation_locks,
                *sha_locks,
                *exact_locks,
            } <= locks
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=read_at,
            )

        async def apply_worm_protection(
            locator: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            assert completed_promotions == {0, 1}
            with owner_engine.connect() as monitor:
                locks = _advisory_locks(monitor, pid)
            exact_phase_observations.append(locks)
            assert {
                pack_lock,
                registry_lock,
                *allocation_locks,
                *sha_locks,
                *exact_locks,
            } <= locks
            return _verified(locator, requirement, read_at=read_at)

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            proposals = [
                subject.NewCopyProposal(
                    owner=_proposed_record(subject, seeded),
                    target_bucket=promotion.target_bucket,
                    target_key=promotion.target_key,
                    promote=promote_callback(index),
                )
                for index, (seeded, promotion) in enumerate(
                    zip((first, second), promotions, strict=True)
                )
            ]

            results = await subject.protect_proposed_owners(session, proposals=proposals)

            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            with owner_engine.connect() as monitor:
                still_held = _advisory_locks(monitor, pid)

        assert len(callback_observations) == 2
        assert exact_phase_observations
        assert {
            pack_lock,
            registry_lock,
            *allocation_locks,
            *sha_locks,
            *exact_locks,
        } <= still_held
        assert {result.assertion.locator for result in results} == set(locators)
    finally:
        owner_engine.dispose()


async def test_protection_failure_leaves_no_owner_blob_or_success_audit_write(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
            audit_before = connection.execute(
                sa.text("SELECT count(*) FROM audit_event")
            ).scalar_one()
        promotion = _promotion(seeded)

        async def promote() -> PromotionResult:
            return promotion

        async def read_worm_state(locator: WormObjectLocator) -> WormObjectState:
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )

        async def refuse_protection(
            _locator: WormObjectLocator, _requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            raise RuntimeError("injected exact protection failure")

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", refuse_protection)
        async with _session(database_authority_dsns) as session:
            item = subject.NewCopyProposal(
                owner=_proposed_record(subject, seeded),
                target_bucket=promotion.target_bucket,
                target_key=promotion.target_key,
                promote=promote,
            )
            with pytest.raises(RuntimeError, match="injected exact protection failure"):
                await subject.protect_proposed_owners(session, proposals=[item])
            await session.flush()
            assert session.in_transaction()
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            )
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": seeded.blob_sha256},
            )
            assert await session.scalar(sa.text("SELECT count(*) FROM audit_event")) == audit_before
            await session.rollback()

        with owner_engine.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            ).scalar_one()
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": seeded.blob_sha256},
            ).scalar_one()
            assert (
                connection.execute(sa.text("SELECT count(*) FROM audit_event")).scalar_one()
                == audit_before
            )
    finally:
        owner_engine.dispose()


async def test_locked_adopted_orphan_retry_repairs_expired_exact_version_without_manual_blob_row(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection, duration="P10Y")
            retention_policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:record"),
                {"record": seeded.record_id},
            ).scalar_one()
            org_hash = int(
                connection.execute(
                    sa.text("SELECT hashtext(:org)"),
                    {"org": str(seeded.seed.org_id)},
                ).scalar_one()
            )
            sha_hash = int(
                connection.execute(
                    sa.text("SELECT hashtext(:sha)"),
                    {"sha": seeded.blob_sha256},
                ).scalar_one()
            )
        copied = _promotion(seeded, version="adopted-orphan-version")
        adopted = replace(copied, outcome=PromotionOutcome.ADOPTED_EXISTING)
        locator = WormObjectLocator(
            adopted.target_bucket,
            adopted.target_key,
            adopted.target_version_id,
        )
        read_at = datetime.datetime.now(datetime.UTC)
        expired = _expired_observation(
            locator,
            retain_until=read_at - datetime.timedelta(days=1),
            read_at=read_at,
        )
        pid = 0
        callback_locks: set[tuple[int, int, str]] = set()
        protection_locks: set[tuple[int, int, str]] = set()
        applied_requirement: WormRequirement | None = None
        allocation_key = records_repo.destination_allocation_lock_key(
            adopted.target_bucket,
            adopted.target_key,
        )
        exact_key = records_repo.exact_version_lock_key(locator)

        async def promote() -> PromotionResult:
            nonlocal callback_locks
            with owner_engine.connect() as monitor:
                callback_locks = _advisory_locks(monitor, pid)
                assert not monitor.execute(
                    sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                    {"sha": seeded.blob_sha256},
                ).scalar_one()
            return adopted

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return expired

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal applied_requirement, protection_locks
            assert observed == locator
            assert requirement.retain_until is not None
            assert requirement.retain_until > read_at
            with owner_engine.connect() as monitor:
                protection_locks = _advisory_locks(monitor, pid)
            applied_requirement = requirement
            return _verified(locator, requirement, read_at=read_at)

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            protected = (
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, seeded),
                            target_bucket=adopted.target_bucket,
                            target_key=adopted.target_key,
                            promote=promote,
                        )
                    ],
                )
            )[0]

            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": seeded.blob_sha256},
            )
            await session.rollback()

        assert callback_locks >= {
            (0x4553504B, _unsigned(org_hash), "ShareLock"),
            (allocation_key[0], _unsigned(allocation_key[1]), "ExclusiveLock"),
            (0x45534F52, _unsigned(org_hash), "ExclusiveLock"),
            (0x45535348, _unsigned(sha_hash), "ExclusiveLock"),
        }
        assert (
            exact_key[0],
            _unsigned(exact_key[1]),
            "ExclusiveLock",
        ) not in callback_locks
        assert protection_locks >= {
            (0x4553504B, _unsigned(org_hash), "ShareLock"),
            (allocation_key[0], _unsigned(allocation_key[1]), "ExclusiveLock"),
            (0x45534F52, _unsigned(org_hash), "ExclusiveLock"),
            (0x45535348, _unsigned(sha_hash), "ExclusiveLock"),
            (exact_key[0], _unsigned(exact_key[1]), "ExclusiveLock"),
        }
        assert applied_requirement is not None
        assert protected.promotion == adopted
        assert protected.assertion.locator == locator
        assert protected.authority == subject.ResolvedRecordEvidenceAuthority(
            evidence_blob_id=seeded.evidence_blob_id,
            record_id=seeded.record_id,
            retention_policy_id=retention_policy_id,
        )
    finally:
        owner_engine.dispose()


async def test_existing_version_proposal_ratchets_expired_persisted_exact_blob(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection, duration="P10Y")
            current_record_id = _add_owner(
                connection,
                seeded.seed,
                logical_hold=False,
                policy_duration="P3Y",
            )
            _set_record_basis(connection, current_record_id)
            persisted_blob = connection.execute(
                sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                {"sha": seeded.seed.blob_sha256},
            ).scalar_one()
        seeded = replace(seeded, blob_sha256=seeded.seed.blob_sha256)
        locator = WormObjectLocator(
            seeded.seed.bucket,
            seeded.seed.object_key,
            seeded.seed.object_version_id,
        )
        read_at = datetime.datetime.now(datetime.UTC)
        expired = _expired_observation(
            locator,
            retain_until=read_at - datetime.timedelta(days=1),
            legal_hold=True,
            read_at=read_at,
        )
        applied_requirement: WormRequirement | None = None

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return expired

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal applied_requirement
            assert observed == locator
            assert requirement.retain_until is not None
            assert requirement.retain_until > read_at
            assert requirement.legal_hold is True
            applied_requirement = requirement
            return _verified(locator, requirement, read_at=read_at)

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            protected = await subject.protect_existing_owner(
                session,
                owner=_proposed_record(subject, seeded),
            )
            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            assert protected.promotion is None
            assert protected.assertion.locator == locator
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            )
            await session.rollback()

        assert applied_requirement is not None
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                    {"sha": seeded.blob_sha256},
                ).scalar_one()
                == persisted_blob
            )
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            ).scalar_one()
    finally:
        owner_engine.dispose()


async def test_reconciliation_ratchets_an_expired_exact_provider_observation(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seed = _set_blob_domain(
                connection,
                _seed_ordinary_owner(connection, physical_hold=False),
            )
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="P10Y",
            )
            _set_record_basis(connection, record_id)
        locator = WormObjectLocator(seed.bucket, seed.object_key, seed.object_version_id)
        read_at = datetime.datetime.now(datetime.UTC)
        expired = _expired_observation(
            locator,
            retain_until=read_at - datetime.timedelta(days=1),
            read_at=read_at,
        )
        applied: WormRequirement | None = None

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return expired

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal applied
            assert observed == locator
            assert requirement.retain_until is not None
            assert requirement.retain_until > read_at
            applied = requirement
            return _verified(locator, requirement, read_at=read_at)

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            assertion = await subject.reconcile_exact_version(
                session,
                org_id=seed.org_id,
                blob_sha256=seed.blob_sha256,
                locator=locator,
            )
            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            assert assertion.locator == locator
            await session.rollback()

        assert applied is not None
    finally:
        owner_engine.dispose()


async def test_database_rollback_after_success_leaves_only_exact_overretained_orphan_for_reconcile(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection, duration="P10Y")
        promotion = _promotion(seeded, version="orphan-exact-version")
        locator = WormObjectLocator(
            promotion.target_bucket,
            promotion.target_key,
            promotion.target_version_id,
        )
        read_at = datetime.datetime.now(datetime.UTC)
        physical_state = WormObjectState(
            locator=locator,
            mode="GOVERNANCE",
            retain_until=read_at + datetime.timedelta(days=30),
            legal_hold=False,
            read_at=read_at,
        )
        exact_operations: list[WormObjectLocator] = []

        async def promote() -> PromotionResult:
            return promotion

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            exact_operations.append(observed)
            assert observed == locator
            return physical_state

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal physical_state
            exact_operations.append(observed)
            assert observed == locator
            asserted = _verified(
                locator,
                requirement,
                read_at=datetime.datetime.now(datetime.UTC),
            )
            physical_state = asserted.verified
            return asserted

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as session:
            protected = (
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, seeded),
                            target_bucket=promotion.target_bucket,
                            target_key=promotion.target_key,
                            promote=promote,
                        )
                    ],
                )
            )[0]
            await session.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                         worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                         worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                         worm_legal_hold_verified_at,sse)
                    VALUES
                        (:sha,:org,1,'application/octet-stream',:bucket,:key,:version,true,
                         'GOVERNANCE',:retain,:asserted_at,:retain,:verified_at,:hold,:verified_at,
                         false)
                    """
                ),
                {
                    "sha": seeded.blob_sha256,
                    "org": seeded.seed.org_id,
                    "bucket": locator.bucket,
                    "key": locator.object_key,
                    "version": locator.object_version_id,
                    "retain": protected.assertion.asserted_retain_until,
                    "asserted_at": protected.assertion.asserted_at,
                    "verified_at": protected.assertion.verified.read_at,
                    "hold": protected.assertion.verified.legal_hold,
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO evidence_blob"
                    "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                    "VALUES (:id,:org,:record,:sha,true,:user)"
                ),
                {
                    "id": seeded.evidence_blob_id,
                    "org": seeded.seed.org_id,
                    "record": seeded.record_id,
                    "sha": seeded.blob_sha256,
                    "user": seeded.seed.user_id,
                },
            )
            assert await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            )
            await session.rollback()

        expected_retain = datetime.datetime(
            2036,
            1,
            15,
            23,
            59,
            59,
            999000,
            tzinfo=datetime.UTC,
        )
        assert physical_state.retain_until == expected_retain
        with owner_engine.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": seeded.blob_sha256},
            ).scalar_one()
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            ).scalar_one()

        weaker_at = datetime.datetime.now(datetime.UTC)
        weaker_retain = weaker_at + datetime.timedelta(days=30)
        with owner_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                         worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                         worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                         worm_legal_hold_verified_at,sse)
                    VALUES
                        (:sha,:org,1,'application/octet-stream',:bucket,:key,:version,true,
                         'GOVERNANCE',:retain,:at,:retain,:at,false,:at,false)
                    """
                ),
                {
                    "sha": seeded.blob_sha256,
                    "org": seeded.seed.org_id,
                    "bucket": locator.bucket,
                    "key": locator.object_key,
                    "version": locator.object_version_id,
                    "retain": weaker_retain,
                    "at": weaker_at,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO evidence_blob"
                    "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                    "VALUES (:id,:org,:record,:sha,true,:user)"
                ),
                {
                    "id": seeded.evidence_blob_id,
                    "org": seeded.seed.org_id,
                    "record": seeded.record_id,
                    "sha": seeded.blob_sha256,
                    "user": seeded.seed.user_id,
                },
            )

        async with _session(database_authority_dsns) as session:
            assertion = await subject.reconcile_exact_version(
                session,
                org_id=seeded.seed.org_id,
                blob_sha256=seeded.blob_sha256,
                locator=locator,
            )
            assert assertion.locator == locator
            assert (
                await session.scalar(
                    sa.text("SELECT worm_retain_until FROM blob WHERE sha256=:sha"),
                    {"sha": seeded.blob_sha256},
                )
                == expected_retain
            )
            await session.commit()

        assert exact_operations
        assert set(exact_operations) == {locator}
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT worm_retain_until FROM blob WHERE sha256=:sha"),
                    {"sha": seeded.blob_sha256},
                ).scalar_one()
                == expected_retain
            )
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "mismatch",
    ("SHA", "BUCKET", "KEY", "VERSION"),
)
async def test_new_copy_identity_mismatch_refuses_before_storage_protection_or_visibility(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
        expected = _promotion(seeded)
        overrides: dict[str, Any] = {}
        if mismatch == "SHA":
            overrides["verified_sha256"] = "f" * 64
        elif mismatch == "BUCKET":
            overrides["target_bucket"] = "documents"
        elif mismatch == "KEY":
            overrides["target_key"] = "records/different-key"
        else:
            assert mismatch == "VERSION"
            overrides["target_version_id"] = "null"
        mismatched = replace(expected, **overrides)
        storage_called = False

        async def promote() -> PromotionResult:
            return mismatched

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            nonlocal storage_called
            storage_called = True
            raise AssertionError("storage protection ran after an identity mismatch")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)
        async with _session(database_authority_dsns) as session:
            proposal = subject.NewCopyProposal(
                owner=_proposed_record(subject, seeded),
                target_bucket=expected.target_bucket,
                target_key=expected.target_key,
                promote=promote,
            )
            with pytest.raises(WormIdentityMismatch):
                await subject.protect_proposed_owners(session, proposals=[proposal])
            await session.flush()
            assert session.in_transaction()
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            )
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": seeded.blob_sha256},
            )
            await session.rollback()

        assert not storage_called
        with owner_engine.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": seeded.evidence_blob_id},
            ).scalar_one()
    finally:
        owner_engine.dispose()


async def test_cross_org_same_destination_serializes_before_copy_and_loser_refuses(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            first = _seed_proposed_record(connection)
            second = _seed_proposed_record(connection)
        second = replace(second, blob_sha256=first.blob_sha256)
        first_promotion = _promotion(first, version="winner-version")
        target_key = first_promotion.target_key
        first_started = Event()
        release_first = Event()
        second_started = Event()

        async def first_promote() -> PromotionResult:
            first_started.set()
            await release_first.wait()
            return first_promotion

        async def second_promote() -> PromotionResult:
            second_started.set()
            return _promotion(
                second,
                key=target_key,
                version="loser-must-not-exist",
            )

        async def read_worm_state(locator: WormObjectLocator) -> WormObjectState:
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            locator: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            return _verified(
                locator,
                requirement,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with (
            _session(database_authority_dsns) as first_session,
            _session(database_authority_dsns) as second_session,
        ):
            await first_session.execute(sa.text("SET application_name='t4-global-winner'"))
            await second_session.execute(sa.text("SET application_name='t4-global-loser'"))
            first_item = subject.NewCopyProposal(
                owner=_proposed_record(subject, first),
                target_bucket="records",
                target_key=target_key,
                promote=first_promote,
            )
            second_item = subject.NewCopyProposal(
                owner=_proposed_record(subject, second),
                target_bucket="records",
                target_key=target_key,
                promote=second_promote,
            )
            winner = create_task(
                subject.protect_proposed_owners(first_session, proposals=[first_item])
            )
            await wait_for(first_started.wait(), timeout=5)
            loser = create_task(
                subject.protect_proposed_owners(second_session, proposals=[second_item])
            )
            assert await to_thread(
                _wait_for_named_lock,
                owner_engine,
                "t4-global-loser",
                timeout_seconds=5,
            )
            assert not second_started.is_set()

            release_first.set()
            winner_results = await wait_for(winner, timeout=5)
            assertion = winner_results[0].assertion
            await first_session.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                         worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                         worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                         worm_legal_hold_verified_at,sse)
                    VALUES
                        (:sha,:org,1,'application/octet-stream',:bucket,:key,:version,true,
                         'GOVERNANCE',:retain,:asserted_at,:retain,:verified_at,:hold,:verified_at,
                         false)
                    """
                ),
                {
                    "sha": first.blob_sha256,
                    "org": first.seed.org_id,
                    "bucket": assertion.locator.bucket,
                    "key": assertion.locator.object_key,
                    "version": assertion.locator.object_version_id,
                    "retain": assertion.asserted_retain_until,
                    "asserted_at": assertion.asserted_at,
                    "verified_at": assertion.verified.read_at,
                    "hold": assertion.verified.legal_hold,
                },
            )
            await first_session.execute(
                sa.text(
                    """
                    INSERT INTO evidence_blob
                        (id,org_id,record_id,blob_sha256,is_original,created_by)
                    VALUES (:id,:org,:record,:sha,true,:user)
                    """
                ),
                {
                    "id": first.evidence_blob_id,
                    "org": first.seed.org_id,
                    "record": first.record_id,
                    "sha": first.blob_sha256,
                    "user": first.seed.user_id,
                },
            )
            await first_session.commit()

            with pytest.raises(subject.WormOwnerIntegrityError):
                await wait_for(loser, timeout=5)
            assert not second_started.is_set()

        with owner_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT org_id,object_version_id FROM blob WHERE sha256=:sha"),
                {"sha": first.blob_sha256},
            ).one() == (first.seed.org_id, "winner-version")
    finally:
        owner_engine.dispose()


async def test_same_sha_different_domain_and_org_first_write_serializes_globally(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            record = _seed_proposed_record(connection)
            document = _seed_proposed_document(connection, authority_kind="POLICY")
        document = replace(document, blob_sha256=record.blob_sha256)
        first_promotion = _promotion(record, version="cross-domain-winner")
        second_promotion = _promotion_for_sha(
            document.blob_sha256,
            bucket="documents",
            key=document.blob_sha256,
            version="cross-domain-loser",
        )
        first_started = Event()
        release_first = Event()
        second_started = Event()

        async def first_promote() -> PromotionResult:
            first_started.set()
            await release_first.wait()
            return first_promotion

        async def second_promote() -> PromotionResult:
            second_started.set()
            return second_promotion

        async def read_worm_state(locator: WormObjectLocator) -> WormObjectState:
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            locator: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            return _verified(locator, requirement, read_at=datetime.datetime.now(datetime.UTC))

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with (
            _session(database_authority_dsns) as record_session,
            _session(database_authority_dsns) as document_session,
        ):
            await record_session.execute(sa.text("SET application_name='t4-cross-domain-winner'"))
            await document_session.execute(sa.text("SET application_name='t4-cross-domain-loser'"))
            winner = create_task(
                subject.protect_proposed_owners(
                    record_session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, record),
                            target_bucket="records",
                            target_key=record.blob_sha256,
                            promote=first_promote,
                        )
                    ],
                )
            )
            await wait_for(first_started.wait(), timeout=5)
            loser = create_task(
                subject.protect_proposed_owners(
                    document_session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=subject.ProposedDocumentSource(
                                owner_id=document.document_version_id,
                                document_id=document.document_id,
                                org_id=document.seed.org_id,
                                blob_sha256=document.blob_sha256,
                                authority_kind=document.authority_kind,
                                authority_id=document.authority_id,
                            ),
                            target_bucket="documents",
                            target_key=document.blob_sha256,
                            promote=second_promote,
                        )
                    ],
                )
            )
            assert await to_thread(
                _wait_for_named_lock,
                owner_engine,
                "t4-cross-domain-loser",
                timeout_seconds=5,
            )
            assert not second_started.is_set()

            release_first.set()
            protected = (await wait_for(winner, timeout=5))[0]
            await record_session.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                         worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                         worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                         worm_legal_hold_verified_at,sse)
                    VALUES
                        (:sha,:org,1,'application/octet-stream',:bucket,:key,:version,true,
                         'GOVERNANCE',:retain,:asserted_at,:retain,:verified_at,:hold,:verified_at,
                         false)
                    """
                ),
                {
                    "sha": record.blob_sha256,
                    "org": record.seed.org_id,
                    "bucket": protected.assertion.locator.bucket,
                    "key": protected.assertion.locator.object_key,
                    "version": protected.assertion.locator.object_version_id,
                    "retain": protected.assertion.asserted_retain_until,
                    "asserted_at": protected.assertion.asserted_at,
                    "verified_at": protected.assertion.verified.read_at,
                    "hold": protected.assertion.verified.legal_hold,
                },
            )
            await record_session.execute(
                sa.text(
                    "INSERT INTO evidence_blob"
                    "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                    "VALUES (:id,:org,:record,:sha,true,:user)"
                ),
                {
                    "id": record.evidence_blob_id,
                    "org": record.seed.org_id,
                    "record": record.record_id,
                    "sha": record.blob_sha256,
                    "user": record.seed.user_id,
                },
            )
            await record_session.commit()

            with pytest.raises(subject.WormOwnerIntegrityError):
                await wait_for(loser, timeout=5)
            assert not second_started.is_set()

        with owner_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT org_id,bucket FROM blob WHERE sha256=:sha"),
                {"sha": record.blob_sha256},
            ).one() == (record.seed.org_id, "records")
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM document_version WHERE id=:id)"),
                {"id": document.document_version_id},
            ).scalar_one()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize("first_path", ("PROPOSAL", "RAW_REGISTRY"))
async def test_proposal_and_raw_registry_paths_share_the_same_absent_sha_locks(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    first_path: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    app_engine = sa.create_engine(database_authority_dsns["easysynq_app"])
    proposal_callback = Event()
    release_callback = Event()
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
        promotion = _promotion(seeded, version=f"crossed-{first_path.lower()}")
        locator = WormObjectLocator(
            promotion.target_bucket,
            promotion.target_key,
            promotion.target_version_id,
        )

        async def promote() -> PromotionResult:
            proposal_callback.set()
            if first_path == "PROPOSAL":
                await release_callback.wait()
            return promotion

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            assert observed == locator
            return _verified(locator, requirement, read_at=datetime.datetime.now(datetime.UTC))

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with _session(database_authority_dsns) as proposal_session:
            proposal_name = f"t4-crossed-proposal-{first_path.lower()}"
            raw_name = f"t4-crossed-raw-{first_path.lower()}"
            await proposal_session.execute(
                sa.text("SELECT set_config('application_name',:name,false)"),
                {"name": proposal_name},
            )
            item = subject.NewCopyProposal(
                owner=_proposed_record(subject, seeded),
                target_bucket=promotion.target_bucket,
                target_key=promotion.target_key,
                promote=promote,
            )

            if first_path == "PROPOSAL":
                proposal_task = create_task(
                    subject.protect_proposed_owners(proposal_session, proposals=[item])
                )
                await wait_for(proposal_callback.wait(), timeout=5)

                def raw_registry() -> list[sa.Row[Any]]:
                    with app_engine.begin() as connection:
                        connection.execute(
                            sa.text("SELECT set_config('application_name',:name,false)"),
                            {"name": raw_name},
                        )
                        connection.execute(sa.text("SET LOCAL statement_timeout='15s'"))
                        return connection.execute(
                            sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                            {"org": seeded.seed.org_id, "sha": seeded.blob_sha256},
                        ).all()

                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    raw_future = executor.submit(raw_registry)
                    assert await to_thread(
                        _wait_for_named_lock,
                        owner_engine,
                        raw_name,
                        timeout_seconds=5,
                    )
                    release_callback.set()
                    assert len(await wait_for(proposal_task, timeout=5)) == 1
                    assert not raw_future.done()
                    await proposal_session.rollback()
                    assert raw_future.result(timeout=10) == []
                finally:
                    executor.shutdown(wait=True, cancel_futures=True)
            else:
                with app_engine.connect() as raw_connection:
                    raw_transaction = raw_connection.begin()
                    raw_connection.execute(
                        sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                        {"org": seeded.seed.org_id, "sha": seeded.blob_sha256},
                    ).all()
                    proposal_task = create_task(
                        subject.protect_proposed_owners(proposal_session, proposals=[item])
                    )
                    assert await to_thread(
                        _wait_for_named_lock,
                        owner_engine,
                        proposal_name,
                        timeout_seconds=5,
                    )
                    assert not proposal_callback.is_set()
                    raw_transaction.rollback()
                assert len(await wait_for(proposal_task, timeout=5)) == 1
                assert proposal_callback.is_set()
                await proposal_session.rollback()
    finally:
        owner_engine.dispose()
        app_engine.dispose()


async def test_proposal_holds_pack_and_destination_before_waiting_on_esor(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    application_name = f"t4-proposal-prefix-{uuid.uuid4().hex}"
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
            org_hash = int(
                connection.execute(
                    sa.text("SELECT hashtext(:org)"),
                    {"org": str(seeded.seed.org_id)},
                ).scalar_one()
            )
        promotion = _promotion(seeded, version="prefix-version")
        locator = WormObjectLocator(
            promotion.target_bucket,
            promotion.target_key,
            promotion.target_version_id,
        )
        allocation = records_repo.destination_allocation_lock_key(
            promotion.target_bucket,
            promotion.target_key,
        )

        async def promote() -> PromotionResult:
            return promotion

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            return _verified(observed, requirement, read_at=datetime.datetime.now(datetime.UTC))

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        with owner_engine.connect() as locker:
            lock_transaction = locker.begin()
            locker.execute(
                sa.text("SELECT pg_advisory_xact_lock(1163087698,hashtext(:org))"),
                {"org": str(seeded.seed.org_id)},
            )
            async with _session(database_authority_dsns) as session:
                await session.execute(
                    sa.text("SELECT set_config('application_name',:name,false)"),
                    {"name": application_name},
                )
                task = create_task(
                    subject.protect_proposed_owners(
                        session,
                        proposals=[
                            subject.NewCopyProposal(
                                owner=_proposed_record(subject, seeded),
                                target_bucket=promotion.target_bucket,
                                target_key=promotion.target_key,
                                promote=promote,
                            )
                        ],
                    )
                )
                try:
                    assert await to_thread(
                        _wait_for_named_lock,
                        owner_engine,
                        application_name,
                        timeout_seconds=5,
                    )
                    pid = locker.execute(
                        sa.text("SELECT pid FROM pg_stat_activity WHERE application_name=:name"),
                        {"name": application_name},
                    ).scalar_one()
                    locks = set(
                        locker.execute(
                            sa.text(
                                "SELECT classid::bigint,objid::bigint,mode,granted "
                                "FROM pg_locks WHERE pid=:pid AND locktype='advisory'"
                            ),
                            {"pid": pid},
                        ).all()
                    )
                    assert (0x4553504B, _unsigned(org_hash), "ShareLock", True) in locks
                    assert (
                        allocation[0],
                        _unsigned(allocation[1]),
                        "ExclusiveLock",
                        True,
                    ) in locks
                    assert (
                        0x45534F52,
                        _unsigned(org_hash),
                        "ExclusiveLock",
                        False,
                    ) in locks
                    assert not any(lock[0] == 0x45535348 for lock in locks)
                finally:
                    if lock_transaction.is_active:
                        lock_transaction.commit()
                assert len(await wait_for(task, timeout=5)) == 1
                await session.rollback()
    finally:
        owner_engine.dispose()


async def test_inverted_mixed_new_existing_batches_share_one_deterministic_lock_order(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            base = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            new_sha = uuid.uuid4().hex * 2
            first_new = _add_proposed_record(connection, base, blob_sha256=new_sha)
            first_existing = _add_proposed_record(
                connection,
                base,
                blob_sha256=base.blob_sha256,
            )
            second_new = _add_proposed_record(connection, base, blob_sha256=new_sha)
            second_existing = _add_proposed_record(
                connection,
                base,
                blob_sha256=base.blob_sha256,
            )
        target_key = new_sha
        shared_promotion = _promotion(
            first_new,
            key=target_key,
            version="shared-batch-version",
        )
        first_protection_entered = Event()
        release_first = Event()
        apply_calls = 0

        async def promote() -> PromotionResult:
            return shared_promotion

        async def read_worm_state(locator: WormObjectLocator) -> WormObjectState:
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=locator.object_version_id == base.object_version_id,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            locator: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal apply_calls
            apply_calls += 1
            if apply_calls == 1:
                first_protection_entered.set()
                await release_first.wait()
            return _verified(
                locator,
                requirement,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)

        async with (
            _session(database_authority_dsns) as first_session,
            _session(database_authority_dsns) as second_session,
        ):
            await first_session.execute(sa.text("SET application_name='t4-mixed-forward'"))
            await second_session.execute(sa.text("SET application_name='t4-mixed-inverted'"))
            forward = [
                subject.NewCopyProposal(
                    owner=_proposed_record(subject, first_new),
                    target_bucket="records",
                    target_key=target_key,
                    promote=promote,
                ),
                subject.ExistingVersionProposal(
                    owner=_proposed_record(subject, first_existing),
                ),
            ]
            inverted = [
                subject.ExistingVersionProposal(
                    owner=_proposed_record(subject, second_existing),
                ),
                subject.NewCopyProposal(
                    owner=_proposed_record(subject, second_new),
                    target_bucket="records",
                    target_key=target_key,
                    promote=promote,
                ),
            ]
            forward_task = create_task(
                subject.protect_proposed_owners(first_session, proposals=forward)
            )
            await wait_for(first_protection_entered.wait(), timeout=5)
            inverted_task = create_task(
                subject.protect_proposed_owners(second_session, proposals=inverted)
            )
            assert await to_thread(
                _wait_for_named_lock,
                owner_engine,
                "t4-mixed-inverted",
                timeout_seconds=5,
            )
            release_first.set()
            assert len(await wait_for(forward_task, timeout=5)) == 2
            await first_session.rollback()
            assert len(await wait_for(inverted_task, timeout=5)) == 2
            await second_session.rollback()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    ("proposal_duration", "current_duration"),
    (("P10Y", "P3Y"), ("P3Y", "P10Y")),
)
async def test_existing_version_adoption_derives_locator_and_stronger_owner_from_database(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    proposal_duration: str,
    current_duration: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection, duration=proposal_duration)
            current_record_id = _add_owner(
                connection,
                seeded.seed,
                logical_hold=False,
                policy_duration=current_duration,
            )
            _set_record_basis(connection, current_record_id)
            current_evidence_id = _evidence_id(connection, current_record_id)
            current_policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:id"),
                {"id": current_record_id},
            ).scalar_one()
            proposed_policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:id"),
                {"id": seeded.record_id},
            ).scalar_one()
        seeded = replace(seeded, blob_sha256=seeded.seed.blob_sha256)
        locator = WormObjectLocator(
            seeded.seed.bucket,
            seeded.seed.object_key,
            seeded.seed.object_version_id,
        )
        observed_requirement: WormRequirement | None = None

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=True,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal observed_requirement
            assert observed == locator
            observed_requirement = requirement
            return _verified(
                locator,
                requirement,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)
        pack_connection = owner_engine.connect()
        pack_transaction = pack_connection.begin()
        pack_connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(:namespace,hashtext(:org))"),
            {"namespace": 0x4553504B, "org": str(seeded.seed.org_id)},
        )
        try:
            async with _session(database_authority_dsns) as session:
                application_name = f"t4-existing-pack-first-{uuid.uuid4().hex[:8]}"
                await session.execute(
                    sa.text("SELECT set_config('application_name',:name,false)"),
                    {"name": application_name},
                )
                pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
                transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
                protection = create_task(
                    subject.protect_existing_owner(
                        session,
                        owner=_proposed_record(subject, seeded),
                    )
                )
                assert await to_thread(
                    _wait_for_named_lock,
                    owner_engine,
                    application_name,
                    timeout_seconds=5,
                )
                with owner_engine.connect() as row_probe:
                    probe_transaction = row_probe.begin()
                    row_probe.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                    assert (
                        row_probe.execute(
                            sa.text("SELECT sha256 FROM blob WHERE sha256=:sha FOR UPDATE"),
                            {"sha": seeded.blob_sha256},
                        ).scalar_one()
                        == seeded.blob_sha256
                    )
                    probe_transaction.rollback()
                pack_transaction.rollback()
                result = await wait_for(protection, timeout=6)
                assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
                assert session.in_transaction()
                with owner_engine.connect() as monitor:
                    held_advisory = _advisory_locks(monitor, pid)
                    pack_key = int(
                        monitor.execute(
                            sa.text("SELECT hashtext(:org)"),
                            {"org": str(seeded.seed.org_id)},
                        ).scalar_one()
                    )
                exact_key = records_repo.exact_version_lock_key(locator)
                assert (
                    0x4553504B,
                    _unsigned(pack_key),
                    "ShareLock",
                ) in held_advisory
                assert (
                    exact_key[0],
                    _unsigned(exact_key[1]),
                    "ExclusiveLock",
                ) in held_advisory

                locked_rows = (
                    (
                        "blob",
                        "SELECT sha256 FROM blob WHERE sha256=:sha FOR UPDATE",
                        {"sha": seeded.blob_sha256},
                    ),
                    (
                        "current-evidence",
                        "SELECT id FROM evidence_blob WHERE id=:id FOR UPDATE",
                        {"id": current_evidence_id},
                    ),
                    (
                        "current-record",
                        "SELECT id FROM record WHERE id=:id FOR UPDATE",
                        {"id": current_record_id},
                    ),
                    (
                        "current-policy",
                        "SELECT id FROM retention_policy WHERE id=:id FOR UPDATE",
                        {"id": current_policy_id},
                    ),
                    (
                        "proposed-record",
                        "SELECT id FROM record WHERE id=:id FOR UPDATE",
                        {"id": seeded.record_id},
                    ),
                    (
                        "proposed-policy",
                        "SELECT id FROM retention_policy WHERE id=:id FOR UPDATE",
                        {"id": proposed_policy_id},
                    ),
                )
                with ThreadPoolExecutor(max_workers=len(locked_rows)) as executor:
                    futures = {}
                    for label, statement, parameters in locked_rows:
                        row_application_name = f"t4-existing-{label}-{uuid.uuid4().hex[:8]}"
                        futures[row_application_name] = executor.submit(
                            _run_named_owner_update,
                            owner_engine,
                            application_name=row_application_name,
                            statement=statement,
                            parameters=parameters,
                        )
                    blocked = {
                        name: await to_thread(
                            _wait_for_named_lock,
                            owner_engine,
                            name,
                            timeout_seconds=2,
                        )
                        for name in futures
                    }
                    await session.rollback()
                    assert all(future.result(timeout=6) == "ok" for future in futures.values())
                assert all(blocked.values()), blocked
        finally:
            if pack_transaction.is_active:
                pack_transaction.rollback()
            pack_connection.close()

        assert observed_requirement is not None
        assert observed_requirement.retain_until == datetime.datetime(
            2036, 1, 15, 23, 59, 59, 999000, tzinfo=datetime.UTC
        )
        assert result.assertion.locator == locator
        assert result.owner == subject.WormOwner(
            kind=subject.WormOwnerKind.RECORD_EVIDENCE,
            owner_id=seeded.evidence_blob_id,
            org_id=seeded.seed.org_id,
            blob_sha256=seeded.blob_sha256,
            basis_date=_BASIS,
            duration=proposal_duration,
            worm_lock_period=proposal_duration,
            domain_hold=False,
            permanent=False,
        )
        assert result.authority == subject.ResolvedRecordEvidenceAuthority(
            evidence_blob_id=seeded.evidence_blob_id,
            record_id=seeded.record_id,
            retention_policy_id=proposed_policy_id,
        )
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT record_id FROM evidence_blob WHERE id=:id"),
                    {"id": current_evidence_id},
                ).scalar_one()
                == current_record_id
            )
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "proposal_kind",
    ("DOCUMENT_POLICY", "DOCUMENT_INSTALLATION_MINIMUM", "PACK"),
)
async def test_previsible_document_and_pack_proposals_return_locked_persistence_authority(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    proposal_kind: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            if proposal_kind.startswith("DOCUMENT_"):
                authority_kind = proposal_kind.removeprefix("DOCUMENT_")
                seeded = _seed_proposed_document(
                    connection,
                    authority_kind=authority_kind,
                )
                proposal = subject.ProposedDocumentSource(
                    owner_id=seeded.document_version_id,
                    document_id=seeded.document_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                    authority_kind=seeded.authority_kind,
                    authority_id=seeded.authority_id,
                )
                expected_authority = None
                expected_owner = None
                blob_sha256 = seeded.blob_sha256
                missing_statement = "SELECT EXISTS (SELECT 1 FROM document_version WHERE id=:id)"
                missing_id = seeded.document_version_id
                authority_statement = (
                    "SELECT id FROM retention_policy WHERE id=:id FOR UPDATE"
                    if authority_kind == "POLICY"
                    else "SELECT id FROM document_worm_config WHERE id=:id FOR UPDATE"
                )
                locked_rows = (
                    (
                        "document",
                        "SELECT id FROM documented_information WHERE id=:id FOR UPDATE",
                        {"id": seeded.document_id},
                    ),
                    (
                        "document-type",
                        "SELECT id FROM document_type WHERE id=:id FOR UPDATE",
                        {"id": seeded.document_type_id},
                    ),
                    (
                        "document-authority",
                        authority_statement,
                        {"id": seeded.authority_id},
                    ),
                )
            else:
                seeded = _seed_proposed_pack(connection)
                proposal = subject.ProposedSealedPack(
                    owner_id=seeded.evidence_pack_id,
                    pack_record_id=seeded.pack_record_id,
                    evidence_blob_id=seeded.evidence_blob_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
                expected_authority = subject.ResolvedSealedPackAuthority(
                    evidence_pack_id=seeded.evidence_pack_id,
                    pack_record_id=seeded.pack_record_id,
                    evidence_blob_id=seeded.evidence_blob_id,
                    retention_policy_id=seeded.retention_policy_id,
                )
                expected_owner = subject.WormOwner(
                    kind=subject.WormOwnerKind.SEALED_PACK,
                    owner_id=seeded.evidence_pack_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                    basis_date=_BASIS,
                    duration="PERMANENT",
                    worm_lock_period="PERMANENT",
                    domain_hold=False,
                    permanent=True,
                )
                blob_sha256 = seeded.blob_sha256
                missing_statement = "SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"
                missing_id = seeded.evidence_blob_id
                locked_rows = (
                    (
                        "pack",
                        "SELECT id FROM evidence_pack WHERE id=:id FOR UPDATE",
                        {"id": seeded.evidence_pack_id},
                    ),
                    (
                        "pack-record",
                        "SELECT id FROM record WHERE id=:id FOR UPDATE",
                        {"id": seeded.pack_record_id},
                    ),
                    (
                        "pack-policy",
                        "SELECT id FROM retention_policy WHERE id=:id FOR UPDATE",
                        {"id": seeded.retention_policy_id},
                    ),
                )
        target_bucket = "documents" if proposal_kind.startswith("DOCUMENT_") else "records"
        promotion = _promotion_for_sha(
            blob_sha256,
            bucket=target_bucket,
            key=blob_sha256,
            version=f"previsible-{proposal_kind.lower()}",
        )
        locator = WormObjectLocator(
            promotion.target_bucket,
            promotion.target_key,
            promotion.target_version_id,
        )
        with owner_engine.connect() as connection:
            pack_key = int(
                connection.execute(
                    sa.text("SELECT hashtext(:org)"),
                    {"org": str(seeded.seed.org_id)},
                ).scalar_one()
            )
            sha_key = int(
                connection.execute(
                    sa.text("SELECT hashtext(:sha)"),
                    {"sha": blob_sha256},
                ).scalar_one()
            )
        allocation_key = records_repo.destination_allocation_lock_key(
            promotion.target_bucket,
            promotion.target_key,
        )
        exact_key = records_repo.exact_version_lock_key(locator)
        common_pack_lock = (0x4553504B, _unsigned(pack_key), "ShareLock")
        registry_lock = (0x45534F52, _unsigned(pack_key), "ExclusiveLock")
        sha_lock = (0x45535348, _unsigned(sha_key), "ExclusiveLock")
        allocation_lock = (
            allocation_key[0],
            _unsigned(allocation_key[1]),
            "ExclusiveLock",
        )
        exact_lock = (exact_key[0], _unsigned(exact_key[1]), "ExclusiveLock")
        callback_locks: set[tuple[int, int, str]] = set()
        protection_locks: set[tuple[int, int, str]] = set()
        pid = 0

        async def promote() -> PromotionResult:
            nonlocal callback_locks
            with owner_engine.connect() as monitor:
                callback_locks = _advisory_locks(monitor, pid)
            return promotion

        async def read_worm_state(observed: WormObjectLocator) -> WormObjectState:
            assert observed == locator
            return WormObjectState(
                locator=locator,
                mode="GOVERNANCE",
                retain_until=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
                legal_hold=False,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        async def apply_worm_protection(
            observed: WormObjectLocator, requirement: WormRequirement
        ) -> VerifiedWormAssertion:
            nonlocal protection_locks
            assert observed == locator
            with owner_engine.connect() as monitor:
                protection_locks = _advisory_locks(monitor, pid)
            return _verified(
                locator,
                requirement,
                read_at=datetime.datetime.now(datetime.UTC),
            )

        monkeypatch.setattr(subject, "read_worm_state", read_worm_state)
        monkeypatch.setattr(subject, "apply_worm_protection", apply_worm_protection)
        async with _session(database_authority_dsns) as session:
            pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
            transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
            executed_sql: list[str] = []
            basis_values: list[datetime.date] = []
            if proposal_kind.startswith("DOCUMENT_"):
                original_execute = session.execute
                original_scalar = session.scalar

                class ClockResult:
                    def __init__(self, delegate: Any) -> None:
                        self.delegate = delegate

                    def scalar_one(self) -> datetime.date:
                        value = self.delegate.scalar_one()
                        assert type(value) is datetime.date
                        basis_values.append(value)
                        return value

                    def __getattr__(self, name: str) -> Any:
                        return getattr(self.delegate, name)

                async def recording_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
                    rendered = str(statement)
                    executed_sql.append(rendered)
                    result = await original_execute(statement, *args, **kwargs)
                    if "clock_timestamp" in rendered.lower():
                        return ClockResult(result)
                    return result

                async def recording_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
                    rendered = str(statement)
                    executed_sql.append(rendered)
                    value = await original_scalar(statement, *args, **kwargs)
                    if "clock_timestamp" in rendered.lower():
                        assert type(value) is datetime.date
                        basis_values.append(value)
                    return value

                monkeypatch.setattr(session, "execute", recording_execute)
                monkeypatch.setattr(session, "scalar", recording_scalar)
            result = (
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=proposal,
                            target_bucket=promotion.target_bucket,
                            target_key=promotion.target_key,
                            promote=promote,
                        )
                    ],
                )
            )[0]
            if proposal_kind.startswith("DOCUMENT_"):
                assert len(basis_values) == 1
                boundary_basis_date = basis_values[0]
                expected_authority = subject.ResolvedDocumentAuthority(
                    document_version_id=seeded.document_version_id,
                    document_id=seeded.document_id,
                    authority_kind=seeded.authority_kind,
                    authority_id=seeded.authority_id,
                    basis_date=boundary_basis_date,
                )
                expected_owner = subject.WormOwner(
                    kind=subject.WormOwnerKind.DOCUMENT_VERSION,
                    owner_id=seeded.document_version_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                    basis_date=boundary_basis_date,
                    duration="P10Y",
                    worm_lock_period=("P10Y" if seeded.authority_kind == "POLICY" else None),
                    domain_hold=False,
                    permanent=False,
                )
                normalized_sql = [" ".join(statement.lower().split()) for statement in executed_sql]
                parent_index = next(
                    index
                    for index, statement in enumerate(normalized_sql)
                    if "from documented_information document" in statement
                )
                authority_marker = (
                    "from retention_policy where id=:id for update"
                    if proposal_kind == "DOCUMENT_POLICY"
                    else "easysynq_lock_document_worm_config"
                )
                authority_index = next(
                    index
                    for index, statement in enumerate(normalized_sql)
                    if authority_marker in statement
                )
                clock_index = next(
                    index
                    for index, statement in enumerate(normalized_sql)
                    if statement == "select (clock_timestamp() at time zone 'utc')::date"
                )
                assert parent_index < authority_index < clock_index
                assert result.owner.basis_date == boundary_basis_date
                assert result.authority.basis_date == boundary_basis_date
            assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
            assert session.in_transaction()
            assert not await session.scalar(
                sa.text(missing_statement),
                {"id": missing_id},
            )
            assert not await session.scalar(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": blob_sha256},
            )
            if proposal_kind == "PACK":
                assert (
                    await session.execute(
                        sa.text(
                            "SELECT status::text,zip_blob_sha256 FROM evidence_pack WHERE id=:id"
                        ),
                        {"id": seeded.evidence_pack_id},
                    )
                ).one() == ("DRAFT", None)
            assert result.promotion == promotion
            assert result.owner == expected_owner
            assert result.authority == expected_authority
            assert result.assertion.locator == locator
            with owner_engine.connect() as monitor:
                still_held = _advisory_locks(monitor, pid)
            assert {common_pack_lock, allocation_lock, registry_lock, sha_lock} <= callback_locks
            assert exact_lock not in callback_locks
            expected_locks = {
                common_pack_lock,
                allocation_lock,
                registry_lock,
                sha_lock,
                exact_lock,
            }
            assert expected_locks <= protection_locks
            assert expected_locks <= still_held

            with ThreadPoolExecutor(max_workers=len(locked_rows)) as executor:
                futures = {}
                for label, statement, parameters in locked_rows:
                    application_name = f"t4-previsible-{label}-{uuid.uuid4().hex[:8]}"
                    futures[application_name] = executor.submit(
                        _run_named_owner_update,
                        owner_engine,
                        application_name=application_name,
                        statement=statement,
                        parameters=parameters,
                    )
                blocked = {
                    name: await to_thread(
                        _wait_for_named_lock,
                        owner_engine,
                        name,
                        timeout_seconds=2,
                    )
                    for name in futures
                }
                await session.rollback()
                assert all(future.result(timeout=6) == "ok" for future in futures.values())
            assert all(blocked.values()), blocked
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "invalid_authority",
    (
        "TENANT_RECORD_CHAIN",
        "TENANT_DOCUMENT_POLICY_CHAIN",
        "TENANT_DOCUMENT_CONFIG_CHAIN",
        "TENANT_PACK_CHAIN",
        "RECORD_EDGE_RELATION",
        "PACK_RECORD_RELATION",
        "PACK_EDGE_RELATION",
        "DOCUMENT_POLICY_PIN_MISMATCH",
        "DOCUMENT_CONFIG_PIN_MISMATCH",
        "DOCUMENT_POLICY_DANGLING",
        "DOCUMENT_CONFIG_DANGLING",
    ),
)
async def test_previsible_proposal_authority_rejects_one_isolated_invalid_relation(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    invalid_authority: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    storage_called = False
    try:
        with owner_engine.begin() as connection:
            if invalid_authority == "TENANT_RECORD_CHAIN":
                declared = _seed_proposed_record(connection)
                seeded = _seed_proposed_record(connection)
                proposal = subject.ProposedRecordEvidence(
                    owner_id=seeded.evidence_blob_id,
                    record_id=seeded.record_id,
                    org_id=declared.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
                target_bucket = "records"
            elif invalid_authority.startswith("TENANT_DOCUMENT_"):
                authority_kind = (
                    "POLICY"
                    if invalid_authority == "TENANT_DOCUMENT_POLICY_CHAIN"
                    else "INSTALLATION_MINIMUM"
                )
                declared = _seed_proposed_record(connection)
                seeded = _seed_proposed_document(connection, authority_kind=authority_kind)
                proposal = subject.ProposedDocumentSource(
                    owner_id=seeded.document_version_id,
                    document_id=seeded.document_id,
                    org_id=declared.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                    authority_kind=seeded.authority_kind,
                    authority_id=seeded.authority_id,
                )
                target_bucket = "documents"
            elif invalid_authority == "TENANT_PACK_CHAIN":
                declared = _seed_proposed_record(connection)
                seeded = _seed_proposed_pack(connection)
                proposal = subject.ProposedSealedPack(
                    owner_id=seeded.evidence_pack_id,
                    pack_record_id=seeded.pack_record_id,
                    evidence_blob_id=seeded.evidence_blob_id,
                    org_id=declared.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
                target_bucket = "records"
            elif invalid_authority == "RECORD_EDGE_RELATION":
                seeded = _seed_proposed_record(connection)
                proposal = subject.ProposedRecordEvidence(
                    owner_id=_evidence_id(connection, seeded.seed.record_id),
                    record_id=seeded.record_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
                target_bucket = "records"
            elif invalid_authority in {"PACK_RECORD_RELATION", "PACK_EDGE_RELATION"}:
                seeded = _seed_proposed_pack(connection)
                proposed_record_id = seeded.pack_record_id
                proposed_edge_id = seeded.evidence_blob_id
                if invalid_authority == "PACK_RECORD_RELATION":
                    unrelated = _add_proposed_record(
                        connection,
                        seeded.seed,
                        blob_sha256=uuid.uuid4().hex * 2,
                        duration="PERMANENT",
                    )
                    proposed_record_id = unrelated.record_id
                else:
                    unrelated_record_id = _add_owner(
                        connection,
                        seeded.seed,
                        logical_hold=False,
                        policy_duration="PERMANENT",
                    )
                    proposed_edge_id = _evidence_id(connection, unrelated_record_id)
                proposal = subject.ProposedSealedPack(
                    owner_id=seeded.evidence_pack_id,
                    pack_record_id=proposed_record_id,
                    evidence_blob_id=proposed_edge_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
                target_bucket = "records"
            else:
                seeded_kind = (
                    "POLICY"
                    if invalid_authority.startswith("DOCUMENT_POLICY_")
                    else "INSTALLATION_MINIMUM"
                )
                seeded = _seed_proposed_document(connection, authority_kind=seeded_kind)
                proposed_kind = seeded.authority_kind
                proposed_authority_id = uuid.uuid4()
                if invalid_authority == "DOCUMENT_POLICY_PIN_MISMATCH":
                    proposed_kind = "INSTALLATION_MINIMUM"
                    connection.execute(
                        sa.text(
                            "INSERT INTO document_worm_config (id,org_id,active_period) "
                            "VALUES (:id,:org,'P10Y')"
                        ),
                        {"id": proposed_authority_id, "org": seeded.seed.org_id},
                    )
                elif invalid_authority == "DOCUMENT_CONFIG_PIN_MISMATCH":
                    proposed_kind = "POLICY"
                    proposed_authority_id = seeded.seed.policy_id
                proposal = subject.ProposedDocumentSource(
                    owner_id=seeded.document_version_id,
                    document_id=seeded.document_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                    authority_kind=proposed_kind,
                    authority_id=proposed_authority_id,
                )
                target_bucket = "documents"

            proposal_sha = proposal.blob_sha256
            before = connection.execute(
                sa.text(
                    "SELECT "
                    "(SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha),"
                    "(SELECT count(*) FROM evidence_blob),"
                    "(SELECT count(*) FROM document_version),"
                    "(SELECT count(*) FROM audit_event)"
                ),
                {"sha": proposal_sha},
            ).one()

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            nonlocal storage_called
            storage_called = True
            raise AssertionError("forged proposal reached storage")

        async def forbidden_promote() -> PromotionResult:
            nonlocal storage_called
            storage_called = True
            raise AssertionError("forged proposal reached promotion")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)
        async with _session(database_authority_dsns) as session:
            with pytest.raises(subject.WormOwnerIntegrityError) as error:
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=proposal,
                            target_bucket=target_bucket,
                            target_key=proposal_sha,
                            promote=forbidden_promote,
                        )
                    ],
                )
            assert str(error.value) == "invalid WORM owner state"
            await session.flush()
            assert session.in_transaction()
            assert (
                await session.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha),"
                        "(SELECT count(*) FROM evidence_blob),"
                        "(SELECT count(*) FROM document_version),"
                        "(SELECT count(*) FROM audit_event)"
                    ),
                    {"sha": proposal_sha},
                )
            ).one() == before
            await session.rollback()
        assert not storage_called
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "invalid_existing",
    (
        "MISSING",
        "CROSS_DOMAIN",
        "CROSS_ORG",
        "PURGED",
        "WORM_FALSE",
        "BLANK_VERSION",
    ),
)
async def test_existing_version_adoption_requires_locked_complete_same_domain_blob(
    database_authority_dsns: dict[str, str], invalid_existing: str
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
            if invalid_existing != "MISSING":
                seeded = replace(seeded, blob_sha256=seeded.seed.blob_sha256)
            if invalid_existing == "CROSS_DOMAIN":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE blob SET bucket='documents',object_key=:key WHERE sha256=:sha"),
                    {
                        "sha": seeded.blob_sha256,
                        "key": f"documents/{seeded.blob_sha256}",
                    },
                )
            elif invalid_existing == "CROSS_ORG":
                other = _seed_ordinary_owner(connection)
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE blob SET org_id=:org WHERE sha256=:sha"),
                    {"org": other.org_id, "sha": seeded.blob_sha256},
                )
            elif invalid_existing == "PURGED":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE blob SET purged_at=clock_timestamp() WHERE sha256=:sha"),
                    {"sha": seeded.blob_sha256},
                )
            elif invalid_existing == "WORM_FALSE":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE blob SET worm_locked=false,worm_enforced_mode=NULL,"
                        "worm_asserted_retain_until=NULL,worm_asserted_at=NULL,"
                        "worm_retain_until=NULL,worm_retention_verified_at=NULL,"
                        "worm_legal_hold=NULL,worm_legal_hold_verified_at=NULL "
                        "WHERE sha256=:sha"
                    ),
                    {"sha": seeded.blob_sha256},
                )
            elif invalid_existing == "BLANK_VERSION":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE blob SET object_version_id=' ' WHERE sha256=:sha"),
                    {"sha": seeded.blob_sha256},
                )

        async with _session(database_authority_dsns) as session:
            with pytest.raises(subject.WormOwnerIntegrityError):
                await subject.protect_existing_owner(
                    session,
                    owner=_proposed_record(subject, seeded),
                )
            await session.rollback()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    "identity_mismatch",
    ("ORG", "SHA", "BUCKET", "KEY", "VERSION", "PURGED", "WORM_INCOMPLETE"),
)
async def test_reconciliation_requires_exact_complete_persisted_blob_identity(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    identity_mismatch: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    storage_called = False
    try:
        with owner_engine.begin() as connection:
            seed = _set_blob_domain(connection, _seed_ordinary_owner(connection))
            if identity_mismatch == "PURGED":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE blob SET purged_at=clock_timestamp() WHERE sha256=:sha"),
                    {"sha": seed.blob_sha256},
                )
            elif identity_mismatch == "WORM_INCOMPLETE":
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE blob SET worm_locked=false,worm_enforced_mode=NULL,"
                        "worm_asserted_retain_until=NULL,worm_asserted_at=NULL,"
                        "worm_retain_until=NULL,worm_retention_verified_at=NULL,"
                        "worm_legal_hold=NULL,worm_legal_hold_verified_at=NULL "
                        "WHERE sha256=:sha"
                    ),
                    {"sha": seed.blob_sha256},
                )
            before = connection.execute(
                sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()

        org_id = uuid.uuid4() if identity_mismatch == "ORG" else seed.org_id
        blob_sha256 = "f" * 64 if identity_mismatch == "SHA" else seed.blob_sha256
        locator = WormObjectLocator(
            "documents" if identity_mismatch == "BUCKET" else seed.bucket,
            "records/wrong-key" if identity_mismatch == "KEY" else seed.object_key,
            "wrong-version" if identity_mismatch == "VERSION" else seed.object_version_id,
        )

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            nonlocal storage_called
            storage_called = True
            raise AssertionError("reconciliation reached storage with unproved DB identity")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)
        async with _session(database_authority_dsns) as session:
            with pytest.raises(subject.WormOwnerIntegrityError):
                await subject.reconcile_exact_version(
                    session,
                    org_id=org_id,
                    blob_sha256=blob_sha256,
                    locator=locator,
                )
            await session.rollback()

        assert not storage_called
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                    {"sha": seed.blob_sha256},
                ).scalar_one()
                == before
            )
    finally:
        owner_engine.dispose()


async def test_reconciliation_repairs_permanent_hold_off_by_exact_persisted_locator(
    database_authority_dsns: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            seed = _set_blob_domain(
                connection,
                _seed_ordinary_owner(connection, physical_hold=False),
            )
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="PERMANENT",
            )
            _set_record_basis(connection, record_id)
            evidence_id = _evidence_id(connection, record_id)
            policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:id"),
                {"id": record_id},
            ).scalar_one()
            pack_id = _add_sealed_pack_pointer(
                connection,
                seed,
                pack_record_id=record_id,
            )
        locator = WormObjectLocator(seed.bucket, seed.object_key, seed.object_version_id)
        provider = _ExactOnlyWormClient(locator)
        monkeypatch.setattr(vault_storage, "_client", lambda **_kwargs: provider)
        pack_connection = owner_engine.connect()
        pack_transaction = pack_connection.begin()
        pack_connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(:namespace,hashtext(:org))"),
            {"namespace": 0x4553504B, "org": str(seed.org_id)},
        )
        try:
            async with _session(database_authority_dsns) as session:
                application_name = f"t4-reconcile-pack-first-{uuid.uuid4().hex[:8]}"
                await session.execute(
                    sa.text("SELECT set_config('application_name',:name,false)"),
                    {"name": application_name},
                )
                pid = int(await session.scalar(sa.text("SELECT pg_backend_pid()")))
                transaction_id = int(await session.scalar(sa.text("SELECT txid_current()")))
                reconciliation = create_task(
                    subject.reconcile_exact_version(
                        session,
                        org_id=seed.org_id,
                        blob_sha256=seed.blob_sha256,
                        locator=locator,
                    )
                )
                assert await to_thread(
                    _wait_for_named_lock,
                    owner_engine,
                    application_name,
                    timeout_seconds=5,
                )
                with owner_engine.connect() as row_probe:
                    probe_transaction = row_probe.begin()
                    row_probe.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                    assert (
                        row_probe.execute(
                            sa.text("SELECT sha256 FROM blob WHERE sha256=:sha FOR UPDATE"),
                            {"sha": seed.blob_sha256},
                        ).scalar_one()
                        == seed.blob_sha256
                    )
                    probe_transaction.rollback()
                pack_transaction.rollback()
                assertion = await wait_for(reconciliation, timeout=6)
                assert int(await session.scalar(sa.text("SELECT txid_current()"))) == transaction_id
                assert session.in_transaction()
                assert assertion.locator == locator
                assert provider.legal_hold is True
                assert not provider.listing_attempted
                assert {operation for operation, _arguments in provider.exact_calls} == {
                    "get_object_retention",
                    "get_object_legal_hold",
                    "put_object_legal_hold",
                }
                assert (
                    await session.scalar(
                        sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                        {"sha": seed.blob_sha256},
                    )
                    is True
                )
                with owner_engine.connect() as monitor:
                    held_advisory = _advisory_locks(monitor, pid)
                    pack_key = int(
                        monitor.execute(
                            sa.text("SELECT hashtext(:org)"),
                            {"org": str(seed.org_id)},
                        ).scalar_one()
                    )
                exact_key = records_repo.exact_version_lock_key(locator)
                assert (
                    0x4553504B,
                    _unsigned(pack_key),
                    "ShareLock",
                ) in held_advisory
                assert (
                    exact_key[0],
                    _unsigned(exact_key[1]),
                    "ExclusiveLock",
                ) in held_advisory

                locked_rows = (
                    (
                        "blob",
                        "SELECT sha256 FROM blob WHERE sha256=:sha FOR UPDATE",
                        {"sha": seed.blob_sha256},
                    ),
                    (
                        "evidence",
                        "SELECT id FROM evidence_blob WHERE id=:id FOR UPDATE",
                        {"id": evidence_id},
                    ),
                    (
                        "record",
                        "SELECT id FROM record WHERE id=:id FOR UPDATE",
                        {"id": record_id},
                    ),
                    (
                        "policy",
                        "SELECT id FROM retention_policy WHERE id=:id FOR UPDATE",
                        {"id": policy_id},
                    ),
                    (
                        "pack",
                        "SELECT id FROM evidence_pack WHERE id=:id FOR UPDATE",
                        {"id": pack_id},
                    ),
                )
                with ThreadPoolExecutor(max_workers=len(locked_rows)) as executor:
                    futures = {}
                    for label, statement, parameters in locked_rows:
                        row_application_name = f"t4-reconcile-{label}-{uuid.uuid4().hex[:8]}"
                        futures[row_application_name] = executor.submit(
                            _run_named_owner_update,
                            owner_engine,
                            application_name=row_application_name,
                            statement=statement,
                            parameters=parameters,
                        )
                    blocked = {
                        name: await to_thread(
                            _wait_for_named_lock,
                            owner_engine,
                            name,
                            timeout_seconds=2,
                        )
                        for name in futures
                    }
                    await session.rollback()
                    assert all(future.result(timeout=6) == "ok" for future in futures.values())
                assert all(blocked.values()), blocked
        finally:
            if pack_transaction.is_active:
                pack_transaction.rollback()
            pack_connection.close()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize("owner_family", ("RECORD", "PACK"))
@pytest.mark.parametrize(
    "destroy_authority",
    (
        "ORDINARY",
        "R27_ROOT",
        "R27_DERIVED",
        "R27_ROOT_EXECUTED",
        "R27_ROOT_FAILED",
        "R27_DERIVED_EXECUTED",
        "R27_DERIVED_FAILED",
    ),
)
async def test_destroyed_historical_authority_cannot_be_resurrected_as_proposed_owner(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    owner_family: str,
    destroy_authority: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    callback_calls = 0
    storage_calls = 0
    try:
        if destroy_authority == "ORDINARY":
            with owner_engine.begin() as connection:
                if owner_family == "RECORD":
                    seeded = _seed_proposed_record(connection)
                    _add_disposition(
                        connection,
                        seeded.seed,
                        seeded.record_id,
                        action="DESTROY",
                    )
                    connection.execute(
                        sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                        {"id": seeded.record_id},
                    )
                    proposal = _proposed_record(subject, seeded)
                else:
                    seeded = _seed_proposed_pack(connection)
                    _add_disposition(
                        connection,
                        seeded.seed,
                        seeded.pack_record_id,
                        action="DESTROY",
                    )
                    connection.execute(
                        sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                        {"id": seeded.pack_record_id},
                    )
                    proposal = subject.ProposedSealedPack(
                        owner_id=seeded.evidence_pack_id,
                        pack_record_id=seeded.pack_record_id,
                        evidence_blob_id=seeded.evidence_blob_id,
                        org_id=seeded.seed.org_id,
                        blob_sha256=seeded.blob_sha256,
                    )
        else:
            source = _seed_source_execution(database_authority_dsns, owner_engine)
            target = source.request.targets[0]
            terminal_state = next(
                (state for state in ("EXECUTED", "FAILED") if destroy_authority.endswith(state)),
                None,
            )
            historical_record_id: uuid.UUID | None = None
            if terminal_state is not None:
                historical_record_id, _historical_edge_id, _historical_execution_id = (
                    _add_terminal_historical_r27_destroy_owner(
                        database_authority_dsns,
                        owner_engine,
                        source,
                        target,
                        terminal_state=terminal_state,
                    )
                )
            with owner_engine.begin() as connection:
                if "ROOT" in destroy_authority:
                    record_id = historical_record_id or source.actors.record_id
                else:
                    record_id, _existing_edge_id = _add_r27_evidence_owner(
                        connection,
                        source,
                        target,
                        state="DISPOSED",
                    )
                    source_event_id = (
                        connection.execute(
                            sa.text(
                                "SELECT id FROM disposition_event "
                                "WHERE record_id=:record AND is_worm_destroy "
                                "ORDER BY id LIMIT 1"
                            ),
                            {"record": historical_record_id},
                        ).scalar_one()
                        if historical_record_id is not None
                        else source.disposition_event_id
                    )
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO disposition_event
                                (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                                 requested_by,is_worm_destroy,legal_basis,
                                 derived_from_disposition_event_id,r27_request_id,r27_execution_id)
                            SELECT
                                :id,event.org_id,:record,'DESTROY',true,NULL,event.approved_by,
                                event.requested_by,true,event.legal_basis,event.id,
                                event.r27_request_id,event.r27_execution_id
                            FROM disposition_event event WHERE event.id=:source_event
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "record": record_id,
                            "source_event": source_event_id,
                        },
                    )
                _set_record_basis(connection, record_id)
                if owner_family == "RECORD":
                    proposal = subject.ProposedRecordEvidence(
                        owner_id=uuid.uuid4(),
                        record_id=record_id,
                        org_id=source.actors.org_id,
                        blob_sha256=uuid.uuid4().hex * 2,
                    )
                else:
                    pack = _add_previsible_pack_for_record(
                        connection,
                        org_id=source.actors.org_id,
                        record_id=record_id,
                        created_by=source.actors.requester_id,
                    )
                    proposal = subject.ProposedSealedPack(
                        owner_id=pack.evidence_pack_id,
                        pack_record_id=pack.pack_record_id,
                        evidence_blob_id=pack.evidence_blob_id,
                        org_id=pack.seed.org_id,
                        blob_sha256=pack.blob_sha256,
                    )

        snapshot_sql = sa.text(
            "SELECT "
            "EXISTS (SELECT 1 FROM blob WHERE sha256=:sha),"
            "EXISTS (SELECT 1 FROM evidence_blob WHERE id=:owner),"
            "EXISTS (SELECT 1 FROM evidence_blob WHERE id=:edge),"
            "EXISTS (SELECT 1 FROM document_version WHERE id=:owner),"
            "(SELECT count(*) FROM audit_event)"
        )
        edge_id = (
            proposal.evidence_blob_id
            if isinstance(proposal, subject.ProposedSealedPack)
            else proposal.owner_id
        )
        parameters = {
            "sha": proposal.blob_sha256,
            "owner": proposal.owner_id,
            "edge": edge_id,
        }
        with owner_engine.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()

        async def forbidden_promote() -> PromotionResult:
            nonlocal callback_calls
            callback_calls += 1
            raise AssertionError("destroyed authority reached the storage callback")

        async def forbidden_storage(*_args: object, **_kwargs: object) -> None:
            nonlocal storage_calls
            storage_calls += 1
            raise AssertionError("destroyed authority reached exact storage protection")

        monkeypatch.setattr(subject, "read_worm_state", forbidden_storage)
        monkeypatch.setattr(subject, "apply_worm_protection", forbidden_storage)
        async with _session(database_authority_dsns) as session:
            executed_sql: list[str] = []
            original_execute = session.execute
            original_scalar = session.scalar

            async def recording_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
                executed_sql.append(str(statement))
                return await original_execute(statement, *args, **kwargs)

            async def recording_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
                executed_sql.append(str(statement))
                return await original_scalar(statement, *args, **kwargs)

            monkeypatch.setattr(session, "execute", recording_execute)
            monkeypatch.setattr(session, "scalar", recording_scalar)
            with pytest.raises(subject.WormOwnerIntegrityError) as error:
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=proposal,
                            target_bucket="records",
                            target_key=proposal.blob_sha256,
                            promote=forbidden_promote,
                        )
                    ],
                )
            assert str(error.value) == "invalid WORM owner state"
            _assert_proposal_liveness_lock_order(
                executed_sql,
                owner_family=owner_family,
            )
            await session.flush()
            assert session.in_transaction()
            assert (await session.execute(snapshot_sql, parameters)).one() == before
            await session.rollback()

        assert callback_calls == 0
        assert storage_calls == 0
        with owner_engine.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize("owner_family", ("RECORD", "PACK"))
@pytest.mark.parametrize(
    "non_destroy_control",
    ("DISPOSED_WITHOUT_EVENT", "ARCHIVE_COLD", "TRANSFER", "FORGED_R27_BINDING"),
)
async def test_noncanonical_destroy_shapes_do_not_overrestrict_proposed_owner(
    database_authority_dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    owner_family: str,
    non_destroy_control: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.begin() as connection:
            if owner_family == "RECORD":
                seeded = _seed_proposed_record(connection)
                record_id = seeded.record_id
                proposal = _proposed_record(subject, seeded)
            else:
                seeded = _seed_proposed_pack(connection)
                record_id = seeded.pack_record_id
                proposal = subject.ProposedSealedPack(
                    owner_id=seeded.evidence_pack_id,
                    pack_record_id=seeded.pack_record_id,
                    evidence_blob_id=seeded.evidence_blob_id,
                    org_id=seeded.seed.org_id,
                    blob_sha256=seeded.blob_sha256,
                )
            if non_destroy_control == "DISPOSED_WITHOUT_EVENT":
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": record_id},
                )
            elif non_destroy_control in {"ARCHIVE_COLD", "TRANSFER"}:
                _add_disposition(
                    connection,
                    seeded.seed,
                    record_id,
                    action=non_destroy_control,
                )
            else:
                forged_event = _add_disposition(
                    connection,
                    seeded.seed,
                    record_id,
                    action="DESTROY",
                )
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET is_worm_destroy=true,policy_id=NULL,"
                        "requested_by=approved_by,r27_request_id=:request,"
                        "r27_execution_id=:execution "
                        "WHERE id=:id"
                    ),
                    {
                        "id": forged_event,
                        "request": uuid.uuid4(),
                        "execution": uuid.uuid4(),
                    },
                )
            if non_destroy_control != "DISPOSED_WITHOUT_EVENT":
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": record_id},
                )

        callback_calls = 0

        async def sentinel_callback() -> PromotionResult:
            nonlocal callback_calls
            callback_calls += 1
            raise RuntimeError("proposal-liveness-control-reached-callback")

        async with _session(database_authority_dsns) as session:
            executed_sql: list[str] = []
            original_execute = session.execute
            original_scalar = session.scalar

            async def recording_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
                executed_sql.append(str(statement))
                return await original_execute(statement, *args, **kwargs)

            async def recording_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
                executed_sql.append(str(statement))
                return await original_scalar(statement, *args, **kwargs)

            monkeypatch.setattr(session, "execute", recording_execute)
            monkeypatch.setattr(session, "scalar", recording_scalar)
            with pytest.raises(
                RuntimeError,
                match="proposal-liveness-control-reached-callback",
            ):
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=proposal,
                            target_bucket="records",
                            target_key=proposal.blob_sha256,
                            promote=sentinel_callback,
                        )
                    ],
                )
            assert callback_calls == 1
            _assert_proposal_liveness_lock_order(
                executed_sql,
                owner_family=owner_family,
            )
            await session.rollback()
    finally:
        owner_engine.dispose()


@pytest.mark.parametrize(
    ("policy_column", "policy_value"),
    (
        ("duration", "P999999999999999999999Y"),
        ("worm_lock_period", "P999999999999999999999D"),
    ),
)
async def test_relational_owner_policy_overflow_refuses_bounded_before_callback(
    database_authority_dsns: dict[str, str],
    policy_column: str,
    policy_value: str,
) -> None:
    subject = _subject()
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    callback_calls = 0
    try:
        with owner_engine.begin() as connection:
            seeded = _seed_proposed_record(connection)
            policy_id = connection.execute(
                sa.text("SELECT retention_policy_id FROM record WHERE id=:record"),
                {"record": seeded.record_id},
            ).scalar_one()
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text(
                    {
                        "duration": ("UPDATE retention_policy SET duration=:value WHERE id=:id"),
                        "worm_lock_period": (
                            "UPDATE retention_policy SET worm_lock_period=:value WHERE id=:id"
                        ),
                    }[policy_column]
                ),
                {"id": policy_id, "value": policy_value},
            )

        async def forbidden_callback() -> PromotionResult:
            nonlocal callback_calls
            callback_calls += 1
            raise AssertionError("overflowing authority reached callback")

        async with _session(database_authority_dsns) as session:
            with pytest.raises(subject.WormOwnerIntegrityError) as error:
                await subject.protect_proposed_owners(
                    session,
                    proposals=[
                        subject.NewCopyProposal(
                            owner=_proposed_record(subject, seeded),
                            target_bucket="records",
                            target_key=seeded.blob_sha256,
                            promote=forbidden_callback,
                        )
                    ],
                )
            assert str(error.value) == "invalid WORM owner state"
            assert callback_calls == 0
            await session.rollback()
    finally:
        owner_engine.dispose()
