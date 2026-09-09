"""Built-image custody acceptance for the externally enrolled audit verifier.

This module deliberately does not start with ``test_``.  The owned repository runner invokes it
explicitly after building the exact checkout and passing an immutable image ID.
"""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID
from testcontainers.postgres import PostgresContainer

from easysynq_api.db.models.audit_checkpoint import AuditCheckpoint

from .test_audit_external_trust import (
    _anchor,
    _append_and_link,
    _create_sink,
    _reset_private_database,
    _retarget_sink,
    _rewrite_single_row,
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

_DESCRIPTOR_IN_CONTAINER = "/run/audit-external-trust.json"
_PRIVATE_KEY_IN_CONTAINER = "/run/secrets/audit_ckpt_key"
_IMAGE_PYTHON = "/app/.venv/bin/python"
_REQUIRED_ENV = {
    "DATABASE_URL",
    "AUDIT_SINK_READ_ACCESS_KEY",
    "AUDIT_SINK_READ_SECRET_KEY",
}


@pytest.fixture(scope="session", autouse=True)
def _record_owned_session() -> Iterator[None]:
    run_id = os.environ.get("EASYSYNQ_ACCEPTANCE_RUN_ID")
    record_text = os.environ.get("EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD")
    assert run_id is not None and run_id == str(uuid.UUID(run_id))
    assert record_text is not None
    record = Path(record_text)
    assert record.is_absolute()
    assert record.parent.is_dir() and not record.parent.is_symlink()
    repository_root = Path(__file__).resolve().parents[4]
    cache_parent = repository_root / ".pytest_cache"
    assert cache_parent.is_dir() and not cache_parent.is_symlink()
    assert record.parent.parent == cache_parent
    assert record.parent.name.startswith("audit-external-")
    temporary = record.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"run_id": run_id, "session_id": SESSION_ID}, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, record)
    yield


@dataclass(frozen=True, slots=True)
class _ApiImage:
    image_id: str
    environment: dict[str, str]


def _environment_map(entries: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries or []:
        name, separator, value = entry.partition("=")
        assert separator and name and name not in result
        result[name] = value
    return result


@pytest.fixture(scope="session")
def _api_image() -> _ApiImage:
    image_id = os.environ.get("EASYSYNQ_TEST_API_IMAGE")
    assert image_id is not None
    assert len(image_id) == 71 and image_id.startswith("sha256:")
    assert all(character in "0123456789abcdef" for character in image_id[7:])
    import docker

    client = docker.from_env()
    try:
        image = client.images.get(image_id)
        assert image.id == image_id
        environment = _environment_map(image.attrs["Config"].get("Env"))
        assert _REQUIRED_ENV.isdisjoint(environment)
    finally:
        client.close()
    return _ApiImage(image_id, environment)


@pytest.fixture(scope="module")
def _pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(
        "postgres:18", username="test", password="test", dbname="test", driver="psycopg"
    ) as container:
        yield container


@pytest.fixture(scope="module")
def _pg(_pg_container: PostgresContainer) -> str:
    return _pg_container.get_connection_url()


def _common_bridge_address(_pg_container: PostgresContainer, _minio: dict[str, str]) -> str:
    postgres = _pg_container.get_wrapped_container()
    postgres.reload()
    pg_networks = postgres.attrs["NetworkSettings"]["Networks"]

    import docker

    client = docker.from_env()
    try:
        minio = client.containers.get(_minio["container_id"])
        minio.reload()
        minio_networks = minio.attrs["NetworkSettings"]["Networks"]
    finally:
        client.close()
    common = sorted(set(pg_networks) & set(minio_networks))
    assert len(common) == 1
    address = pg_networks[common[0]]["IPAddress"]
    assert address
    return str(address)


@dataclass(frozen=True, slots=True)
class _DatabaseReader:
    host_dsn: str
    container_dsn: str


async def _database_ddl(owner_dsn: str, statement: str) -> None:
    engine = create_async_engine(
        make_url(owner_dsn).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@asynccontextmanager
async def _frozen_runtime_database(
    pg: PostgresContainer,
    owner_dsn: str,
    reader: _DatabaseReader,
) -> AsyncIterator[_DatabaseReader]:
    """Use the owned PostgreSQL 18 container's tools to freeze a closed runtime target."""
    database = f"runtime_hist_{uuid.uuid4().hex}"
    archive = f"/tmp/{database}.dump"  # noqa: S108 - UUID-owned container path, exactly cleaned
    assert database.replace("_", "").isalnum()
    source_database = make_url(owner_dsn).database
    assert source_database is not None
    container_owner = f"postgresql://test:test@127.0.0.1:5432/{source_database}"
    created = False
    try:
        dump = pg.exec(
            [
                "timeout",
                "120",
                "pg_dump",
                "--format=custom",
                "--file",
                archive,
                "--dbname",
                container_owner,
            ]
        )
        assert dump.exit_code == 0
        await _database_ddl(owner_dsn, f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        restore = pg.exec(
            [
                "timeout",
                "120",
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--dbname",
                f"postgresql://test:test@127.0.0.1:5432/{database}",
                archive,
            ]
        )
        assert restore.exit_code == 0
        username = make_url(reader.host_dsn).username
        assert username is not None and username.replace("_", "").isalnum()
        await _database_ddl(owner_dsn, f'REVOKE CONNECT ON DATABASE "{database}" FROM PUBLIC')
        await _database_ddl(
            owner_dsn,
            f'GRANT CONNECT ON DATABASE "{database}" TO "{username}"',
        )
        yield _DatabaseReader(
            host_dsn=(
                make_url(reader.host_dsn)
                .set(database=database)
                .render_as_string(hide_password=False)
            ),
            container_dsn=(
                make_url(reader.container_dsn)
                .set(database=database)
                .render_as_string(hide_password=False)
            ),
        )
    finally:
        cleanup_errors: list[Exception] = []
        if created:
            try:
                await _database_ddl(owner_dsn, f'DROP DATABASE "{database}" WITH (FORCE)')
            except Exception as exc:  # noqa: BLE001 - continue exact owned archive cleanup
                cleanup_errors.append(exc)
        try:
            removed = pg.exec(["timeout", "10", "rm", "-f", archive])
            assert removed.exit_code == 0
        except Exception as exc:  # noqa: BLE001 - report after all owned cleanup attempts
            cleanup_errors.append(exc)
        if cleanup_errors:
            raise cleanup_errors[0]


@asynccontextmanager
async def _database_reader_scope(
    dsns: dict[str, str],
    _pg_container: PostgresContainer,
    _minio: dict[str, str],
    *,
    historical: bool,
) -> AsyncIterator[_DatabaseReader]:
    role = f"runtime_reader_{uuid.uuid4().hex}"
    password = f"synthetic_{uuid.uuid4().hex}"
    owner = create_async_engine(dsns["owner"])
    async with owner.begin() as connection:
        await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
        await connection.execute(text(f'GRANT CONNECT ON DATABASE test TO "{role}"'))
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await connection.execute(
            text(f'GRANT SELECT ON TABLE organization, audit_event, audit_checkpoint TO "{role}"')
        )
        if historical:
            await connection.execute(
                text(
                    "GRANT SELECT (org_id, canonical_serialize_version) "
                    f'ON TABLE system_config TO "{role}"'
                )
            )
    host_url = make_url(dsns["owner"]).set(username=role, password=password)
    internal_url = host_url.set(host=_common_bridge_address(_pg_container, _minio), port=5432)
    reader = _DatabaseReader(
        host_url.render_as_string(hide_password=False),
        internal_url.render_as_string(hide_password=False),
    )
    try:
        yield reader
    finally:
        try:
            async with owner.begin() as connection:
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
        finally:
            await owner.dispose()


@pytest.fixture
async def _database_reader(
    app_under_test: Any,
    dsns: dict[str, str],
    _pg_container: PostgresContainer,
    _minio: dict[str, str],
) -> AsyncIterator[_DatabaseReader]:
    assert app_under_test is not None
    async with _database_reader_scope(dsns, _pg_container, _minio, historical=False) as reader:
        yield reader


@pytest.fixture
async def _historical_database_reader(
    app_under_test: Any,
    dsns: dict[str, str],
    _pg_container: PostgresContainer,
    _minio: dict[str, str],
) -> AsyncIterator[_DatabaseReader]:
    assert app_under_test is not None
    async with _database_reader_scope(dsns, _pg_container, _minio, historical=True) as reader:
        yield reader


@pytest.fixture
async def _org_id(app_under_test: Any, dsns: dict[str, str]) -> AsyncIterator[uuid.UUID]:
    assert app_under_test is not None
    org_id = await _reset_private_database(dsns)
    try:
        yield org_id
    finally:
        await _reset_private_database(dsns)


def _create_locked_bucket(_minio: dict[str, str], prefix: str) -> tuple[Any, str]:
    client = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = f"{prefix}-{uuid.uuid4().hex[:20]}"
    client.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    return client, bucket


def _scoped_reader_policy(bucket: str) -> dict[str, Any]:
    policy = copy.deepcopy(_policy_from_shipped_heredoc("audit-sink-readonly.json"))
    assert len(policy["Statement"]) == 1
    policy["Statement"][0]["Resource"] = [
        f"arn:aws:s3:::{bucket}",
        f"arn:aws:s3:::{bucket}/*",
    ]
    return policy


@contextmanager
def _restricted_storage_reader(
    mc: DockerContainer, _minio: dict[str, str], bucket: str
) -> Iterator[tuple[str, str, Any]]:
    salt = uuid.uuid4().hex
    username = f"runtime-read-{salt[:10]}"
    secret = f"synthetic-{uuid.uuid4().hex}"
    policy_name = f"runtime-read-{salt[10:20]}"
    users: list[str] = []
    policies: list[str] = []
    client: Any | None = None
    try:
        _provision(
            mc,
            username=username,
            secret=secret,
            policy_name=policy_name,
            policy=_scoped_reader_policy(bucket),
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


def _descriptor_path(
    org_id: uuid.UUID,
    bucket: str,
    keys: tuple[Ed25519PrivateKey, ...],
) -> Path:
    record_text = os.environ.get("EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD")
    assert record_text is not None
    parent = Path(record_text).parent
    assert parent.parent == Path(__file__).resolve().parents[4] / ".pytest_cache"
    assert parent.name.startswith("audit-external-")
    descriptor = parent / f"descriptor-{uuid.uuid4()}.json"
    public_keys = []
    for key in keys:
        raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_keys.append(
            {
                "key_id": "ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
                "public_key": base64.b64encode(raw).decode("ascii"),
            }
        )
    document = {
        "format_version": 1,
        "descriptor_id": str(uuid.uuid4()),
        "organizations": [
            {
                "org_id": str(org_id),
                "public_keys": public_keys,
                "witnesses": [
                    {
                        "witness_id": str(uuid.uuid4()),
                        "kind": "worm_bucket",
                        "endpoint": "http://127.0.0.1:9000",
                        "bucket": bucket,
                        "region": "us-east-1",
                    }
                ],
            }
        ],
    }
    descriptor.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    descriptor.chmod(0o444)
    return descriptor


def _assert_storage_denials(reader: Any, *, bucket: str, key: str, version_id: str) -> None:
    now = datetime.datetime.now(datetime.UTC)
    _assert_denied(lambda: reader.put_object(Bucket=bucket, Key=key, Body=b"forbidden"))
    _assert_denied(lambda: reader.delete_object(Bucket=bucket, Key=key))
    _assert_denied(lambda: reader.delete_object(Bucket=bucket, Key=key, VersionId=version_id))
    _assert_denied(
        lambda: reader.put_object_retention(
            Bucket=bucket,
            Key=key,
            VersionId=version_id,
            Retention={
                "Mode": "GOVERNANCE",
                "RetainUntilDate": now + datetime.timedelta(days=2),
            },
        )
    )
    _assert_denied(
        lambda: reader.delete_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id,
            BypassGovernanceRetention=True,
        )
    )
    _assert_denied(
        lambda: reader.put_object_lock_configuration(
            Bucket=bucket,
            ObjectLockConfiguration={"ObjectLockEnabled": "Enabled"},
        )
    )


async def _assert_database_denials(dsn: str, *, historical: bool = False) -> None:
    engine = create_async_engine(dsn)
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
            if historical:
                await connection.execute(
                    text("SELECT org_id, canonical_serialize_version FROM system_config LIMIT 1")
                )
            await transaction.rollback()
            denied = [
                "INSERT INTO audit_event DEFAULT VALUES",
                "UPDATE audit_event SET reason = reason WHERE false",
                "DELETE FROM audit_event WHERE false",
                "TRUNCATE TABLE audit_event",
                "INSERT INTO audit_checkpoint DEFAULT VALUES",
                "UPDATE audit_checkpoint SET latest_id = latest_id WHERE false",
                "DELETE FROM audit_checkpoint WHERE false",
                "TRUNCATE TABLE audit_checkpoint",
                "UPDATE audit_checkpoint_sink SET enabled = false WHERE false",
            ]
            if historical:
                denied.extend(
                    (
                        "SELECT setup_state FROM system_config LIMIT 1",
                        "INSERT INTO system_config DEFAULT VALUES",
                        "UPDATE system_config SET canonical_serialize_version = 1 WHERE false",
                        "DELETE FROM system_config WHERE false",
                        "TRUNCATE TABLE system_config",
                    )
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


def _exec_ok(container: DockerContainer, argv: list[str]) -> bytes:
    result = container.exec(argv)
    assert result.exit_code == 0
    return result.output


def _decode_report(
    output: bytes,
    *,
    expected_exit: int,
    actual_exit: int,
    descriptor: Path,
    forbidden: tuple[str, ...],
) -> dict[str, Any]:
    decoded = output.decode("utf-8")
    assert actual_exit == expected_exit
    report = json.loads(decoded)
    assert isinstance(report, dict)
    assert report["descriptor_sha256"] == hashlib.sha256(descriptor.read_bytes()).hexdigest()
    assert all(value not in decoded for value in forbidden)
    return report


def _public_cli_command(*, historical: bool = False) -> list[str]:
    command = [
        _IMAGE_PYTHON,
        "-m",
        "easysynq_api.cli.audit",
        "verify-offhost",
        "--trust-descriptor",
        _DESCRIPTOR_IN_CONTAINER,
    ]
    if historical:
        command.append("--historical-target")
    return command


def _run_external_cli(
    image: _ApiImage,
    _minio: dict[str, str],
    descriptor: Path,
    database_url: str,
    access_key: str,
    secret_key: str,
    *,
    expected_exit: int,
    historical: bool = False,
    hostile_environment_cases: tuple[tuple[str, dict[str, str]], ...] = (),
) -> dict[str, Any]:
    run_id = os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]
    supplied_env = {
        "DATABASE_URL": database_url,
        "AUDIT_SINK_READ_ACCESS_KEY": access_key,
        "AUDIT_SINK_READ_SECRET_KEY": secret_key,
    }
    verifier = DockerContainer(
        image.image_id,
        command=["sleep", "300"],
        volumes=[(str(descriptor), _DESCRIPTOR_IN_CONTAINER, "ro")],
        network_mode=f"container:{_minio['container_id']}",
        user="10001:10001",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={"com.easysynq.audit-external.run": run_id},
    )
    for name, value in supplied_env.items():
        verifier.with_env(name, value)
    with verifier:
        wrapped = verifier.get_wrapped_container()
        wrapped.reload()
        attrs = wrapped.attrs
        assert _exec_ok(verifier, ["id", "-u"]).strip() == b"10001"
        assert attrs["Image"] == image.image_id
        assert attrs["Config"]["User"] == "10001:10001"
        assert attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert attrs["HostConfig"]["NetworkMode"] == f"container:{_minio['container_id']}"
        assert attrs["Config"]["Labels"][LABEL_SESSION_ID] == SESSION_ID
        assert attrs["Config"]["Labels"]["com.easysynq.audit-external.run"] == run_id
        expected_environment = dict(image.environment)
        expected_environment.update(supplied_env)
        assert _environment_map(attrs["Config"].get("Env")) == expected_environment
        mounts = attrs["Mounts"]
        assert len(mounts) == 1
        assert mounts[0]["Type"] == "bind"
        assert mounts[0]["Source"] == str(descriptor)
        assert mounts[0]["Destination"] == _DESCRIPTOR_IN_CONTAINER
        assert mounts[0]["RW"] is False
        assert descriptor.parent == Path(os.environ["EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD"]).parent
        assert descriptor.stat().st_mode & 0o777 == 0o444

        write_probe = (
            "from pathlib import Path\n"
            f"p=Path({_DESCRIPTOR_IN_CONTAINER!r})\n"
            "try:\n p.write_bytes(b'forbidden')\n"
            "except OSError:\n raise SystemExit(0)\n"
            "raise SystemExit(1)\n"
        )
        _exec_ok(verifier, [_IMAGE_PYTHON, "-c", write_probe])
        private_key_probe = (
            f"from pathlib import Path\nassert not Path({_PRIVATE_KEY_IN_CONTAINER!r}).exists()\n"
        )
        _exec_ok(
            verifier,
            [_IMAGE_PYTHON, "-c", private_key_probe],
        )
        command = _public_cli_command(historical=historical)
        assert all(value not in command for value in supplied_env.values())
        result = verifier.exec(ExecConfig(command=command, environment={}))
        report = _decode_report(
            result.output,
            expected_exit=expected_exit,
            actual_exit=result.exit_code,
            descriptor=descriptor,
            forbidden=(database_url, access_key, secret_key),
        )
        for case_name, hostile_environment in hostile_environment_cases:
            assert case_name
            assert all(
                value not in command
                for name, value in hostile_environment.items()
                if name in _REQUIRED_ENV
            )
            hostile_result = verifier.exec(
                ExecConfig(command=command, environment=hostile_environment)
            )
            hostile_report = _decode_report(
                hostile_result.output,
                expected_exit=expected_exit,
                actual_exit=hostile_result.exit_code,
                descriptor=descriptor,
                forbidden=(
                    database_url,
                    access_key,
                    secret_key,
                    *hostile_environment.values(),
                ),
            )
            assert hostile_report == report
        return report


def _first_retained_version(reader: Any, org_id: uuid.UUID, bucket: str) -> tuple[str, str]:
    prefix = f"checkpoints/{org_id}/"
    page = reader.list_object_versions(Bucket=bucket, Prefix=prefix)
    versions = page.get("Versions", [])
    assert versions
    key = str(versions[0]["Key"])
    version_id = str(versions[0]["VersionId"])
    response = reader.get_object(Bucket=bucket, Key=key, VersionId=version_id)
    body = response["Body"]
    try:
        assert body.read()
    finally:
        body.close()
    return key, version_id


async def test_external_cli_runtime_is_public_only_and_read_only(
    _api_image: _ApiImage,
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _database_reader: _DatabaseReader,
) -> None:
    admin, bucket = _create_locked_bucket(_minio, "runtime-positive")
    key = Ed25519PrivateKey.generate()
    descriptor: Path | None = None
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        await _append_and_link(dsns, _org_id)
        checkpoint = await _anchor(dsns, _org_id, key)
        assert isinstance(checkpoint, AuditCheckpoint)
        descriptor = _descriptor_path(_org_id, bucket, (key,))
        with _restricted_storage_reader(_mc, _minio, bucket) as (access, secret, reader):
            object_key, version_id = _first_retained_version(reader, _org_id, bucket)
            _assert_storage_denials(reader, bucket=bucket, key=object_key, version_id=version_id)
            await _assert_database_denials(_database_reader.host_dsn)
            omitted_port_url = make_url(_database_reader.container_dsn)._replace(port=None)
            assert omitted_port_url.port is None
            default_port_url = omitted_port_url.render_as_string(hide_password=False)
            report = _run_external_cli(
                _api_image,
                _minio,
                descriptor,
                _database_reader.container_dsn,
                access,
                secret,
                expected_exit=0,
                hostile_environment_cases=(
                    ("hostaddr", {"PGHOSTADDR": "127.0.0.2"}),
                    (
                        "omitted-port",
                        {"DATABASE_URL": default_port_url, "PGPORT": "65432"},
                    ),
                    (
                        "service",
                        {
                            "PGSERVICE": "missing-runtime-service",
                            "PGSERVICEFILE": _DESCRIPTOR_IN_CONTAINER,
                        },
                    ),
                ),
            )
        assert report["verified"] is True
        assert report["organizations"][0]["witnesses"][0]["status"] == "passed"
    finally:
        admin.close()
        if descriptor is not None:
            descriptor.chmod(0o600)
            descriptor.unlink(missing_ok=True)


async def test_external_cli_runtime_preserves_enrolled_obligation_after_db_selection_attack(
    _api_image: _ApiImage,
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _database_reader: _DatabaseReader,
) -> None:
    admin, bucket_a = _create_locked_bucket(_minio, "runtime-attack-a")
    bucket_b = f"runtime-attack-b-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket_b, ObjectLockEnabledForBucket=True)
    key = Ed25519PrivateKey.generate()
    descriptor: Path | None = None
    try:
        sink_id = await _create_sink(dsns, _org_id, _minio["endpoint"], bucket_a)
        event = await _append_and_link(dsns, _org_id)
        anchor_a = await _anchor(dsns, _org_id, key)
        rewritten_hash = await _rewrite_single_row(dsns, event.id)
        await _retarget_sink(dsns, sink_id, _minio["endpoint"], bucket_b)
        anchor_b = await _anchor(dsns, _org_id, key)
        assert bytes(anchor_a.latest_row_hash) != rewritten_hash
        assert bytes(anchor_b.latest_row_hash) == rewritten_hash
        descriptor = _descriptor_path(_org_id, bucket_a, (key,))

        with _restricted_storage_reader(_mc, _minio, bucket_a) as (access, secret, _reader):
            report = _run_external_cli(
                _api_image,
                _minio,
                descriptor,
                _database_reader.container_dsn,
                access,
                secret,
                expected_exit=1,
            )
        organization = report["organizations"][0]
        witness = organization["witnesses"][0]
        assert report["verified"] is False
        assert organization["local_checkpoint"]["signature_ok"] is True
        assert organization["local_checkpoint"]["hash_match"] is True
        assert witness["attempted"] is True
        assert witness["status"] == "failed"
        assert witness["attest_failures"] == 1
        assert any("row_hash mismatch" in reason["message"] for reason in witness["reasons"])
    finally:
        admin.close()
        if descriptor is not None:
            descriptor.chmod(0o600)
            descriptor.unlink(missing_ok=True)


async def test_external_cli_runtime_accepts_historical_target_with_newer_witness(
    _api_image: _ApiImage,
    _org_id: uuid.UUID,
    dsns: dict[str, str],
    _minio: dict[str, str],
    _mc: DockerContainer,
    _pg_container: PostgresContainer,
    _historical_database_reader: _DatabaseReader,
) -> None:
    admin, bucket = _create_locked_bucket(_minio, "runtime-historical")
    signing_key = Ed25519PrivateKey.generate()
    descriptor: Path | None = None
    try:
        await _create_sink(dsns, _org_id, _minio["endpoint"], bucket)
        event_a = await _append_and_link(dsns, _org_id)
        checkpoint_a = await _anchor(dsns, _org_id, signing_key)
        assert checkpoint_a.latest_id == event_a.id
        descriptor = _descriptor_path(_org_id, bucket, (signing_key,))

        async with _frozen_runtime_database(
            _pg_container, dsns["owner"], _historical_database_reader
        ) as target:
            event_b = await _append_and_link(dsns, _org_id)
            checkpoint_b = await _anchor(dsns, _org_id, signing_key)
            assert checkpoint_b.latest_id == event_b.id > event_a.id

            with _restricted_storage_reader(_mc, _minio, bucket) as (
                access,
                secret,
                reader,
            ):
                object_key, version_id = _first_retained_version(reader, _org_id, bucket)
                _assert_storage_denials(
                    reader,
                    bucket=bucket,
                    key=object_key,
                    version_id=version_id,
                )
                await _assert_database_denials(target.host_dsn, historical=True)
                live = _run_external_cli(
                    _api_image,
                    _minio,
                    descriptor,
                    target.container_dsn,
                    access,
                    secret,
                    expected_exit=1,
                )
                historical = _run_external_cli(
                    _api_image,
                    _minio,
                    descriptor,
                    target.container_dsn,
                    access,
                    secret,
                    expected_exit=0,
                    historical=True,
                )

        assert live["mode"] == "external-legacy-v1"
        assert live["verified"] is False
        assert historical["mode"] == "external-historical-legacy-v1"
        assert historical["verified"] is True
        organization = historical["organizations"][0]
        witness = organization["witnesses"][0]
        assert organization["historical"] == {
            "canonical_serialize_version": 1,
            "linked_head_id": event_a.id,
            "covered_through_id": event_a.id,
            "covered_rows": 1,
            "uncovered_linked_rows": 0,
        }
        assert witness["status"] == "passed"
        assert witness["historical"] == {
            "applicable_checkpoints": 1,
            "ahead_checkpoints": 1,
            "highest_ahead_id": event_b.id,
            "covered_through_id": event_a.id,
        }
    finally:
        admin.close()
        if descriptor is not None:
            descriptor.chmod(0o600)
            descriptor.unlink(missing_ok=True)
