"""S8b2 integration proofs — backup config + the restore-test drill mechanics (gate G-C / AC#5).

The headline finalize-gating proofs (PASS lifts G-C; FAIL blocks it) live in ``test_setup.py``
(``test_setup_finalize_requires_restore_pass`` + ``test_restore_drill_failure_blocks_finalize``).
Here: the authz on the two new endpoints, the destination writability check, and the durable-backup
+ scratch-teardown mechanics. The pg_dump/pg_restore-backed tests need postgresql-client on the
runner (CI has it; a host without it makes the drill an honest FAIL, not a 500).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.backup_policy import BackupPolicy
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services import backup as backup_service

from .test_setup import (
    _auth,
    _bootstrap,
    _bootstrap_through_storage,
    _org_id,
    _reset_uninitialized,
    _sub,
)

pytestmark = pytest.mark.integration


async def test_configure_backup_requires_permission(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """configure-backup is gated on backup.configure — a non-admin is 403; the admin writes the
    policy + a BACKUP_CONFIGURED audit row."""
    secret = await _reset_uninitialized()
    dest = tempfile.mkdtemp(prefix="easysynq-cfg-")

    h_other = _auth(token_factory, _sub("nocfg"))
    forbidden = await app_client.post(
        "/api/v1/setup/configure-backup", headers=h_other, json={"destination": dest}
    )
    assert forbidden.status_code == 403

    h = _auth(token_factory, _sub("cfg"))
    body = await _bootstrap(app_client, h, secret)
    admin_id = uuid.UUID(body["admin_user_id"])
    ok = await app_client.post(
        "/api/v1/setup/configure-backup", headers=h, json={"destination": dest}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["configured"] is True

    async with get_sessionmaker()() as s:
        policy = (await s.execute(select(BackupPolicy))).scalar_one()
        assert policy.destination == dest
        assert policy.last_restore_test_result is None  # configured ≠ verified
        configured = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.BACKUP_CONFIGURED,
                AuditEvent.actor_id == admin_id,
            )
        )
    assert configured is not None


async def test_configure_backup_rejects_unwritable_destination(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A destination that cannot be created/written is a 422 (live reachability check, doc 08 §8.1)
    — not a silent success that would later fail the nightly backup."""
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("baddest"))
    await _bootstrap(app_client, h, secret)

    # A path whose PARENT is a regular file → makedirs fails regardless of uid (robust in CI/root).
    fd, parent_file = tempfile.mkstemp(prefix="easysynq-notadir-")
    os.close(fd)
    bad_dest = os.path.join(parent_file, "backups")
    try:
        r = await app_client.post(
            "/api/v1/setup/configure-backup", headers=h, json={"destination": bad_dest}
        )
        assert r.status_code == 422
        assert r.json()["code"] == "backup_destination_unreachable"
    finally:
        os.remove(parent_file)


async def test_configure_backup_rejects_bad_cron(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("badcron"))
    await _bootstrap(app_client, h, secret)
    dest = tempfile.mkdtemp(prefix="easysynq-cron-")
    r = await app_client.post(
        "/api/v1/setup/configure-backup",
        headers=h,
        json={"destination": dest, "cron": "not a cron"},
    )
    assert r.status_code == 422


async def test_run_restore_test_requires_permission(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """run-restore-test is gated on restore.run — a non-admin is 403 (before any drill runs)."""
    await _reset_uninitialized()
    h_other = _auth(token_factory, _sub("norun"))
    r = await app_client.post("/api/v1/setup/run-restore-test", headers=h_other)
    assert r.status_code == 403


async def test_run_restore_test_requires_configured_backup(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An admin who has not configured a backup gets 409 backup_not_configured (no drill run)."""
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("nocfgrun"))
    await _bootstrap(app_client, h, secret)
    r = await app_client.post("/api/v1/setup/run-restore-test", headers=h)
    assert r.status_code == 409
    assert r.json()["code"] == "backup_not_configured"


async def test_durable_backup_writes_verified_archive(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """`easysynq backup run` (run_scheduled_backups) writes a checksum-valid archive to the
    configured destination — the durable artifact (pg_dump + blob manifest)."""
    h, _admin = await _bootstrap_through_storage(app_client, token_factory, "durable")
    dest = tempfile.mkdtemp(prefix="easysynq-durable-")
    await app_client.post("/api/v1/setup/configure-backup", headers=h, json={"destination": dest})

    out = await backup_service.run_scheduled_backups()
    assert out["backups"], out
    entry = out["backups"][0]
    assert "error" not in entry, entry
    # verified=True means build_durable_backup re-read the archive + matched its .sha256 sidecar
    # (the 'checksum verified' leg) — i.e. the file was written to the destination and round-trips.
    assert entry["verified"] is True
    assert entry["archive"].startswith(dest)
    assert entry["blobs"] >= 0  # blob count depends on prior tests in the shared session DB


async def test_drill_tears_down_scratch_namespace(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """After a PASS drill, no scratch DB lingers and the scratch-bucket prefix is emptied — the
    drill never leaves immutable/locked residue (R37) or orphaned scratch databases."""
    import boto3

    h, admin_id = await _bootstrap_through_storage(app_client, token_factory, "teardown")
    org_id = await _org_id()
    dest = tempfile.mkdtemp(prefix="easysynq-teardown-")
    await app_client.post("/api/v1/setup/configure-backup", headers=h, json={"destination": dest})

    result = await backup_service.run_restore_test(org_id, admin_id)
    assert result["result"] == "PASS", result

    settings = get_settings()
    # No scratch DB remains.
    import psycopg

    from easysynq_api.services.backup.dsn import conn_kwargs

    with (
        psycopg.connect(**conn_kwargs(settings.sync_dsn), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_easysynq_%'")
        row = cur.fetchone()
        assert row is not None and row[0] == 0

    # The scratch bucket carries no leftover objects.
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    listing = client.list_objects_v2(Bucket=settings.s3_bucket_restore_scratch)
    assert listing.get("KeyCount", 0) == 0, listing.get("Contents")
