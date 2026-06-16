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
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.backup_policy import BackupPolicy
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services import backup as backup_service

from . import s5_helpers as s5
from .test_setup import (
    _auth,
    _bootstrap,
    _bootstrap_through_storage,
    _org_id,
    _reset_uninitialized,
    _sub,
)

pytestmark = pytest.mark.integration


def _s3_client() -> object:
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )


async def _insert_backup_policy(org_id: uuid.UUID, destination: str) -> None:
    async with get_sessionmaker()() as s:
        existing = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        if existing is None:
            s.add(BackupPolicy(org_id=org_id, destination=destination, cron="0 2 * * *"))
        else:
            existing.destination = destination
        await s.commit()


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


async def test_configure_backup_rejects_wal_pitr(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """wal_pitr_enabled is a recorded forward-seam; continuous WAL/PITR is S11/v1.x (D-6) — setting
    it true is a 422, so the scope boundary is enforced rather than silently accepted."""
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("walpitr"))
    await _bootstrap(app_client, h, secret)
    dest = tempfile.mkdtemp(prefix="easysynq-wal-")
    r = await app_client.post(
        "/api/v1/setup/configure-backup",
        headers=h,
        json={"destination": dest, "wal_pitr_enabled": True},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "wal_pitr_unavailable"


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

    # The drill's transient verification archive (+ its .sha256 sidecar) is removed from the backup
    # destination — a SCHEDULED drill must not accumulate PLAINTEXT db dumps there (Codex P1, #155).
    leftover = [f for f in os.listdir(dest) if f.startswith("easysynq-backup-")]
    assert leftover == [], leftover

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


# --- the blob-dependent triad legs, exercised with REAL blob data (not vacuously) -------------
#
# The setup-flow drill (test_setup.py) runs at IN_SETUP time when the DB carries 0 blobs, so the
# blob SHA-256 re-hash + document_version→blob FK legs are vacuous there. These tests run while
# OPERATIONAL (the conftest default), create a real Effective document → a real source blob, and
# drive the drill over it — so the legs run over real rows AND a corrupted restored blob is caught.


async def _make_effective_doc(
    app_client: AsyncClient, token_factory: Callable[..., str], content: bytes
) -> None:
    """Create one Effective document (→ a content-addressed source blob in the documents bucket)."""
    subj = SimpleNamespace(a=_sub("bk-author"), b=_sub("bk-approver"))
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), content)


async def test_drill_passes_over_real_blobs(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[AC#5 blob legs] With a real Effective document present, the drill PASSES and the triad runs
    NON-vacuously over real blobs (details.blobs ≥ 1) — the SHA-256 re-hash + FK legs cover real
    rows, not an empty set."""
    org_id = await _org_id()  # conftest leaves the DB OPERATIONAL — no reset, so blobs can be made
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-drill-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-realblob-")
    await _insert_backup_policy(org_id, dest)

    result = await backup_service.run_restore_test(org_id)
    assert result["result"] == "PASS", result
    assert result["details"]["blobs"] >= 1  # the re-hash leg actually iterated real blobs


async def test_drill_fails_on_corrupted_restored_blob(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[AC#5 negative, re-hash leg] A restored scratch blob whose bytes are corrupted re-hashes to a
    different digest → the drill FAILs specifically on the blob SHA-256 leg. This is the leg the
    fresh-setup negative test cannot reach (it has no blobs)."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-drill-v2")
    dest = tempfile.mkdtemp(prefix="easysynq-corrupt-")
    await _insert_backup_policy(org_id, dest)
    client = _s3_client()

    def _corrupt_one_blob(handle: backup_service.ScratchHandle) -> None:
        listing = client.list_objects_v2(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Prefix=handle.object_prefix
        )
        objs = listing.get("Contents", [])
        assert objs, "expected ≥1 restored scratch blob to corrupt"
        client.put_object(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Key=objs[0]["Key"], Body=b"corrupted-not-the-real-bytes"
        )

    result = await backup_service.run_restore_test(org_id, after_restore=_corrupt_one_blob)
    assert result["result"] == "FAIL", result
    assert "re-hash" in result["reason"] or "SHA-256" in result["reason"], result


# --- Phase-1 I-7: the SCHEDULED retained-backup verify (verify_latest_retained_backup) ----------
#
# The redesign (Codex P2 on #155): the weekly job must verify the NEWEST RETAINED durable archive
# (build_durable_backup's easysynq-backup-*.tar[.enc] — encrypted when BACKUP_ENCRYPTION_KEY is set,
# which it IS in CI), NOT a fresh transient drill archive. So these drive a real durable backup to a
# temp destination, then verify_latest_retained_backup over it. They share the OPERATIONAL DB + the
# pg_dump/pg_restore runner requirement with the drill tests above.


async def _count_retained_verify_audits(org_id: uuid.UUID, event_type: EventType) -> int:
    """Count RESTORE_TEST_* audit rows for ``org_id`` carrying the scheduled-retained-verify source
    tag — the discriminator from the on-demand G-C drill (which never sets ``source``). Delta-based
    so the assertion is robust on the shared session DB."""
    async with get_sessionmaker()() as s:
        n = await s.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.org_id == org_id,
                AuditEvent.event_type == event_type,
                AuditEvent.after["source"].astext == "scheduled_retained_verify",
            )
        )
    return int(n or 0)


async def test_verify_retained_backup_passes(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[I-7 PASS] Over a REAL retained durable archive (written by run_scheduled_backups), the
    scheduled verify PASSES, persists last_restore_test_result=PASS, and emits a RESTORE_TEST_PASSED
    audit tagged source=scheduled_retained_verify (so an auditor can tell a scheduled
    retained-verify from the on-demand G-C drill, which never sets that tag)."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-retained-verify-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-retained-pass-")
    await _insert_backup_policy(org_id, dest)

    # Write a durable archive to the policy destination (a .tar.enc in CI — the key is set).
    out = await backup_service.run_scheduled_backups()
    assert out["backups"] and "error" not in out["backups"][0], out

    before = await _count_retained_verify_audits(org_id, EventType.RESTORE_TEST_PASSED)
    result = await backup_service.verify_latest_retained_backup(org_id)
    assert result["result"] == "PASS", result
    assert result["details"]["archive"].startswith("easysynq-backup-"), result

    async with get_sessionmaker()() as s:
        policy = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        assert policy is not None and policy.last_restore_test_result == "PASS"
        assert policy.last_restore_test_at is not None
    after = await _count_retained_verify_audits(org_id, EventType.RESTORE_TEST_PASSED)
    assert after == before + 1, (before, after)


async def test_verify_retained_backup_fails_on_corrupted_blob(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[I-7 negative] A manifested blob corrupted since the backup → the blob re-hash leg FAILs (the
    genuine 'this retained backup is no longer fully restorable' signal). Driven via the
    after_restore injector over the verify's scratch copy (mirrors
    test_drill_fails_on_corrupted_restored_blob)."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-retained-verify-v2")
    dest = tempfile.mkdtemp(prefix="easysynq-retained-fail-")
    await _insert_backup_policy(org_id, dest)
    await backup_service.run_scheduled_backups()
    client = _s3_client()

    def _corrupt_one_blob(handle: backup_service.ScratchHandle) -> None:
        listing = client.list_objects_v2(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Prefix=handle.object_prefix
        )
        objs = listing.get("Contents", [])
        assert objs, "expected ≥1 restored scratch blob to corrupt"
        client.put_object(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Key=objs[0]["Key"], Body=b"corrupted-not-the-real-bytes"
        )

    result = await backup_service.verify_latest_retained_backup(
        org_id, after_restore=_corrupt_one_blob
    )
    assert result["result"] == "FAIL", result
    assert "re-hash" in result["reason"] or "SHA-256" in result["reason"], result
    async with get_sessionmaker()() as s:
        policy = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        assert policy is not None and policy.last_restore_test_result == "FAIL"


async def test_verify_retained_backup_skips_when_no_archive(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[I-7 SKIP] A fresh install whose nightly hasn't run yet (an empty destination) → SKIPPED, NOT
    a FAIL, and nothing is persisted/audited — it must not flap red."""
    org_id = await _org_id()
    empty_dest = tempfile.mkdtemp(prefix="easysynq-retained-empty-")
    await _insert_backup_policy(org_id, empty_dest)

    async with get_sessionmaker()() as s:
        policy = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        assert policy is not None
        before_at, before_result = policy.last_restore_test_at, policy.last_restore_test_result

    result = await backup_service.verify_latest_retained_backup(org_id)
    assert result["result"] == "SKIPPED", result
    assert "no retained backup archive" in result["reason"], result

    async with get_sessionmaker()() as s:
        policy = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        assert policy is not None
        assert policy.last_restore_test_at == before_at  # unchanged — SKIPPED persists nothing
        assert policy.last_restore_test_result == before_result


async def test_verify_retained_backup_decrypts_encrypted_archive(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[I-7 encrypted round-trip] With BACKUP_ENCRYPTION_KEY set (the CI default), the retained
    archive is a .tar.enc; the verify DECRYPTS it and PASSES — proving the encrypted STORED backups
    (not just a plaintext drill archive) are restorable, the exact gap Codex P2 flagged. Robust to a
    keyless run (then it asserts the plaintext .tar path instead)."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-retained-verify-v3")
    dest = tempfile.mkdtemp(prefix="easysynq-retained-enc-")
    await _insert_backup_policy(org_id, dest)
    out = await backup_service.run_scheduled_backups()
    entry = out["backups"][0]
    assert "error" not in entry, entry

    result = await backup_service.verify_latest_retained_backup(org_id)
    assert result["result"] == "PASS", result
    verified_name = result["details"]["archive"]
    if entry["encrypted"]:  # CI sets the key → the durable archive is AES-256-GCM .tar.enc
        assert verified_name.endswith(".tar.enc"), (verified_name, entry)
        assert any(f.endswith(".tar.enc") for f in os.listdir(dest)), os.listdir(dest)
    else:  # keyless fallback (not the CI default) → plaintext .tar
        assert verified_name.endswith(".tar"), (verified_name, entry)


async def test_verify_retained_backup_tears_down_scratch(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[I-7 teardown] A verify is never a standing target — after a PASS, its DEDICATED
    verify_easysynq_* scratch DB is dropped and its scratch-bucket prefix is emptied (mirrors
    test_drill_tears_down_scratch_namespace, scoped to the verify namespace it actually created
    so it is immune to other tests' scratch on the shared bucket)."""
    import psycopg

    from easysynq_api.services.backup.dsn import conn_kwargs

    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"effective-source-for-retained-verify-v4")
    dest = tempfile.mkdtemp(prefix="easysynq-retained-teardown-")
    await _insert_backup_policy(org_id, dest)
    await backup_service.run_scheduled_backups()

    captured: dict[str, str] = {}

    def _capture(handle: backup_service.ScratchHandle) -> None:
        captured["db"] = handle.scratch_db
        captured["bucket"] = handle.scratch_bucket
        captured["prefix"] = handle.object_prefix

    result = await backup_service.verify_latest_retained_backup(org_id, after_restore=_capture)
    assert result["result"] == "PASS", result
    assert captured["db"].startswith("verify_easysynq_"), captured

    settings = get_settings()
    with (
        psycopg.connect(**conn_kwargs(settings.sync_dsn), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (captured["db"],))
        row = cur.fetchone()
        assert row is not None and row[0] == 0, captured["db"]  # the verify scratch DB is gone

    client = _s3_client()
    listing = client.list_objects_v2(  # type: ignore[attr-defined]
        Bucket=captured["bucket"], Prefix=captured["prefix"]
    )
    assert listing.get("KeyCount", 0) == 0, listing.get("Contents")
