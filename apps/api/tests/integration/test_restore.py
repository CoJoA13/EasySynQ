"""S11 integration proofs — the operator-grade WORM-aware restore-to-verified-target (R37) + the
encrypted durable archive + the pre-backup/health-gated upgrade.

These exercise the real pg_dump/pg_restore + MinIO round-trip (CI has postgresql-client-16; a host
without it makes the restore an honest FAIL, not a 500). The pure checkpoint-not-ahead verdict + the
crypto envelope are unit-proven in ``tests/unit/test_backup_crypto.py``; here we prove the full
orchestration: a verified target stands up + audits RESTORE_VERIFIED, a checkpoint-ahead is FLAGGED
(and an ack proceeds + audits RESTORE_CHECKPOINT_ACK), a corrupted restored blob FAILs, and the
restored chain re-verify runs. The blob bytes are READ from the locked vault — never written.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services import backup as backup_service
from easysynq_api.services.audit.linker import link_all
from easysynq_api.services.backup import archive, crypto, drill, restore
from easysynq_api.services.backup.dsn import conn_kwargs

from .test_backup import _insert_backup_policy, _make_effective_doc, _s3_client
from .test_setup import _org_id

pytestmark = pytest.mark.integration


async def _durable_archive(org_id: uuid.UUID) -> str:
    """Write one encrypted durable archive for the org's policy; return its path."""
    out = await backup_service.run_scheduled_backups()
    entry = next(b for b in out["backups"] if str(b.get("org_id")) == str(org_id))
    assert "error" not in entry, entry
    return str(entry["archive"])


async def _drop_target(scratch_db: str | None) -> None:
    if scratch_db:
        restore.discard_target(get_settings(), scratch_db)


async def test_durable_backup_encrypted_roundtrips(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The durable archive is AES-256-GCM ``.tar.enc`` (manifest v2 + the config-snapshot leg);
    decrypt+unpack recovers a valid plaintext tar. Keycloak is absent in CI → realm leg 'absent'."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"enc-roundtrip-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-enc-")
    await _insert_backup_policy(org_id, dest)

    out = await backup_service.run_scheduled_backups()
    entry = next(b for b in out["backups"] if str(b.get("org_id")) == str(org_id))
    assert entry["encrypted"] is True, entry
    assert entry["archive"].endswith(".tar.enc")
    assert entry["legs"]["config_snapshot"] == "present"
    assert entry["legs"]["realm_export"] == "absent"  # no Keycloak admin in CI → graceful absent

    from pathlib import Path

    enc = Path(entry["archive"])
    assert crypto.is_encrypted_archive(enc)
    plain = crypto.decrypt_archive(
        enc, Path(tempfile.mkdtemp()) / "round.tar", secret=get_settings().backup_encryption_key
    )
    manifest = archive.read_manifest(plain)
    assert manifest["manifest_version"] == 2
    assert manifest["legs"]["config_snapshot"] == "present"


async def test_restore_to_verified_target_passes(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[R37] A real archive restores to a VERIFIED, standing target (off-host checkpoint not ahead),
    the restored chain re-verify runs, and RESTORE_VERIFIED is audited. The target is left standing
    (PASS) for the operator cutover — then discarded here."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"verified-target-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-restore-ok-")
    await _insert_backup_policy(org_id, dest)
    archive_path = await _durable_archive(org_id)

    out = await backup_service.run_restore(
        org_id,
        archive_path=archive_path,
        fetch_off_host=lambda _s, _o: 0,  # 0 ≤ head → OK
    )
    try:
        assert out["result"] == "PASS", out
        assert out["scratch_db"] and out["scratch_db"].startswith("restore_easysynq_")
        assert out["checkpoint"]["verdict"] == "OK"
        assert out["chain"]["verified"] is True  # the restored-chain re-verify ran
        async with get_sessionmaker()() as s:
            verified = await s.scalar(
                select(AuditEvent.id).where(AuditEvent.event_type == EventType.RESTORE_VERIFIED)
            )
        assert verified is not None
    finally:
        await _drop_target(out.get("scratch_db"))


async def test_restore_flagged_on_checkpoint_ahead(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[R37 tamper guard] An off-host checkpoint AHEAD of the restored head FLAGS the restore (the
    target is torn down) and audits RESTORE_CHECKPOINT_AHEAD — never a silent PASS."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"flagged-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-restore-flag-")
    await _insert_backup_policy(org_id, dest)
    archive_path = await _durable_archive(org_id)

    out = await backup_service.run_restore(
        org_id,
        archive_path=archive_path,
        fetch_off_host=lambda _s, _o: 10**9,  # far ahead
    )
    assert out["result"] == "FLAGGED", out
    assert out["scratch_db"] is None  # torn down
    async with get_sessionmaker()() as s:
        flagged = await s.scalar(
            select(AuditEvent.id).where(AuditEvent.event_type == EventType.RESTORE_CHECKPOINT_AHEAD)
        )
    assert flagged is not None


async def test_restore_flagged_then_ack_passes(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[R37] The audited operator acknowledgement proceeds past a checkpoint-ahead flag → PASS +
    a dedicated RESTORE_CHECKPOINT_ACK audit row recording the ack."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"ack-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-restore-ack-")
    await _insert_backup_policy(org_id, dest)
    archive_path = await _durable_archive(org_id)

    out = await backup_service.run_restore(
        org_id,
        archive_path=archive_path,
        audit_checkpoint_ack=True,
        fetch_off_host=lambda _s, _o: 10**9,
    )
    try:
        assert out["result"] == "PASS", out
        assert out["checkpoint"]["acknowledged"] is True
        async with get_sessionmaker()() as s:
            acked = await s.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == EventType.RESTORE_CHECKPOINT_ACK
                )
            )
        assert acked is not None
    finally:
        await _drop_target(out.get("scratch_db"))


async def test_restore_fails_on_corrupted_blob(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[R37 negative] A corrupted restored blob re-hashes to a different digest → the restore FAILs
    on the triad (no standing target left)."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"corrupt-blob-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-restore-corrupt-")
    await _insert_backup_policy(org_id, dest)
    archive_path = await _durable_archive(org_id)
    client = _s3_client()

    def _corrupt(handle: backup_service.ScratchHandle) -> None:
        listing = client.list_objects_v2(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Prefix=handle.object_prefix
        )
        objs = listing.get("Contents", [])
        assert objs, "expected ≥1 restored scratch blob to corrupt"
        client.put_object(  # type: ignore[attr-defined]
            Bucket=handle.scratch_bucket, Key=objs[0]["Key"], Body=b"corrupted-not-the-bytes"
        )

    out = await backup_service.run_restore(
        org_id, archive_path=archive_path, fetch_off_host=lambda _s, _o: 0, after_restore=_corrupt
    )
    assert out["result"] == "FAIL", out
    assert out["scratch_db"] is None  # torn down on FAIL


async def test_restore_fails_on_corrupted_chain(
    app_client: AsyncClient, token_factory: Callable[..., str], dsns: dict[str, str]
) -> None:
    """[R37 / AC#6] The restored-chain re-verify catches a mutated audit row. Link the live chain
    first (so the archive carries chained rows), then corrupt a chained row_hash in the restored
    scratch (owner-owned tables) — the re-verify reports it as a broken link → FAIL."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"corrupt-chain-source-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-restore-chain-")
    await _insert_backup_policy(org_id, dest)

    # link the chain on the live DB as the dedicated linker role, so chained rows exist in the dump
    linker_engine = create_async_engine(dsns["linker"])
    sm = async_sessionmaker(linker_engine, expire_on_commit=False)
    try:
        async with sm() as s:
            await link_all(s)
    finally:
        await linker_engine.dispose()

    archive_path = await _durable_archive(org_id)

    def _corrupt_chain(handle: backup_service.ScratchHandle) -> None:
        import psycopg

        with (
            psycopg.connect(
                **conn_kwargs(handle.owner_dsn, dbname=handle.scratch_db), autocommit=True
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "UPDATE audit_event SET row_hash = decode(repeat('00', 32), 'hex') "
                "WHERE id = (SELECT min(id) FROM audit_event WHERE chained_at IS NOT NULL)"
            )

    out = await backup_service.run_restore(
        org_id,
        archive_path=archive_path,
        fetch_off_host=lambda _s, _o: 0,
        after_restore=_corrupt_chain,
    )
    assert out["result"] == "FAIL", out
    assert "chain" in out["reason"].lower()
    assert out["scratch_db"] is None


async def test_upgrade_pre_backup_and_health_gate(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[S11] easysynq upgrade: pre-backup → ``alembic upgrade head`` (no-op, already at head) →
    readiness health-gate → UPGRADE_COMPLETED. Keycloak is unreachable in CI, so the readiness
    probe is stubbed green here — we are proving the upgrade orchestration, not readiness."""
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    dest = tempfile.mkdtemp(prefix="easysynq-upgrade-")
    await _insert_backup_policy(org_id, dest)

    async def _all_green() -> list[dict[str, object]]:
        return [{"name": "postgres", "ready": True}, {"name": "alembic", "ready": True}]

    monkeypatch.setattr(upgrade_service, "check_all", _all_green)

    out = await upgrade_service.run_upgrade(org_id)
    assert out["result"] == "OK", out
    assert str(out["pre_backup_archive"]).startswith(dest)
    async with get_sessionmaker()() as s:
        completed = await s.scalar(
            select(AuditEvent.id).where(AuditEvent.event_type == EventType.UPGRADE_COMPLETED)
        )
    assert completed is not None


async def test_restore_discard_cleans_scratch_bucket(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[review S11-RS-1] `easysynq restore --discard` (discard_target) tears down BOTH legs — the
    scratch DB AND the copied blobs under its prefix — so a discarded restore never orphans a full
    copy of the org's Effective blob set in the non-WORM restore-scratch bucket."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"discard-cleanup-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-discard-")
    await _insert_backup_policy(org_id, dest)
    archive_path = await _durable_archive(org_id)

    out = await backup_service.run_restore(
        org_id, archive_path=archive_path, fetch_off_host=lambda _s, _o: 0
    )
    assert out["result"] == "PASS", out
    prefix, scratch_db = out["object_prefix"], out["scratch_db"]
    client = _s3_client()
    bucket = get_settings().s3_bucket_restore_scratch
    before = client.list_objects_v2(Bucket=bucket, Prefix=prefix)  # type: ignore[attr-defined]
    assert before.get("KeyCount", 0) >= 1, "expected the verified target to hold copied blobs"

    restore.discard_target(get_settings(), scratch_db)

    after = client.list_objects_v2(Bucket=bucket, Prefix=prefix)  # type: ignore[attr-defined]
    assert after.get("KeyCount", 0) == 0, after.get("Contents")


async def test_durable_backup_without_key_omits_sensitive_legs(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[review S11-RS-2] With no BACKUP_ENCRYPTION_KEY the durable archive is PLAINTEXT and the
    realm-export + config-snapshot legs (which carry secrets) are OMITTED — never cleartext (doc 12
    §6.2). The pre-existing db.dump still ships (plaintext was always its mode)."""
    await _make_effective_doc(app_client, token_factory, b"no-key-legs-v1")
    dest = tempfile.mkdtemp(prefix="easysynq-nokey-")
    settings = get_settings().model_copy(update={"backup_encryption_key": "CHANGE_ME"})

    out = await asyncio.to_thread(drill.build_durable_backup, settings, destination=dest)
    assert out["encrypted"] is False, out
    assert out["archive"].endswith(".tar"), out
    assert out["legs"]["realm_export"] == "absent"
    assert out["legs"]["config_snapshot"] == "absent"
