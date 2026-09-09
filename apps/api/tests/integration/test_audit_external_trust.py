"""Real PostgreSQL/MinIO falsifiers for externally enrolled legacy trust."""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from easysynq_api.cli import audit as audit_cli
from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import CheckpointSinkKind, EventType
from easysynq_api.db.models.audit_checkpoint import AuditCheckpoint
from easysynq_api.db.models.audit_checkpoint_sink import AuditCheckpointSink
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.organization import Organization
from easysynq_api.services.audit.canonical import (
    GENESIS_HASH,
    audit_row_from_orm,
    compute_row_hash,
)
from easysynq_api.services.audit.checkpoint import anchor_checkpoint
from easysynq_api.services.audit.external import _verify_external
from easysynq_api.services.audit.linker import link_all
from easysynq_api.services.audit.trust import (
    ExternalCredentials,
    TrustDescriptor,
    TrustedLegacyKey,
    TrustedOrganization,
    TrustedWitness,
)
from easysynq_api.services.vault.audit import DbVaultAuditSink, VaultAuditEvent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _pg() -> Iterator[str]:
    with PostgresContainer(
        "postgres:18", username="test", password="test", dbname="test", driver="psycopg"
    ) as pg:
        yield pg.get_connection_url()


def _reader_dsn(owner_dsn: str, username: str, password: str) -> str:
    return (
        make_url(owner_dsn)
        .set(username=username, password=password)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
async def _dedicated_credentials(
    app_under_test: Any,
    dsns: dict[str, str],
    _minio: dict[str, str],
) -> AsyncIterator[ExternalCredentials]:
    """Create the explicit verifier's synthetic SELECT-only principal in the private database."""
    assert app_under_test is not None
    role = f"audit_reader_{uuid.uuid4().hex}"
    password = f"synthetic_{uuid.uuid4().hex}"
    owner = create_async_engine(dsns["owner"])
    async with owner.begin() as connection:
        await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
        await connection.execute(text(f'GRANT CONNECT ON DATABASE test TO "{role}"'))
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await connection.execute(
            text(f'GRANT SELECT ON TABLE organization, audit_event, audit_checkpoint TO "{role}"')
        )
    credentials = ExternalCredentials(
        database_url=_reader_dsn(dsns["owner"], role, password),
        access_key=_minio["access_key"],
        secret_key=_minio["secret_key"],
    )
    try:
        yield credentials
    finally:
        try:
            async with owner.begin() as connection:
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
        finally:
            await owner.dispose()


async def _reset_private_database(dsns: dict[str, str]) -> uuid.UUID:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            organizations = (
                (await session.execute(select(Organization).order_by(Organization.created_at)))
                .scalars()
                .all()
            )
            assert organizations
            primary = organizations[0]
            await session.execute(delete(AuditCheckpoint))
            await session.execute(delete(AuditCheckpointSink))
            await session.execute(delete(AuditEvent))
            for extra in organizations[1:]:
                await session.delete(extra)
            await session.commit()
            return primary.id
    finally:
        await owner.dispose()


@pytest.fixture
async def _org_id(app_under_test: Any, dsns: dict[str, str]) -> AsyncIterator[uuid.UUID]:
    assert app_under_test is not None
    org_id = await _reset_private_database(dsns)
    try:
        yield org_id
    finally:
        await _reset_private_database(dsns)


def _s3(_minio: dict[str, str]) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=_minio["endpoint"],
        aws_access_key_id=_minio["access_key"],
        aws_secret_access_key=_minio["secret_key"],
        region_name="us-east-1",
    )


def _create_bucket(client: Any, prefix: str) -> str:
    bucket = f"{prefix}-{uuid.uuid4().hex[:20]}"
    client.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    return bucket


async def _create_sink(
    dsns: dict[str, str], org_id: uuid.UUID, endpoint: str, bucket: str
) -> uuid.UUID:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            sink = AuditCheckpointSink(
                org_id=org_id,
                kind=CheckpointSinkKind.worm_bucket,
                connection={
                    "bucket": bucket,
                    "endpoint": endpoint,
                    "region": "us-east-1",
                    "off_host": True,
                },
                enabled=True,
            )
            session.add(sink)
            await session.flush()
            sink_id = sink.id
            await session.commit()
            return sink_id
    finally:
        await owner.dispose()


async def _retarget_sink(
    dsns: dict[str, str], sink_id: uuid.UUID, endpoint: str, bucket: str
) -> None:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            sink = (
                await session.execute(
                    select(AuditCheckpointSink).where(AuditCheckpointSink.id == sink_id)
                )
            ).scalar_one()
            sink.connection = {
                "bucket": bucket,
                "endpoint": endpoint,
                "region": "us-east-1",
                "off_host": True,
            }
            sink.last_anchored_at = None
            sink.enabled_at = datetime.datetime.now(datetime.UTC)
            await session.commit()
    finally:
        await owner.dispose()


async def _append_and_link(dsns: dict[str, str], org_id: uuid.UUID) -> AuditEvent:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            event = DbVaultAuditSink().record(
                session,
                VaultAuditEvent(
                    occurred_at=datetime.datetime.now(datetime.UTC),
                    event_type=EventType.DOCUMENT_CREATED.value,
                    actor_id="system",
                    org_id=str(org_id),
                    object_type="document",
                    object_id=str(uuid.uuid4()),
                    identifier=f"external-trust-{uuid.uuid4().hex}",
                    reason="external trust integration baseline",
                ),
            )
            assert event is not None
            await session.flush()
            event_id = event.id
            await session.commit()
    finally:
        await owner.dispose()

    linker = create_async_engine(dsns["linker"])
    try:
        async with async_sessionmaker(linker, expire_on_commit=False)() as session:
            assert (await link_all(session)).linked >= 1
    finally:
        await linker.dispose()

    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            return (
                await session.execute(select(AuditEvent).where(AuditEvent.id == event_id))
            ).scalar_one()
    finally:
        await owner.dispose()


async def _anchor(
    dsns: dict[str, str],
    org_id: uuid.UUID,
    key: Ed25519PrivateKey,
    *,
    push: bool = True,
) -> AuditCheckpoint:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            checkpoint = await anchor_checkpoint(session, org_id, signing_key=key, push=push)
            assert checkpoint is not None
            return checkpoint
    finally:
        await owner.dispose()


def _trusted_key(key: Ed25519PrivateKey) -> TrustedLegacyKey:
    public_key = key.public_key()
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return TrustedLegacyKey(
        key_id="ed25519-sha256:" + hashlib.sha256(raw).hexdigest(), public_key=public_key
    )


def _descriptor(
    org_id: uuid.UUID,
    endpoint: str,
    bucket: str,
    keys: tuple[Ed25519PrivateKey, ...],
) -> TrustDescriptor:
    return TrustDescriptor(
        descriptor_id=uuid.uuid4(),
        sha256=hashlib.sha256(f"{org_id}:{bucket}".encode()).hexdigest(),
        organizations=(
            TrustedOrganization(
                org_id=org_id,
                public_keys=tuple(_trusted_key(key) for key in keys),
                witnesses=(
                    TrustedWitness(
                        witness_id=uuid.uuid4(),
                        kind="worm_bucket",
                        endpoint=endpoint,
                        bucket=bucket,
                        region="us-east-1",
                    ),
                ),
            ),
        ),
    )


def _write_local_key(key: Ed25519PrivateKey) -> None:
    path = Path(get_settings().audit_checkpoint_signing_key_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))


async def _rewrite_single_row(dsns: dict[str, str], event_id: int) -> bytes:
    owner = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            row = (
                await session.execute(select(AuditEvent).where(AuditEvent.id == event_id))
            ).scalar_one()
            row.reason = "OWNER-CONSISTENT-EXTERNAL-TRUST-REWRITE"
            row.row_hash = compute_row_hash(
                audit_row_from_orm(row), row.prev_hash or GENESIS_HASH, version=1
            )
            await session.commit()
            assert row.row_hash is not None
            return bytes(row.row_hash)
    finally:
        await owner.dispose()


async def test_db_discovery_can_forget_a_but_external_enrollment_still_fails(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    try:
        bucket_a = _create_bucket(client, "external-a")
        bucket_b = _create_bucket(client, "external-b")
        key_a = Ed25519PrivateKey.generate()
        sink_id = await _create_sink(dsns, _org_id, _minio["endpoint"], bucket_a)
        event = await _append_and_link(dsns, _org_id)
        anchor_a = await _anchor(dsns, _org_id, key_a)
        descriptor = _descriptor(_org_id, _minio["endpoint"], bucket_a, (key_a,))

        rewritten_hash = await _rewrite_single_row(dsns, event.id)
        await _retarget_sink(dsns, sink_id, _minio["endpoint"], bucket_b)
        anchor_b = await _anchor(dsns, _org_id, key_a)
        _write_local_key(key_a)

        assert bytes(anchor_a.latest_row_hash) != rewritten_hash
        assert bytes(anchor_b.latest_row_hash) == rewritten_hash
        legacy_ok, legacy_reads, legacy_reasons = await audit_cli._verify_offhost()
        assert legacy_ok is True
        assert legacy_reads == 1
        assert legacy_reasons == []

        strict = await _verify_external(descriptor, _dedicated_credentials)
        enrolled = strict.organizations[0]
        assert strict.verified is False
        assert (
            enrolled.witnesses[0].witness_id == descriptor.organizations[0].witnesses[0].witness_id
        )
        assert enrolled.witnesses[0].attempted is True
        assert enrolled.witnesses[0].status == "failed"
        assert enrolled.witnesses[0].attest_failures == 1
        assert any(
            "row_hash mismatch" in reason.message for reason in enrolled.witnesses[0].reasons
        )
        assert enrolled.local_checkpoint is not None
        assert enrolled.local_checkpoint.signature_ok is True
        assert enrolled.local_checkpoint.hash_match is True
    finally:
        client.close()


async def test_unchanged_legacy_state_passes_old_and_external_modes(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    try:
        bucket = _create_bucket(client, "external-positive")
        key = Ed25519PrivateKey.generate()
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key)
        _write_local_key(key)
        descriptor = _descriptor(_org_id, _minio["endpoint"], bucket, (key,))

        legacy_ok, legacy_reads, legacy_reasons = await audit_cli._verify_offhost()
        strict = await _verify_external(descriptor, _dedicated_credentials)

        assert legacy_ok is True
        assert legacy_reads == 1
        assert legacy_reasons == []
        assert strict.verified is True
        assert strict.organizations[0].verified is True
        assert strict.organizations[0].witnesses[0].status == "passed"
    finally:
        client.close()


async def test_two_enrolled_keys_ignore_substituted_local_key_files(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    try:
        bucket = _create_bucket(client, "external-multikey")
        key_a = Ed25519PrivateKey.generate()
        key_b = Ed25519PrivateKey.generate()
        substituted = Ed25519PrivateKey.generate()
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key_a)
        await _anchor(dsns, _org_id, key_b)
        _write_local_key(substituted)
        descriptor = _descriptor(_org_id, _minio["endpoint"], bucket, (key_a, key_b))

        report = await _verify_external(descriptor, _dedicated_credentials)

        assert report.verified is True
        assert report.organizations[0].local_checkpoint is not None
        assert report.organizations[0].local_checkpoint.signature_ok is True
        assert report.organizations[0].witnesses[0].verified is True
    finally:
        client.close()


async def test_missing_and_unknown_key_local_checkpoints_fail_external_mode(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    owner = create_async_engine(dsns["owner"])
    try:
        bucket = _create_bucket(client, "external-local")
        enrolled_key = Ed25519PrivateKey.generate()
        unknown_key = Ed25519PrivateKey.generate()
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, enrolled_key)
        descriptor = _descriptor(_org_id, _minio["endpoint"], bucket, (enrolled_key,))

        async with owner.begin() as connection:
            await connection.execute(
                delete(AuditCheckpoint).where(AuditCheckpoint.org_id == _org_id)
            )
        missing = await _verify_external(descriptor, _dedicated_credentials)
        assert "LOCAL_CHECKPOINT_MISSING" in {
            reason.code for reason in missing.organizations[0].reasons
        }
        assert missing.organizations[0].witnesses[0].status == "passed"
        assert missing.verified is False

        await _anchor(dsns, _org_id, unknown_key, push=False)
        unknown = await _verify_external(descriptor, _dedicated_credentials)
        assert "LOCAL_CHECKPOINT_INVALID" in {
            reason.code for reason in unknown.organizations[0].reasons
        }
        assert unknown.organizations[0].local_checkpoint is not None
        assert unknown.organizations[0].local_checkpoint.signature_ok is False
        assert unknown.organizations[0].witnesses[0].status == "passed"
        assert unknown.verified is False
    finally:
        await owner.dispose()
        client.close()


async def test_extra_database_org_fails_an_otherwise_healthy_external_check(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    owner = create_async_engine(dsns["owner"])
    try:
        enrolled_bucket = _create_bucket(client, "external-extra-org")
        key = Ed25519PrivateKey.generate()
        await _create_sink(dsns, _org_id, _minio["endpoint"], enrolled_bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key)
        descriptor = _descriptor(_org_id, _minio["endpoint"], enrolled_bucket, (key,))

        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            session.add(
                Organization(
                    id=uuid.uuid4(),
                    legal_name="Synthetic Unenrolled Organization",
                    short_code=f"EXT-{uuid.uuid4().hex[:8].upper()}",
                    timezone="UTC",
                )
            )
            await session.commit()

        report = await _verify_external(descriptor, _dedicated_credentials)

        assert report.unenrolled_orgs_present is True
        assert "UNENROLLED_ORGANIZATIONS" in {reason.code for reason in report.reasons}
        witness = report.organizations[0].witnesses[0]
        assert witness.attempted is True
        assert witness.status == "passed"
        assert report.organizations[0].local_checkpoint is not None
        assert report.organizations[0].local_checkpoint.signature_ok is True
        assert report.verified is False
    finally:
        await owner.dispose()
        client.close()


async def test_empty_enrolled_witness_fails_despite_fresh_database_grace(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    try:
        enrolled_bucket = _create_bucket(client, "external-empty-a")
        alternate_bucket = _create_bucket(client, "external-empty-b")
        key = Ed25519PrivateKey.generate()
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key, push=False)
        await _create_sink(dsns, _org_id, _minio["endpoint"], alternate_bucket)
        descriptor = _descriptor(_org_id, _minio["endpoint"], enrolled_bucket, (key,))

        report = await _verify_external(descriptor, _dedicated_credentials)

        assert report.unenrolled_orgs_present is False
        assert report.organizations[0].local_checkpoint is not None
        assert report.organizations[0].local_checkpoint.signature_ok is True
        witness = report.organizations[0].witnesses[0]
        assert witness.attempted is True
        assert witness.status == "failed"
        assert witness.sinks_read == 0
        assert "WITNESS_INVALID" in {reason.code for reason in witness.reasons}
        assert report.verified is False
    finally:
        client.close()


async def test_zero_database_orgs_cannot_remove_preserved_external_obligation(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _dedicated_credentials: ExternalCredentials,
) -> None:
    client = _s3(_minio)
    owner = create_async_engine(dsns["owner"])
    try:
        bucket = _create_bucket(client, "external-zero-org")
        key = Ed25519PrivateKey.generate()
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key)
        _write_local_key(key)
        descriptor = _descriptor(_org_id, _minio["endpoint"], bucket, (key,))

        async with owner.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE organization CASCADE"))
        legacy_ok, legacy_reads, legacy_reasons = await audit_cli._verify_offhost()
        report = await _verify_external(descriptor, _dedicated_credentials)

        assert legacy_ok is True
        assert legacy_reads == 0
        assert legacy_reasons == []
        assert report.organizations[0].org_id == _org_id
        assert report.organizations[0].present is False
        assert "MISSING_ORGANIZATION" in {reason.code for reason in report.organizations[0].reasons}
        assert report.organizations[0].witnesses[0].attempted is True
        assert report.verified is False
    finally:
        async with async_sessionmaker(owner, expire_on_commit=False)() as session:
            if await session.get(Organization, _org_id) is None:
                session.add(
                    Organization(
                        id=_org_id,
                        legal_name="EasySynQ Test Organization",
                        short_code="EASYSYNQ",
                        timezone="UTC",
                    )
                )
                await session.commit()
        await owner.dispose()
        client.close()


async def test_dedicated_reader_has_real_select_only_database_grants(
    _org_id: uuid.UUID,
    _dedicated_credentials: ExternalCredentials,
) -> None:
    engine = create_async_engine(_dedicated_credentials.database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            assert await connection.scalar(text("SHOW transaction_read_only")) == "off"
            for statement in (
                "SELECT 1 FROM organization LIMIT 1",
                "SELECT 1 FROM audit_event LIMIT 1",
                "SELECT 1 FROM audit_checkpoint LIMIT 1",
            ):
                await connection.execute(text(statement))
            await transaction.rollback()

            denied = (
                "INSERT INTO audit_event DEFAULT VALUES",
                "UPDATE audit_event SET reason = reason WHERE false",
                "DELETE FROM audit_event WHERE false",
                "TRUNCATE TABLE audit_event",
                "INSERT INTO audit_checkpoint DEFAULT VALUES",
                "UPDATE audit_checkpoint SET latest_id = latest_id WHERE false",
                "DELETE FROM audit_checkpoint WHERE false",
                "TRUNCATE TABLE audit_checkpoint",
                "UPDATE audit_checkpoint_sink SET enabled = false WHERE false",
            )
            for statement in denied:
                transaction = await connection.begin()
                assert await connection.scalar(text("SHOW transaction_read_only")) == "off"
                with pytest.raises(DBAPIError) as exc:
                    await connection.execute(text(statement))
                assert getattr(exc.value.orig, "sqlstate", None) == "42501"
                await transaction.rollback()
    finally:
        await engine.dispose()
