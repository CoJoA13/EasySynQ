"""Real storage and PostgreSQL proof for historical audit-witness verification."""

from __future__ import annotations

import dataclasses
import datetime
import json
import uuid
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from easysynq_api.db.models._audit_enums import CheckpointSinkKind, EventType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_checkpoint import AuditCheckpoint
from easysynq_api.db.models.audit_checkpoint_sink import AuditCheckpointSink
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.notification import Notification, NotificationEmail
from easysynq_api.db.models.role import RoleAssignment
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.audit.canonical import (
    GENESIS_HASH,
    audit_row_from_orm,
    compute_row_hash,
)
from easysynq_api.services.audit.checkpoint import (
    _attest_offhost_doc,
    anchor_checkpoint,
    load_signing_key,
    load_verify_key,
    verify_offhost_checkpoint,
)
from easysynq_api.services.audit.linker import link_all
from easysynq_api.services.audit.sink import fetch_latest_offhost_checkpoint
from easysynq_api.services.audit.verify import verify_chain
from easysynq_api.services.notifications.constants import EVENT_INTEGRITY_ALARM
from easysynq_api.services.notifications.ops_events import CHECK_OFFHOST_WITNESS
from easysynq_api.services.vault.audit import DbVaultAuditSink, VaultAuditEvent
from easysynq_api.tasks import audit as audit_tasks

from . import s5_helpers as s5
from .test_audit_orchestrator import (
    _audit_ids,
    _capture_alerts,
    _configure_key_paths,
    _new_audit_rows,
    _notifications_for,
    _real_verify,
    _seed_unique_admin,
)
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _pg() -> Iterator[str]:
    with PostgresContainer(
        "postgres:18", username="test", password="test", dbname="test", driver="psycopg"
    ) as pg:
        yield pg.get_connection_url()


async def _drive_to_effective(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    author = _auth(token_factory, subj.a)
    approver = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    await s5.drive_to_effective(
        app_client,
        author,
        approver,
        approver,
        type_id,
        b"historical-witness-content",
    )


async def _link_as_linker(dsns: dict[str, str]) -> int:
    engine = create_async_engine(dsns["linker"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return (await link_all(session)).linked
    finally:
        await engine.dispose()


async def _drain_linker(dsns: dict[str, str]) -> None:
    for _ in range(10):
        if await _link_as_linker(dsns) == 0:
            return
    raise AssertionError("audit linker did not reach its fixed point")


def _checkpoint_key(org_id: uuid.UUID, checkpoint: AuditCheckpoint) -> str:
    timestamp = checkpoint.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.UTC)
    return (
        f"checkpoints/{org_id}/{checkpoint.latest_id}-"
        f"{timestamp.astimezone(datetime.UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    )


def _read_body(client: Any, *, bucket: str, key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        return bytes(body.read())
    finally:
        body.close()


@dataclasses.dataclass(frozen=True, slots=True)
class _ListedVersion:
    key: str
    version_id: str
    document: dict[str, Any]
    incoming_markers: tuple[str | None, str | None]


class _OneEntryVersionPager:
    """Wrap a real S3 client and force the provider's two-marker pagination path."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.forwarded_markers: list[tuple[str | None, str | None]] = []
        self.returned_markers: list[tuple[str | None, str | None]] = []

    def scan(self, *, bucket: str, prefix: str) -> list[_ListedVersion]:
        found: list[_ListedVersion] = []
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            incoming = (key_marker, version_marker)
            self.forwarded_markers.append(incoming)
            params: dict[str, Any] = {
                "Bucket": bucket,
                "Prefix": prefix,
                "MaxKeys": 1,
            }
            if key_marker is not None:
                params["KeyMarker"] = key_marker
            if version_marker is not None:
                params["VersionIdMarker"] = version_marker
            page = self._client.list_object_versions(**params)
            for version in page.get("Versions", []):
                key = version["Key"]
                version_id = version["VersionId"]
                response = self._client.get_object(
                    Bucket=bucket,
                    Key=key,
                    VersionId=version_id,
                )
                body = response["Body"]
                try:
                    document = json.loads(body.read())
                finally:
                    body.close()
                assert isinstance(document, dict)
                found.append(_ListedVersion(key, version_id, document, incoming))
            returned = (
                page.get("NextKeyMarker"),
                page.get("NextVersionIdMarker"),
            )
            self.returned_markers.append(returned)
            if page.get("IsTruncated") is not True:
                return found
            key_marker, version_marker = returned


def _assert_document_matches(checkpoint: AuditCheckpoint, document: dict[str, Any]) -> None:
    signed = document["checkpoint"]
    assert signed["latest_id"] == checkpoint.latest_id
    assert signed["latest_row_hash"] == bytes(checkpoint.latest_row_hash).hex()
    timestamp = checkpoint.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.UTC)
    assert signed["timestamp"] == timestamp.astimezone(datetime.UTC).isoformat()


@pytest.mark.parametrize("advance_head", [False, True], ids=["same-head", "advancing-head"])
async def test_real_reanchor_preserves_historical_mismatch(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    dsns: dict[str, str],
    _minio: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    advance_head: bool,
) -> None:
    """A genuine re-anchor must not hide a retained checkpoint contradicted by a DB rewrite."""
    import boto3

    salt = uuid.uuid4().hex
    subj = SimpleNamespace(a=f"history-author-{salt}", b=f"history-approver-{salt}")
    await _drive_to_effective(app_client, token_factory, subj)
    await _drain_linker(dsns)
    org_id = await s5.default_org_id()

    admin = boto3.client(
        "s3",
        endpoint_url=_minio["endpoint"],
        aws_access_key_id=_minio["access_key"],
        aws_secret_access_key=_minio["secret_key"],
        region_name="us-east-1",
    )
    bucket = f"audit-history-{salt[:24]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)

    test_sink_id: uuid.UUID | None = None
    test_checkpoint_ids: list[uuid.UUID] = []
    head_id = 0
    original_reason: str | None = None
    original_hash = b""
    head_captured = False
    owner_engine = create_async_engine(dsns["owner"])
    try:
        async with get_sessionmaker()() as session:
            sink = AuditCheckpointSink(
                org_id=org_id,
                kind=CheckpointSinkKind.worm_bucket,
                connection={
                    "bucket": bucket,
                    "endpoint": _minio["endpoint"],
                    "region": "us-east-1",
                    "off_host": True,
                },
                enabled=True,
            )
            session.add(sink)
            await session.flush()
            test_sink_id = sink.id
            await session.commit()

        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            row = (
                (
                    await session.execute(
                        select(AuditEvent)
                        .where(
                            AuditEvent.org_id == org_id,
                            AuditEvent.chained_at.is_not(None),
                        )
                        .order_by(AuditEvent.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .one()
            )
            head_id = row.id
            original_reason = row.reason
            assert row.row_hash is not None
            original_hash = bytes(row.row_hash)
            head_captured = True

        key = load_signing_key()
        ts_a = datetime.datetime(2026, 9, 8, 12, 0, tzinfo=datetime.UTC)
        ts_b = ts_a + datetime.timedelta(minutes=15)
        monkeypatch.setattr("easysynq_api.services.audit.checkpoint._now", lambda: ts_a)
        async with get_sessionmaker()() as session:
            anchor_a = await anchor_checkpoint(session, org_id, signing_key=key)
        assert anchor_a is not None
        test_checkpoint_ids.append(anchor_a.id)
        assert anchor_a.latest_id == head_id
        assert bytes(anchor_a.latest_row_hash) == original_hash

        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            row = (
                await session.execute(select(AuditEvent).where(AuditEvent.id == head_id))
            ).scalar_one()
            row.reason = "OWNER-CONSISTENT-REWRITE"
            row.row_hash = compute_row_hash(
                audit_row_from_orm(row), row.prev_hash or GENESIS_HASH, version=1
            )
            await session.commit()

        if advance_head:
            async with get_sessionmaker()() as session:
                emitted = DbVaultAuditSink().record(
                    session,
                    VaultAuditEvent(
                        occurred_at=ts_b - datetime.timedelta(minutes=1),
                        event_type=EventType.DOCUMENT_CREATED.value,
                        actor_id="system",
                        org_id=str(org_id),
                        object_type="document",
                        object_id=str(uuid.uuid4()),
                        identifier=f"history-advance-{salt[:12]}",
                        reason="historical witness advancing-head proof",
                    ),
                )
                assert emitted is not None
                await session.commit()
            await _drain_linker(dsns)

        monkeypatch.setattr("easysynq_api.services.audit.checkpoint._now", lambda: ts_b)
        async with get_sessionmaker()() as session:
            anchor_b = await anchor_checkpoint(session, org_id, signing_key=key)
        assert anchor_b is not None
        test_checkpoint_ids.append(anchor_b.id)

        assert (anchor_b.latest_id > anchor_a.latest_id) is advance_head
        assert anchor_a.latest_row_hash != anchor_b.latest_row_hash

        async with get_sessionmaker()() as session:
            latest_local = await verify_chain(session, org_id, verify_key=key.public_key())
        assert latest_local.verified is True

        key_a = _checkpoint_key(org_id, anchor_a)
        key_b = _checkpoint_key(org_id, anchor_b)
        body_a = _read_body(admin, bucket=bucket, key=key_a)
        body_b = _read_body(admin, bucket=bucket, key=key_b)
        document_a = json.loads(body_a)
        document_b = json.loads(body_b)
        assert isinstance(document_a, dict)
        assert isinstance(document_b, dict)
        _assert_document_matches(anchor_a, document_a)
        _assert_document_matches(anchor_b, document_b)
        shadow_version = admin.put_object(Bucket=bucket, Key=key_a, Body=body_b)["VersionId"]

        pager = _OneEntryVersionPager(admin)
        versions = pager.scan(bucket=bucket, prefix=key_a)
        assert versions[0].key == key_a
        assert versions[0].version_id == shadow_version
        _assert_document_matches(anchor_b, versions[0].document)
        historical_index = next(
            index
            for index, version in enumerate(versions)
            if version.key == key_a
            and version.document["checkpoint"]["latest_row_hash"]
            == bytes(anchor_a.latest_row_hash).hex()
        )
        assert historical_index > 0
        assert versions[historical_index].version_id != shadow_version
        provider_markers = pager.returned_markers[historical_index - 1]
        assert provider_markers[0] is not None
        assert provider_markers[1] == shadow_version
        assert versions[historical_index].incoming_markers == provider_markers
        assert pager.forwarded_markers[historical_index] == provider_markers

        latest_doc = fetch_latest_offhost_checkpoint(
            "worm_bucket",
            {
                "bucket": bucket,
                "endpoint": _minio["endpoint"],
                "region": "us-east-1",
                "off_host": True,
            },
            org_id,
        )
        assert latest_doc is not None
        _assert_document_matches(anchor_b, latest_doc)
        async with get_sessionmaker()() as session:
            latest_reason = await _attest_offhost_doc(
                session,
                org_id,
                key.public_key(),
                latest_doc,
                now=ts_b,
            )
        assert latest_reason is None

        async with get_sessionmaker()() as session:
            history = await verify_offhost_checkpoint(
                session,
                org_id,
                verify_key=key.public_key(),
                now=ts_b,
            )
        assert history.sinks_read == 1
        assert history.verified is False
        assert history.attest_failures == 1
        assert any("mismatch" in reason for reason in history.reasons)
    finally:
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            if test_checkpoint_ids:
                await session.execute(
                    delete(AuditCheckpoint).where(AuditCheckpoint.id.in_(test_checkpoint_ids))
                )
            if test_sink_id is not None:
                await session.execute(
                    delete(AuditCheckpointSink).where(AuditCheckpointSink.id == test_sink_id)
                )
            if head_captured:
                await session.execute(
                    delete(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.id > head_id,
                    )
                )
                restored = (
                    await session.execute(select(AuditEvent).where(AuditEvent.id == head_id))
                ).scalar_one_or_none()
                if restored is not None:
                    restored.reason = original_reason
                    restored.row_hash = original_hash
            await session.commit()
        await owner_engine.dispose()
        admin.close()


async def test_historical_mismatch_emits_durable_orchestrator_alarm(
    app_under_test: Any,
    dsns: dict[str, str],
    _minio: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A locally consistent rewrite found only in retained A reaches every alarm channel."""
    import boto3

    assert app_under_test is not None
    _configure_key_paths(monkeypatch, tmp_path, witness_required=True)
    signing_key = load_signing_key()
    verify_key = load_verify_key()
    assert verify_key is not None
    org_id = await s5.default_org_id()
    admin_id = await _seed_unique_admin(org_id)
    alerts = _capture_alerts(monkeypatch)
    salt = uuid.uuid4().hex
    admin = boto3.client(
        "s3",
        endpoint_url=_minio["endpoint"],
        aws_access_key_id=_minio["access_key"],
        aws_secret_access_key=_minio["secret_key"],
        region_name="us-east-1",
    )
    bucket = f"audit-alarm-history-{salt[:20]}"
    sink_id: uuid.UUID | None = None
    checkpoint_ids: list[uuid.UUID] = []
    target_id: int | None = None
    owner_engine = create_async_engine(dsns["owner"])
    try:
        admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
        async with get_sessionmaker()() as session:
            sink = AuditCheckpointSink(
                org_id=org_id,
                kind=CheckpointSinkKind.worm_bucket,
                connection={
                    "bucket": bucket,
                    "endpoint": _minio["endpoint"],
                    "region": "us-east-1",
                    "off_host": True,
                },
                enabled=True,
            )
            session.add(sink)
            await session.flush()
            sink_id = sink.id
            emitted = DbVaultAuditSink().record(
                session,
                VaultAuditEvent(
                    occurred_at=datetime.datetime(2026, 9, 8, 13, 0, tzinfo=datetime.UTC),
                    event_type=EventType.DOCUMENT_CREATED.value,
                    actor_id="system",
                    org_id=str(org_id),
                    object_type="document",
                    object_id=str(uuid.uuid4()),
                    identifier=f"historical-alarm-{salt[:12]}",
                    reason="historical witness alarm proof",
                ),
            )
            assert emitted is not None
            await session.flush()
            target_id = emitted.id
            await session.commit()
        await _drain_linker(dsns)

        ts_a = datetime.datetime(2026, 9, 8, 13, 15, tzinfo=datetime.UTC)
        ts_b = ts_a + datetime.timedelta(minutes=15)
        monkeypatch.setattr("easysynq_api.services.audit.checkpoint._now", lambda: ts_a)
        async with get_sessionmaker()() as session:
            anchor_a = await anchor_checkpoint(session, org_id, signing_key=signing_key)
        assert anchor_a is not None and anchor_a.latest_id == target_id
        checkpoint_ids.append(anchor_a.id)

        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            row = (
                await session.execute(select(AuditEvent).where(AuditEvent.id == target_id))
            ).scalar_one()
            row.reason = "OWNER-CONSISTENT-HISTORICAL-ALARM-REWRITE"
            row.row_hash = compute_row_hash(
                audit_row_from_orm(row), row.prev_hash or GENESIS_HASH, version=1
            )
            await session.commit()

        monkeypatch.setattr("easysynq_api.services.audit.checkpoint._now", lambda: ts_b)
        async with get_sessionmaker()() as session:
            anchor_b = await anchor_checkpoint(session, org_id, signing_key=signing_key)
        assert anchor_b is not None and anchor_b.latest_id == target_id
        checkpoint_ids.append(anchor_b.id)
        assert anchor_a.latest_row_hash != anchor_b.latest_row_hash
        assert (await _real_verify(org_id, verify_key)).verified is True

        before_alarm = await _audit_ids(org_id)
        assert await audit_tasks._run_verify_chain() > 0
        rows = await _new_audit_rows(org_id, before_alarm)
        failures = [row for row in rows if row.event_type is EventType.CHAIN_VERIFY_FAIL]
        assert len(failures) == 1
        event = failures[0]
        assert event.after is not None
        assert any("mismatch" in reason for reason in event.after["offhost_reasons"])

        notifications = await _notifications_for(admin_id)
        assert len(notifications) == 1
        note = notifications[0]
        assert note.event_key == EVENT_INTEGRITY_ALARM
        assert note.context is not None
        assert note.context["check"] == CHECK_OFFHOST_WITNESS
        assert "mismatch" in note.context["reason_summary"]
        assert len(alerts) == 1
        assert alerts[0].event == EVENT_INTEGRITY_ALARM
        assert alerts[0].detail["check"] == CHECK_OFFHOST_WITNESS
        assert any("mismatch" in reason for reason in alerts[0].detail["reasons"])
    finally:
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            notification_ids = (
                (
                    await session.execute(
                        select(Notification.id).where(Notification.recipient_user_id == admin_id)
                    )
                )
                .scalars()
                .all()
            )
            if notification_ids:
                await session.execute(
                    delete(NotificationEmail).where(
                        NotificationEmail.notification_id.in_(notification_ids)
                    )
                )
            await session.execute(
                delete(Notification).where(Notification.recipient_user_id == admin_id)
            )
            await session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == admin_id))
            await session.execute(delete(AppUser).where(AppUser.id == admin_id))
            if checkpoint_ids:
                await session.execute(
                    delete(AuditCheckpoint).where(AuditCheckpoint.id.in_(checkpoint_ids))
                )
            if sink_id is not None:
                await session.execute(
                    delete(AuditCheckpointSink).where(AuditCheckpointSink.id == sink_id)
                )
            if target_id is not None:
                await session.execute(
                    delete(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.id >= target_id,
                    )
                )
            await session.commit()
        await owner_engine.dispose()
        admin.close()
