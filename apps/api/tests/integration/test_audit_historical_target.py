"""Real PostgreSQL/MinIO proof for an explicitly selected historical audit target."""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import json
import os
import re
import selectors
import socket
import socketserver
import subprocess
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.services.audit import historical as historical_service
from easysynq_api.services.audit.linker import link_all
from easysynq_api.services.audit.trust import ExternalCredentials, load_trust_descriptor
from easysynq_api.services.vault.audit import DbVaultAuditSink, VaultAuditEvent

from .test_audit_external_trust import (
    _anchor,
    _append_and_link,
    _create_sink,
    _reset_private_database,
    _retarget_sink,
    _rewrite_single_row,
)
from .test_audit_external_trust import (
    _pg as _pg,
)
from .test_audit_witness_storage import (
    _assert_denied,
    _mc_exec,
    _policy_from_shipped_heredoc,
    _provision,
    _s3,
)
from .test_audit_witness_storage import (
    _mc as _mc,
)

pytestmark = pytest.mark.integration

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_PG_DUMP = Path("/usr/bin/pg_dump")
_PG_RESTORE = Path("/usr/bin/pg_restore")
_CLI_TIMEOUT_SECONDS = 120
_POSTGRES_TOOL_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class _HistoricalReader:
    role: str
    source_dsn: str


@dataclass(frozen=True, slots=True)
class _FrozenDatabase:
    owner_dsn: str
    reader_dsn: str


class _RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = False
    block_on_close = True

    def __init__(self, upstream: tuple[str, int]) -> None:
        self.upstream = upstream
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), _RelayHandler)


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast(_RelayServer, self.server)
        try:
            upstream = socket.create_connection(server.upstream, timeout=10)
        except OSError:
            return
        with upstream, selectors.DefaultSelector() as selected:
            self.request.settimeout(10)
            upstream.settimeout(10)
            selected.register(self.request, selectors.EVENT_READ, upstream)
            selected.register(upstream, selectors.EVENT_READ, self.request)
            while not server.stopping.is_set():
                for key, _events in selected.select(timeout=0.2):
                    source = cast(socket.socket, key.fileobj)
                    destination = cast(socket.socket, key.data)
                    try:
                        chunk = source.recv(64 * 1024)
                        if not chunk:
                            return
                        destination.sendall(chunk)
                    except (OSError, TimeoutError):
                        return


@contextmanager
def _loopback_http_relay(endpoint: str) -> Iterator[str]:
    parsed = urlsplit(endpoint)
    assert parsed.scheme == "http"
    assert parsed.hostname is not None and parsed.port is not None
    assert parsed.path in {"", "/"}
    assert not parsed.query and not parsed.fragment
    server = _RelayServer((parsed.hostname, parsed.port))
    relay_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="historical-witness-loopback-relay",
    )
    relay_thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        relay_thread.join(timeout=5)
        assert not relay_thread.is_alive()


@pytest.fixture
def _witness_endpoint(_minio: dict[str, str]) -> Iterator[str]:
    with _loopback_http_relay(_minio["endpoint"]) as endpoint:
        yield endpoint


@pytest.fixture
async def _org_id(app_under_test: Any, dsns: dict[str, str]) -> AsyncIterator[uuid.UUID]:
    assert app_under_test is not None
    org_id = await _reset_private_database(dsns)
    try:
        yield org_id
    finally:
        try:
            await _drain_pending_audit_rows(dsns["linker"], org_id)
        finally:
            await _reset_private_database(dsns)


async def _drain_pending_audit_rows(linker_dsn: str, org_id: uuid.UUID) -> None:
    """Link a test's deliberate pending tail before shared reset deletes its allocated ID."""
    engine = create_async_engine(linker_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for _attempt in range(3):
                pending_id = (
                    await session.execute(
                        select(AuditEvent.id)
                        .where(
                            AuditEvent.org_id == org_id,
                            AuditEvent.chained_at.is_(None),
                        )
                        .order_by(AuditEvent.id.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if pending_id is None:
                    return
                await link_all(session)
            remaining = (
                await session.execute(
                    select(AuditEvent.id)
                    .where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.chained_at.is_(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            assert remaining is None
    finally:
        await engine.dispose()


@pytest.fixture
async def _historical_reader(
    app_under_test: Any, dsns: dict[str, str]
) -> AsyncIterator[_HistoricalReader]:
    """Create one synthetic reader with only the historical consumer's required columns."""
    assert app_under_test is not None
    role = f"historical_reader_{uuid.uuid4().hex}"
    password = f"synthetic_{uuid.uuid4().hex}"
    database = make_url(dsns["owner"]).database
    assert database is not None and _IDENTIFIER.fullmatch(database)
    assert _IDENTIFIER.fullmatch(role)
    owner = create_async_engine(dsns["owner"])
    async with owner.begin() as connection:
        await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
        await connection.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"'))
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await connection.execute(
            text(f'GRANT SELECT ON TABLE organization, audit_event, audit_checkpoint TO "{role}"')
        )
        await connection.execute(
            text(
                "GRANT SELECT (org_id, canonical_serialize_version) "
                f'ON TABLE system_config TO "{role}"'
            )
        )
    source_dsn = (
        make_url(dsns["owner"])
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    try:
        yield _HistoricalReader(role=role, source_dsn=source_dsn)
    finally:
        try:
            async with owner.begin() as connection:
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
        finally:
            await owner.dispose()


def _postgres_tool(
    executable: Path,
    arguments: list[str],
    dsn: str,
    *,
    operation: str,
) -> None:
    assert executable.is_file()
    url = make_url(dsn)
    assert url.host is not None and url.username is not None and url.database is not None
    assert url.password is not None
    command = [
        str(executable),
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        url.username,
        "--no-password",
        *arguments,
    ]
    environment = dict(os.environ)
    environment["PGPASSWORD"] = url.password
    try:
        result = subprocess.run(  # noqa: S603 - fixed PG18 binaries and UUID-owned fixture
            command,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_POSTGRES_TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(f"{operation} exceeded its finite timeout") from None
    assert result.returncode == 0, f"{operation} failed: {result.stderr[-2000:]}"


async def _database_ddl(owner_dsn: str, statement: str) -> None:
    admin_url = make_url(owner_dsn).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@asynccontextmanager
async def _frozen_database_at_a(
    owner_dsn: str,
    reader: _HistoricalReader,
    tmp_path: Path,
) -> AsyncIterator[_FrozenDatabase]:
    """Dump A and restore it into one closed, UUID-owned inspection database."""
    database = f"easysynq_hist_{uuid.uuid4().hex}"
    assert _IDENTIFIER.fullmatch(database)
    archive = tmp_path / f"{database}.dump"
    source_url = make_url(owner_dsn)
    _postgres_tool(
        _PG_DUMP,
        ["--format=custom", "--file", str(archive), "--dbname", source_url.database or ""],
        owner_dsn,
        operation="historical target dump",
    )
    assert archive.is_file() and archive.stat().st_size > 0

    created = False
    try:
        await _database_ddl(owner_dsn, f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        target_owner = make_url(owner_dsn).set(database=database)
        _postgres_tool(
            _PG_RESTORE,
            [
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--dbname",
                database,
                str(archive),
            ],
            target_owner.render_as_string(hide_password=False),
            operation="historical target restore",
        )
        await _database_ddl(owner_dsn, f'REVOKE CONNECT ON DATABASE "{database}" FROM PUBLIC')
        await _database_ddl(owner_dsn, f'GRANT CONNECT ON DATABASE "{database}" TO "{reader.role}"')
        yield _FrozenDatabase(
            owner_dsn=target_owner.render_as_string(hide_password=False),
            reader_dsn=(
                make_url(reader.source_dsn)
                .set(database=database)
                .render_as_string(hide_password=False)
            ),
        )
    finally:
        if created:
            await _database_ddl(owner_dsn, f'DROP DATABASE "{database}" WITH (FORCE)')


def _scoped_reader_policy(buckets: tuple[str, ...]) -> dict[str, Any]:
    policy = copy.deepcopy(_policy_from_shipped_heredoc("audit-sink-readonly.json"))
    assert len(policy["Statement"]) == 1
    policy["Statement"][0]["Resource"] = [
        resource
        for bucket in buckets
        for resource in (f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*")
    ]
    return policy


@contextmanager
def _restricted_witness_reader(
    mc: DockerContainer,
    _minio: dict[str, str],
    buckets: str | tuple[str, ...],
) -> Iterator[tuple[str, str, Any]]:
    scoped_buckets = (buckets,) if isinstance(buckets, str) else buckets
    assert scoped_buckets
    salt = uuid.uuid4().hex
    username = f"historical-read-{salt[:10]}"
    secret = f"synthetic-{uuid.uuid4().hex}"
    policy_name = f"historical-read-{salt[10:20]}"
    users: list[str] = []
    policies: list[str] = []
    client: Any | None = None
    try:
        _provision(
            mc,
            username=username,
            secret=secret,
            policy_name=policy_name,
            policy=_scoped_reader_policy(scoped_buckets),
            created_users=users,
            created_policies=policies,
        )
        client = _s3(_minio, username, secret)
        yield username, secret, client
    finally:
        cleanup_errors: list[Exception] = []
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - continue exact owned cleanup
                cleanup_errors.append(exc)
        for owned_user in reversed(users):
            try:
                _mc_exec(mc, ["mc", "admin", "user", "rm", "local", owned_user])
            except Exception as exc:  # noqa: BLE001 - continue exact owned cleanup
                cleanup_errors.append(exc)
        for owned_policy in reversed(policies):
            try:
                _mc_exec(mc, ["mc", "admin", "policy", "rm", "local", owned_policy])
            except Exception as exc:  # noqa: BLE001 - continue exact owned cleanup
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise cleanup_errors[0]


def _write_descriptor(
    tmp_path: Path,
    org_id: uuid.UUID,
    endpoint: str,
    bucket: str,
    signing_key: Ed25519PrivateKey,
) -> tuple[Path, uuid.UUID]:
    path, witness_ids = _write_descriptor_matrix(
        tmp_path,
        endpoint,
        ((org_id, (signing_key,), (bucket,)),),
    )
    return path, witness_ids[0][0]


def _write_descriptor_matrix(
    tmp_path: Path,
    endpoint: str,
    organizations: tuple[tuple[uuid.UUID, tuple[Ed25519PrivateKey, ...], tuple[str, ...]], ...],
) -> tuple[Path, tuple[tuple[uuid.UUID, ...], ...]]:
    parsed_endpoint = urlsplit(endpoint)
    assert parsed_endpoint.scheme == "http"
    assert parsed_endpoint.hostname in {"localhost", "127.0.0.1"}
    assert parsed_endpoint.port is not None
    assert parsed_endpoint.path in {"", "/"}
    assert not parsed_endpoint.query and not parsed_endpoint.fragment
    public_endpoint = f"http://127.0.0.1:{parsed_endpoint.port}"
    witness_ids = tuple(
        tuple(uuid.uuid4() for _bucket in buckets) for _org_id, _keys, buckets in organizations
    )
    document = {
        "format_version": 1,
        "descriptor_id": str(uuid.uuid4()),
        "organizations": [
            {
                "org_id": str(org_id),
                "public_keys": [
                    {
                        "key_id": "ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
                        "public_key": base64.b64encode(raw).decode("ascii"),
                    }
                    for signing_key in signing_keys
                    for raw in (
                        signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                    )
                ],
                "witnesses": [
                    {
                        "witness_id": str(witness_id),
                        "kind": "worm_bucket",
                        "endpoint": public_endpoint,
                        "bucket": bucket,
                        "region": "us-east-1",
                    }
                    for witness_id, bucket in zip(organization_witness_ids, buckets, strict=True)
                ],
            }
            for (org_id, signing_keys, buckets), organization_witness_ids in zip(
                organizations, witness_ids, strict=True
            )
        ],
    }
    path = (tmp_path / f"historical-trust-{uuid.uuid4()}.json").resolve()
    path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o400)
    validated = load_trust_descriptor(path)
    assert tuple(item.org_id for item in validated.organizations) == tuple(
        item[0] for item in organizations
    )
    assert (
        tuple(
            tuple(witness.witness_id for witness in item.witnesses)
            for item in validated.organizations
        )
        == witness_ids
    )
    assert all(
        witness.endpoint == public_endpoint
        for item in validated.organizations
        for witness in item.witnesses
    )
    return path, witness_ids


def _run_cli(
    descriptor: Path,
    database_url: str,
    access_key: str,
    secret_key: str,
    *,
    historical: bool,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "easysynq_api.cli.audit",
        "verify-offhost",
        "--trust-descriptor",
        str(descriptor),
    ]
    if historical:
        command.append("--historical-target")
    assert all(value not in command for value in (database_url, access_key, secret_key))
    environment = dict(os.environ)
    environment.update(
        {
            "DATABASE_URL": database_url,
            "AUDIT_SINK_READ_ACCESS_KEY": access_key,
            "AUDIT_SINK_READ_SECRET_KEY": secret_key,
        }
    )
    try:
        return subprocess.run(  # noqa: S603 - repository CLI with fixed argv and synthetic inputs
            command,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError("external audit CLI exceeded its finite timeout") from None


def _report(result: subprocess.CompletedProcess[str], *, expected_exit: int) -> dict[str, Any]:
    assert result.returncode == expected_exit, (
        f"expected external audit exit {expected_exit}, got {result.returncode}; "
        f"stdout={result.stdout[-2000:]!r}; stderr={result.stderr[-2000:]!r}"
    )
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, dict)
    assert result.stderr == ""
    return decoded


async def _assert_historical_reader_scope(dsn: str, org_id: uuid.UUID) -> None:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            observed = (
                await connection.execute(
                    select(
                        SystemConfig.org_id,
                        SystemConfig.canonical_serialize_version,
                    ).where(SystemConfig.org_id == org_id)
                )
            ).one()
            assert observed == (org_id, 1)
            await transaction.rollback()

        for statement in (
            "SELECT setup_state FROM system_config LIMIT 1",
            "UPDATE audit_event SET reason = reason WHERE false",
            "INSERT INTO system_config DEFAULT VALUES",
            "UPDATE system_config SET canonical_serialize_version = 1 WHERE false",
            "DELETE FROM system_config WHERE false",
            "TRUNCATE TABLE system_config",
        ):
            async with engine.connect() as connection:
                transaction = await connection.begin()
                with pytest.raises(DBAPIError) as exc:
                    await connection.execute(text(statement))
                assert getattr(exc.value.orig, "sqlstate", None) == "42501"
                await transaction.rollback()
    finally:
        await engine.dispose()


@asynccontextmanager
async def _without_canonical_version_read(
    owner_dsn: str, reader: _HistoricalReader
) -> AsyncIterator[None]:
    """Temporarily revoke one required column from the exact synthetic historical reader."""
    assert _IDENTIFIER.fullmatch(reader.role)
    owner = create_async_engine(owner_dsn)
    try:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "REVOKE SELECT (canonical_serialize_version) ON TABLE system_config "  # noqa: S608 - validated test role
                    f'FROM "{reader.role}"'
                )
            )
        yield
    finally:
        try:
            async with owner.begin() as connection:
                await connection.execute(
                    text(
                        "GRANT SELECT (canonical_serialize_version) ON TABLE system_config "
                        f'TO "{reader.role}"'
                    )
                )
        finally:
            await owner.dispose()


async def _linked_ids(dsn: str, org_id: uuid.UUID) -> list[int]:
    engine = create_async_engine(dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return list(
                (
                    await session.execute(
                        select(AuditEvent.id)
                        .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_not(None))
                        .order_by(AuditEvent.id)
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()


async def _append_pending(owner_dsn: str, org_id: uuid.UUID) -> int:
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            event = DbVaultAuditSink().record(
                session,
                VaultAuditEvent(
                    occurred_at=datetime.datetime.now(datetime.UTC),
                    event_type=EventType.DOCUMENT_CREATED.value,
                    actor_id="system",
                    org_id=str(org_id),
                    object_type="document",
                    object_id=str(uuid.uuid4()),
                    identifier=f"historical-pending-{uuid.uuid4().hex}",
                    reason="historical pending-row proof",
                ),
            )
            assert event is not None
            await session.flush()
            event_id = event.id
            await session.commit()
            return event_id
    finally:
        await engine.dispose()


async def _row_hash(dsn: str, event_id: int) -> bytes:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                select(AuditEvent.row_hash).where(AuditEvent.id == event_id)
            )
            assert value is not None
            return bytes(value)
    finally:
        await engine.dispose()


async def _create_secondary_organization(owner_dsn: str) -> uuid.UUID:
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            organization = Organization(
                legal_name="Historical target secondary",
                short_code=f"HIST-{uuid.uuid4().hex[:12].upper()}",
                timezone="UTC",
            )
            session.add(organization)
            await session.flush()
            org_id = organization.id
            await session.commit()
            return org_id
    finally:
        await engine.dispose()


async def _set_canonical_version(
    owner_dsn: str,
    org_id: uuid.UUID,
    version: int | None,
) -> None:
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(delete(SystemConfig).where(SystemConfig.org_id == org_id))
            if version is not None:
                session.add(
                    SystemConfig(
                        org_id=org_id,
                        canonical_serialize_version=version,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def test_historical_cli_accepts_authentic_newer_witness_for_frozen_older_target(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = f"historical-target-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    signing_key = Ed25519PrivateKey.generate()
    descriptor, witness_id = _write_descriptor(
        tmp_path, _org_id, _witness_endpoint, bucket, signing_key
    )
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        event_a = await _append_and_link(dsns, _org_id)
        checkpoint_a = await _anchor(dsns, _org_id, signing_key)
        assert checkpoint_a.latest_id == event_a.id

        async with _frozen_database_at_a(dsns["owner"], _historical_reader, tmp_path) as target:
            await _assert_historical_reader_scope(target.reader_dsn, _org_id)
            assert await _linked_ids(target.reader_dsn, _org_id) == [event_a.id]

            with _restricted_witness_reader(_mc, _minio, bucket) as (
                access_key,
                secret_key,
                witness_reader,
            ):
                _assert_denied(
                    lambda: witness_reader.put_object(
                        Bucket=bucket,
                        Key=f"checkpoints/{_org_id}/forbidden.json",
                        Body=b"forbidden",
                    )
                )
                target_at_a = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access_key,
                        secret_key,
                        historical=False,
                    ),
                    expected_exit=0,
                )
                assert target_at_a["mode"] == "external-legacy-v1"
                assert target_at_a["verified"] is True
                assert target_at_a["checked"] == 1
                assert target_at_a["pending"] == 0
                assert (
                    target_at_a["organizations"][0]["local_checkpoint"]["latest_id"] == event_a.id
                )
                assert target_at_a["organizations"][0]["witnesses"][0]["status"] == "passed"

                event_b = await _append_and_link(dsns, _org_id)
                checkpoint_b = await _anchor(dsns, _org_id, signing_key)
                assert checkpoint_b.latest_id == event_b.id
                assert event_b.id > event_a.id
                assert await _linked_ids(_historical_reader.source_dsn, _org_id) == [
                    event_a.id,
                    event_b.id,
                ]
                assert await _linked_ids(target.reader_dsn, _org_id) == [event_a.id]

                source_at_b = _report(
                    _run_cli(
                        descriptor,
                        _historical_reader.source_dsn,
                        access_key,
                        secret_key,
                        historical=False,
                    ),
                    expected_exit=0,
                )
                assert source_at_b["verified"] is True
                assert source_at_b["checked"] == 2
                assert (
                    source_at_b["organizations"][0]["local_checkpoint"]["latest_id"] == event_b.id
                )
                assert source_at_b["organizations"][0]["witnesses"][0]["status"] == "passed"

                legacy_target = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access_key,
                        secret_key,
                        historical=False,
                    ),
                    expected_exit=1,
                )
                legacy_witness = legacy_target["organizations"][0]["witnesses"][0]
                assert legacy_target["mode"] == "external-legacy-v1"
                assert legacy_target["verified"] is False
                assert legacy_target["checked"] == 1
                assert legacy_target["pending"] == 0
                assert legacy_witness["status"] == "failed"
                assert legacy_witness["attest_failures"] == 1
                assert any(
                    "missing/unchained chain row" in reason["message"]
                    for reason in legacy_witness["reasons"]
                )
                old_target_reason = next(
                    reason["message"]
                    for reason in legacy_witness["reasons"]
                    if "missing/unchained chain row" in reason["message"]
                )
                print(
                    "historical-target-red-controls="
                    + json.dumps(
                        {
                            "a_target": {
                                "mode": target_at_a["mode"],
                                "checked": target_at_a["checked"],
                                "verified": target_at_a["verified"],
                            },
                            "b_source": {
                                "mode": source_at_b["mode"],
                                "checked": source_at_b["checked"],
                                "verified": source_at_b["verified"],
                            },
                            "a_target_with_b_witness": {
                                "mode": legacy_target["mode"],
                                "checked": legacy_target["checked"],
                                "verified": legacy_target["verified"],
                                "witness_status": legacy_witness["status"],
                                "reason": old_target_reason,
                            },
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )

                historical = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access_key,
                        secret_key,
                        historical=True,
                    ),
                    expected_exit=0,
                )
                organization = historical["organizations"][0]
                witness = organization["witnesses"][0]
                assert historical["mode"] == "external-historical-legacy-v1"
                assert historical["verified"] is True
                assert historical["checked"] == 1
                assert historical["pending"] == 0
                assert organization["verified"] is True
                assert organization["historical"] == {
                    "canonical_serialize_version": 1,
                    "linked_head_id": event_a.id,
                    "covered_through_id": event_a.id,
                    "covered_rows": 1,
                    "uncovered_linked_rows": 0,
                }
                assert witness["witness_id"] == str(witness_id)
                assert witness["status"] == "passed"
                assert witness["verified"] is True
                assert witness["historical"] == {
                    "applicable_checkpoints": 1,
                    "ahead_checkpoints": 1,
                    "highest_ahead_id": event_b.id,
                    "covered_through_id": event_a.id,
                }
    finally:
        admin.close()


async def test_historical_cli_rejects_retained_pre_rewrite_anchor_with_matching_or_ahead_head(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = f"historical-rewrite-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    signing_key = Ed25519PrivateKey.generate()
    descriptor, _witness_id = _write_descriptor(
        tmp_path, _org_id, _witness_endpoint, bucket, signing_key
    )
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        event_a = await _append_and_link(dsns, _org_id)
        anchor_a = await _anchor(dsns, _org_id, signing_key)
        rewritten = await _rewrite_single_row(dsns, event_a.id)
        anchor_b = await _anchor(dsns, _org_id, signing_key)
        assert bytes(anchor_a.latest_row_hash) != rewritten
        assert bytes(anchor_b.latest_row_hash) == rewritten

        with _restricted_witness_reader(_mc, _minio, bucket) as (access, secret, _reader):
            matching_head = _report(
                _run_cli(
                    descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
            matching_witness = matching_head["organizations"][0]["witnesses"][0]
            assert matching_witness["status"] == "failed"
            assert matching_witness["historical"]["applicable_checkpoints"] == 2
            assert matching_witness["historical"]["ahead_checkpoints"] == 0
            assert matching_witness["historical"]["covered_through_id"] is None
            assert any(
                "row_hash mismatch" in reason["message"] for reason in matching_witness["reasons"]
            )

            async with _frozen_database_at_a(dsns["owner"], _historical_reader, tmp_path) as target:
                event_c = await _append_and_link(dsns, _org_id)
                anchor_c = await _anchor(dsns, _org_id, signing_key)
                assert anchor_c.latest_id == event_c.id > event_a.id
                ahead_head = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access,
                        secret,
                        historical=True,
                    ),
                    expected_exit=1,
                )
                ahead_witness = ahead_head["organizations"][0]["witnesses"][0]
                assert ahead_witness["status"] == "failed"
                assert ahead_witness["historical"]["applicable_checkpoints"] == 2
                assert ahead_witness["historical"]["ahead_checkpoints"] == 1
                assert ahead_witness["historical"]["highest_ahead_id"] == event_c.id
                assert ahead_witness["historical"]["covered_through_id"] is None
    finally:
        admin.close()


async def test_historical_cli_accepts_enrolled_key_rotation_and_rejects_bad_ahead_bodies(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = f"historical-keys-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    key_1 = Ed25519PrivateKey.generate()
    key_2 = Ed25519PrivateKey.generate()
    key_3 = Ed25519PrivateKey.generate()
    descriptor, _witness_ids = _write_descriptor_matrix(
        tmp_path,
        _witness_endpoint,
        ((_org_id, (key_1, key_2), (bucket,)),),
    )
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key_1)
        event_b = await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, key_2)
        with _restricted_witness_reader(_mc, _minio, bucket) as (access, secret, _reader):
            rotated = _report(
                _run_cli(
                    descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=0,
            )
            assert rotated["verified"] is True
            assert rotated["organizations"][0]["historical"]["linked_head_id"] == event_b.id
            assert (
                rotated["organizations"][0]["witnesses"][0]["historical"]["applicable_checkpoints"]
                == 2
            )

            async with _frozen_database_at_a(dsns["owner"], _historical_reader, tmp_path) as target:
                event_c = await _append_and_link(dsns, _org_id)
                await _anchor(dsns, _org_id, key_3)
                admin.put_object(
                    Bucket=bucket,
                    Key=f"checkpoints/{_org_id}/{event_c.id + 1}-malformed.json",
                    Body=json.dumps(
                        {"checkpoint": {}, "signature": "invalid"},
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    ContentType="application/json",
                )
                rejected = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access,
                        secret,
                        historical=True,
                    ),
                    expected_exit=1,
                )
                witness = rejected["organizations"][0]["witnesses"][0]
                assert witness["status"] == "failed"
                assert witness["verified"] is False
                assert witness["historical"]["covered_through_id"] is None
                assert any(
                    "signature invalid" in reason["message"] for reason in witness["reasons"]
                )
                assert any(
                    "malformed off-host checkpoint payload" in reason["message"]
                    for reason in witness["reasons"]
                )
    finally:
        admin.close()


async def test_historical_cli_uses_all_required_witnesses_for_actual_row_coverage(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    buckets = tuple(f"historical-required-{index}-{uuid.uuid4().hex[:16]}" for index in range(3))
    for bucket in buckets:
        admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    full_bucket, lower_bucket, diverted_bucket = buckets
    signing_key = Ed25519PrivateKey.generate()
    descriptor, witness_ids = _write_descriptor_matrix(
        tmp_path,
        _witness_endpoint,
        ((_org_id, (signing_key,), (full_bucket, lower_bucket)),),
    )
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], full_bucket)
        lower_sink = await _create_sink(dsns, _org_id, _minio["endpoint"], lower_bucket)
        event_a = await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, signing_key)
        await _retarget_sink(dsns, lower_sink, _minio["endpoint"], diverted_bucket)
        event_b = await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, signing_key)

        with _restricted_witness_reader(_mc, _minio, (full_bucket, lower_bucket)) as (
            access,
            secret,
            _reader,
        ):
            report = _report(
                _run_cli(
                    descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
            pending_id = await _append_pending(dsns["owner"], _org_id)
            assert pending_id > event_b.id
            pending_report = _report(
                _run_cli(
                    descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
        organization = report["organizations"][0]
        by_id = {witness["witness_id"]: witness for witness in organization["witnesses"]}
        full = by_id[str(witness_ids[0][0])]
        lower = by_id[str(witness_ids[0][1])]
        assert full["status"] == "passed"
        assert full["historical"]["covered_through_id"] == event_b.id
        assert lower["status"] == "incomplete"
        assert lower["historical"]["covered_through_id"] == event_a.id
        assert organization["verified"] is False
        assert organization["historical"] == {
            "canonical_serialize_version": 1,
            "linked_head_id": event_b.id,
            "covered_through_id": event_a.id,
            "covered_rows": 1,
            "uncovered_linked_rows": 1,
        }
        pending_organization = pending_report["organizations"][0]
        assert pending_report["checked"] == 2
        assert pending_report["pending"] == 1
        assert pending_organization["historical"]["linked_head_id"] == event_b.id
        assert pending_organization["historical"]["covered_through_id"] == event_a.id
        assert any(
            reason["code"] == "PENDING_ROWS_UNVERIFIED"
            for reason in pending_organization["reasons"]
        )
    finally:
        admin.close()


async def test_historical_cli_later_only_and_empty_witnesses_underclaim_target(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    old_bucket, later_bucket, empty_bucket = tuple(
        f"historical-underclaim-{index}-{uuid.uuid4().hex[:16]}" for index in range(3)
    )
    for bucket in (old_bucket, later_bucket, empty_bucket):
        admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    signing_key = Ed25519PrivateKey.generate()
    descriptor, witness_ids = _write_descriptor_matrix(
        tmp_path,
        _witness_endpoint,
        ((_org_id, (signing_key,), (old_bucket, later_bucket, empty_bucket)),),
    )
    try:
        sink = await _create_sink(dsns, _org_id, _minio["endpoint"], old_bucket)
        event_a = await _append_and_link(dsns, _org_id)
        await _anchor(dsns, _org_id, signing_key)
        async with _frozen_database_at_a(dsns["owner"], _historical_reader, tmp_path) as target:
            await _retarget_sink(dsns, sink, _minio["endpoint"], later_bucket)
            event_b = await _append_and_link(dsns, _org_id)
            await _anchor(dsns, _org_id, signing_key)
            with _restricted_witness_reader(
                _mc, _minio, (old_bucket, later_bucket, empty_bucket)
            ) as (access, secret, _reader):
                report = _report(
                    _run_cli(
                        descriptor,
                        target.reader_dsn,
                        access,
                        secret,
                        historical=True,
                    ),
                    expected_exit=1,
                )
        organization = report["organizations"][0]
        by_id = {witness["witness_id"]: witness for witness in organization["witnesses"]}
        old = by_id[str(witness_ids[0][0])]
        later = by_id[str(witness_ids[0][1])]
        empty = by_id[str(witness_ids[0][2])]
        assert old["status"] == "passed"
        assert old["historical"]["covered_through_id"] == event_a.id
        assert later["status"] == "incomplete"
        assert later["historical"]["applicable_checkpoints"] == 0
        assert later["historical"]["ahead_checkpoints"] == 1
        assert later["historical"]["highest_ahead_id"] == event_b.id
        assert empty["status"] == "incomplete"
        assert empty["sinks_read"] == 0
        assert organization["historical"]["covered_through_id"] is None
        assert organization["historical"]["covered_rows"] == 0
        assert organization["historical"]["uncovered_linked_rows"] == 1
    finally:
        admin.close()


async def test_historical_cli_keeps_per_org_metadata_and_inventory_obligations(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    buckets = tuple(f"historical-org-{index}-{uuid.uuid4().hex[:16]}" for index in range(3))
    for bucket in buckets:
        admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    bucket_1, bucket_2, absent_bucket = buckets
    key_1 = Ed25519PrivateKey.generate()
    key_2 = Ed25519PrivateKey.generate()
    absent_key = Ed25519PrivateKey.generate()
    org_2 = await _create_secondary_organization(dsns["owner"])
    absent_org = uuid.uuid4()
    try:
        missing_config_descriptor, _ids = _write_descriptor_matrix(
            tmp_path,
            _witness_endpoint,
            ((org_2, (key_2,), (bucket_2,)),),
        )
        with _restricted_witness_reader(_mc, _minio, (bucket_1, bucket_2, absent_bucket)) as (
            access,
            secret,
            _reader,
        ):
            missing_config = _report(
                _run_cli(
                    missing_config_descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
            assert missing_config["unenrolled_orgs_present"] is True
            assert missing_config["organizations"][0]["checked"] == 0
            assert any(
                reason["code"] == "CANONICAL_VERSION_MISSING"
                for reason in missing_config["organizations"][0]["reasons"]
            )

            await _set_canonical_version(dsns["owner"], org_2, 2)
            unsupported = _report(
                _run_cli(
                    missing_config_descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
            assert unsupported["organizations"][0]["checked"] == 0
            assert unsupported["organizations"][0]["historical"]["canonical_serialize_version"] == 2
            assert any(
                reason["code"] == "CANONICAL_VERSION_UNSUPPORTED"
                for reason in unsupported["organizations"][0]["reasons"]
            )

            await _set_canonical_version(dsns["owner"], org_2, 1)
            await _create_sink(dsns, _org_id, _minio["endpoint"], bucket_1)
            await _create_sink(dsns, org_2, _minio["endpoint"], bucket_2)
            first_org_1 = await _append_and_link(dsns, _org_id)
            event_org_2 = await _append_and_link(dsns, org_2)
            event_org_1 = await _append_and_link(dsns, _org_id)
            assert first_org_1.id < event_org_2.id < event_org_1.id
            await _anchor(dsns, _org_id, key_1)
            await _anchor(dsns, org_2, key_2)

            complete_descriptor, _complete_ids = _write_descriptor_matrix(
                tmp_path,
                _witness_endpoint,
                (
                    (_org_id, (key_1,), (bucket_1,)),
                    (org_2, (key_2,), (bucket_2,)),
                ),
            )
            complete = _report(
                _run_cli(
                    complete_descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=0,
            )
            assert complete["verified"] is True
            assert complete["checked"] == 3
            assert [item["org_id"] for item in complete["organizations"]] == [
                str(_org_id),
                str(org_2),
            ]
            assert [
                item["historical"]["canonical_serialize_version"]
                for item in complete["organizations"]
            ] == [1, 1]
            assert [item["historical"]["linked_head_id"] for item in complete["organizations"]] == [
                event_org_1.id,
                event_org_2.id,
            ]
            assert [item["historical"]["covered_rows"] for item in complete["organizations"]] == [
                2,
                1,
            ]

            async with _without_canonical_version_read(dsns["owner"], _historical_reader):
                denied_reader = create_async_engine(_historical_reader.source_dsn)
                try:
                    async with denied_reader.connect() as connection:
                        transaction = await connection.begin()
                        assert await connection.scalar(text("SHOW transaction_read_only")) == "off"
                        with pytest.raises(DBAPIError) as exc:
                            await connection.execute(
                                select(SystemConfig.canonical_serialize_version).limit(1)
                            )
                        assert getattr(exc.value.orig, "sqlstate", None) == "42501"
                        await transaction.rollback()
                finally:
                    await denied_reader.dispose()

                denied_metadata = _report(
                    _run_cli(
                        complete_descriptor,
                        _historical_reader.source_dsn,
                        access,
                        secret,
                        historical=True,
                    ),
                    expected_exit=1,
                )
                assert denied_metadata["mode"] == "external-historical-legacy-v1"
                assert denied_metadata["verified"] is False
                assert [item["org_id"] for item in denied_metadata["organizations"]] == [
                    str(_org_id),
                    str(org_2),
                ]
                for organization in denied_metadata["organizations"]:
                    assert organization["verified"] is False
                    assert organization["historical"] == {
                        "canonical_serialize_version": None,
                        "linked_head_id": None,
                        "covered_through_id": None,
                        "covered_rows": None,
                        "uncovered_linked_rows": None,
                    }
                    assert any(
                        reason["code"] == "DATABASE_UNAVAILABLE"
                        for reason in organization["reasons"]
                    )
                    assert any(
                        reason["code"] == "CHECK_INCOMPLETE" for reason in organization["reasons"]
                    )
                    assert len(organization["witnesses"]) == 1
                    witness = organization["witnesses"][0]
                    assert witness["attempted"] is True
                    assert witness["sinks_read"] == 1
                    assert witness["read_failed"] is False
                    assert witness["attest_failures"] == 0
                    assert witness["comparison_unavailable"] is True
                    assert witness["status"] == "incomplete"
                    assert witness["verified"] is False
                    assert witness["historical"] == {
                        "applicable_checkpoints": None,
                        "ahead_checkpoints": None,
                        "highest_ahead_id": None,
                        "covered_through_id": None,
                    }

            missing_org_descriptor, _missing_ids = _write_descriptor_matrix(
                tmp_path,
                _witness_endpoint,
                (
                    (_org_id, (key_1,), (bucket_1,)),
                    (org_2, (key_2,), (bucket_2,)),
                    (absent_org, (absent_key,), (absent_bucket,)),
                ),
            )
            missing_org = _report(
                _run_cli(
                    missing_org_descriptor,
                    _historical_reader.source_dsn,
                    access,
                    secret,
                    historical=True,
                ),
                expected_exit=1,
            )
            absent = missing_org["organizations"][2]
            assert absent["org_id"] == str(absent_org)
            assert absent["present"] is False
            assert any(reason["code"] == "MISSING_ORGANIZATION" for reason in absent["reasons"])
    finally:
        await _set_canonical_version(dsns["owner"], org_2, None)
        admin.close()


async def test_historical_controller_uses_one_snapshot_during_owner_change(
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _historical_reader: _HistoricalReader,
    _witness_endpoint: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = f"historical-snapshot-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    signing_key = Ed25519PrivateKey.generate()
    descriptor_path, _witness_id = _write_descriptor(
        tmp_path, _org_id, _witness_endpoint, bucket, signing_key
    )
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        event = await _append_and_link(dsns, _org_id)
        anchor = await _anchor(dsns, _org_id, signing_key)
        original_hash = bytes(anchor.latest_row_hash)
        async with _frozen_database_at_a(dsns["owner"], _historical_reader, tmp_path) as target:
            with _restricted_witness_reader(_mc, _minio, bucket) as (
                access,
                secret,
                _reader,
            ):
                original_scan = historical_service._scan_witness
                changed_hash = b"\xf3" * 32
                changed = False

                async def change_then_scan(*args: Any, **kwargs: Any) -> Any:
                    nonlocal changed
                    if not changed:
                        changed = True
                        owner = create_async_engine(target.owner_dsn)
                        try:
                            async with owner.begin() as connection:
                                await connection.execute(
                                    update(AuditEvent)
                                    .where(AuditEvent.id == event.id)
                                    .values(row_hash=changed_hash)
                                )
                        finally:
                            await owner.dispose()
                        assert await _row_hash(target.owner_dsn, event.id) == changed_hash
                    return await original_scan(*args, **kwargs)

                monkeypatch.setattr(historical_service, "_scan_witness", change_then_scan)
                report = await historical_service._verify_historical_external(
                    load_trust_descriptor(descriptor_path),
                    ExternalCredentials(
                        database_url=target.reader_dsn,
                        access_key=access,
                        secret_key=secret,
                    ),
                )

            assert changed is True
            assert await _row_hash(target.owner_dsn, event.id) == changed_hash
            rendered = report.to_dict()
            assert rendered["verified"] is True
            assert rendered["organizations"][0]["historical"]["linked_head_id"] == event.id
            assert (
                rendered["organizations"][0]["witnesses"][0]["historical"]["covered_through_id"]
                == event.id
            )
            assert original_hash != changed_hash
    finally:
        admin.close()
