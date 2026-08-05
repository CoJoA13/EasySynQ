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
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


async def test_restore_fails_on_unresolvable_stored_blob_locator(
    app_client: AsyncClient, token_factory: Callable[..., str], tmp_path: Path
) -> None:
    """A restored ``blob.object_key`` must resolve in its stored bucket, not merely have a copied
    scratch object at its SHA-derived key. Mutating only that DB locator leaves the copied bytes
    intact, so this fails if the triad ignores the restored database locator."""
    org_id = await _org_id()
    await _make_effective_doc(app_client, token_factory, b"unresolvable-locator-source-v1")
    await _insert_backup_policy(org_id, str(tmp_path))
    archive_path = await _durable_archive(org_id)
    client = _s3_client()

    def _make_locator_unresolvable(handle: backup_service.ScratchHandle) -> None:
        import psycopg

        with (
            psycopg.connect(
                **conn_kwargs(handle.owner_dsn, dbname=handle.scratch_db), autocommit=True
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT sha256 FROM blob ORDER BY sha256 LIMIT 1")
            row = cur.fetchone()
            assert row is not None, "expected a restored blob locator to mutate"
            sha = str(row[0])
            client.head_object(  # type: ignore[attr-defined]
                Bucket=handle.scratch_bucket, Key=f"{handle.object_prefix}{sha}"
            )
            cur.execute(
                "UPDATE blob SET object_key = %s WHERE sha256 = %s",
                (f"c01c-unresolvable-locator/{sha}", sha),
            )

    out = await backup_service.run_restore(
        org_id,
        archive_path=archive_path,
        fetch_off_host=lambda _s, _o: 0,
        after_restore=_make_locator_unresolvable,
    )
    try:
        assert out["result"] == "FAIL", out
        assert out["scratch_db"] is None  # a locator failure must tear the target down
    finally:
        # The unfixed baseline incorrectly PASSes; keep this RED proof non-leaking while recording
        # it.
        await _drop_target(out.get("scratch_db"))


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


async def _upgrade_failures(*, org_id: uuid.UUID, after_id: int) -> list[dict[str, object]]:
    """Every UPGRADE_FAILED row this test produced for ``org_id``, newer than ``after_id``.

    Deliberately NOT a count of all UPGRADE_FAILED rows — the integration suite shares one session
    DB and shard composition moves under us, so an absolute count is not a stable assertion.
    Constraining org_id and an explicit before/after id boundary makes it run-scoped while retaining
    every terminal failure, so callers can detect contradictory duplicates before checking details.
    """
    async with get_sessionmaker()() as s:
        rows = list(
            await s.execute(
                select(AuditEvent.after).where(
                    AuditEvent.event_type == EventType.UPGRADE_FAILED,
                    AuditEvent.org_id == org_id,
                    AuditEvent.id > after_id,
                )
            )
        )
    return [dict(after or {}) for (after,) in rows]


async def _max_audit_id() -> int:
    """The current audit high-water mark, so a test can scope assertions to rows IT wrote."""
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.coalesce(func.max(AuditEvent.id), 0))) or 0)


async def test_upgrade_aborts_when_pre_backup_raises_a_non_backup_error(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-backup guard must catch EVERY failure, not just ``BackupError``.

    ⚠ Mutation note: a stub raising ``BackupError`` is the obvious test and it PASSES against the
    pre-fix code, because that was the one exception already handled. The canonical pre-backup
    failure is a full or read-only backup mount, which surfaces as ``OSError`` from ``mkdir`` /
    ``write_bytes`` — that escaped ``run_upgrade`` entirely and broke its "never raises" contract.
    So this test raises ``OSError`` on purpose; it is RED against the pre-fix baseline.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    marker = "no-space-left-fixture"
    ran_alembic = False

    async def _fixture_destination(_session: AsyncSession, _org_id: uuid.UUID) -> str:
        return "fixture://upgrade-pre-backup"

    def _boom(*_a: object, **_k: object) -> dict[str, object]:
        raise OSError(marker)

    def _spy() -> None:
        nonlocal ran_alembic
        ran_alembic = True

    monkeypatch.setattr(upgrade_service, "build_durable_backup", _boom)
    monkeypatch.setattr(upgrade_service, "_run_alembic_upgrade", _spy)
    monkeypatch.setattr(upgrade_service, "_backup_destination", _fixture_destination)
    baseline = await _max_audit_id()

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "FAILED", out
    assert out["stage"] == "pre_backup", out
    assert marker in str(out["reason"])
    assert ran_alembic is False, "the migration must NOT run once the pre-backup has failed"
    failures = await _upgrade_failures(org_id=org_id, after_id=baseline)
    assert len(failures) == 1, failures
    assert failures[0].get("stage") == "pre_backup"
    assert marker in str(failures[0].get("error", ""))


async def test_upgrade_aborts_when_pre_backup_archive_fails_verification(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An archive that failed its own checksum is not a usable recovery artifact.

    ``build_durable_backup`` reports a mismatch by RETURNING ``verified=False``, never by raising.
    The pre-fix baseline returns OK and migrates the live database against an archive already known
    to be unusable — then names it as the recovery pointer.
    ``services/backup/service.py:197-209`` already fails closed here for the nightly path.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    archive = "fixture://upgrade-unverified.tar.enc"
    ran_alembic = False

    async def _fixture_destination(_session: AsyncSession, _org_id: uuid.UUID) -> str:
        return "fixture://upgrade-unverified"

    def _unverified(*_a: object, **_k: object) -> dict[str, object]:
        return {"archive": archive, "verified": False, "encrypted": True, "legs": {}}

    def _spy() -> None:
        nonlocal ran_alembic
        ran_alembic = True

    async def _all_green() -> list[dict[str, object]]:
        return [{"name": "postgres", "ready": True}, {"name": "alembic", "ready": True}]

    monkeypatch.setattr(upgrade_service, "build_durable_backup", _unverified)
    monkeypatch.setattr(upgrade_service, "_run_alembic_upgrade", _spy)
    monkeypatch.setattr(upgrade_service, "_backup_destination", _fixture_destination)
    # Stub readiness so the BASELINE failure (pre-fix, this test reached the health gate) cannot be
    # confused with an external-dependency failure. The proof must be about the verified flag alone.
    monkeypatch.setattr(upgrade_service, "check_all", _all_green)
    baseline = await _max_audit_id()

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "FAILED", out
    assert out["stage"] == "pre_backup", out
    assert ran_alembic is False, "must not migrate against an archive that failed verification"
    failures = await _upgrade_failures(org_id=org_id, after_id=baseline)
    assert len(failures) == 1, failures
    assert failures[0].get("stage") == "pre_backup"
    assert archive in str(failures[0].get("error", ""))


async def test_upgrade_never_raises_when_orchestration_fails_outside_a_stage(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three guarded stages are not the whole function.

    ``_alembic_head()`` runs before any stage guard and its failure escaped the function outright.
    ``cli/upgrade.py`` does not wrap ``run_upgrade``, so that reached the operator as a traceback
    instead of a stage plus a recovery pointer. RED against the pre-fix baseline: the call raises.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    marker = "alembic-head-unreadable-fixture"

    def _boom() -> str:
        raise RuntimeError(marker)

    monkeypatch.setattr(upgrade_service, "_alembic_head", _boom)
    baseline = await _max_audit_id()

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "FAILED", out
    assert out["stage"] == "orchestration", out
    assert marker in str(out["reason"])
    failures = await _upgrade_failures(org_id=org_id, after_id=baseline)
    assert len(failures) == 1, failures
    assert failures[0].get("stage") == "orchestration"
    assert marker in str(failures[0].get("error", ""))


async def test_upgrade_failure_after_migration_keeps_exact_recovery_pointer(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-migration orchestration failure must still identify the verified pre-backup."""
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    archive = "fixture://upgrade-post-migration.tar.enc"
    marker = "readiness-orchestration-fixture"
    events: list[tuple[str, dict[str, object]]] = []
    migrated = False

    async def _fixture_destination(_session: AsyncSession, _org_id: uuid.UUID) -> str:
        return "fixture://upgrade-post-migration"

    def _record_emit(
        _session: AsyncSession,
        *,
        event_type: str,
        after: dict[str, object],
        **_kwargs: object,
    ) -> None:
        events.append((event_type, after))

    def _verified_backup(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"archive": archive, "verified": True, "encrypted": True, "legs": {}}

    def _migrate() -> None:
        nonlocal migrated
        migrated = True

    async def _health_boom() -> list[dict[str, object]]:
        raise RuntimeError(marker)

    monkeypatch.setattr(upgrade_service, "_alembic_head", lambda: "fixture-head")
    monkeypatch.setattr(upgrade_service, "_backup_destination", _fixture_destination)
    monkeypatch.setattr(upgrade_service, "_emit", _record_emit)
    monkeypatch.setattr(upgrade_service, "build_durable_backup", _verified_backup)
    monkeypatch.setattr(upgrade_service, "_run_alembic_upgrade", _migrate)
    monkeypatch.setattr(upgrade_service, "check_all", _health_boom)

    out = await upgrade_service.run_upgrade(org_id)

    assert migrated is True
    assert out["result"] == "FAILED", out
    assert out["stage"] == "orchestration", out
    assert marker in str(out["reason"])
    assert out["pre_backup_archive"] == archive
    assert [event for event, _after in events] == ["UPGRADE_STARTED", "UPGRADE_FAILED"]
    assert events[-1][1]["pre_backup_archive"] == archive


@pytest.mark.parametrize("failing", ["get_settings", "create_async_engine", "async_sessionmaker"])
async def test_upgrade_never_raises_when_setup_fails(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """Setup is part of the function, so it is part of the contract.

    ``get_settings()`` (malformed DSN, missing required field), ``create_async_engine()``
    (unparseable URL, bad driver), and the sessionmaker sat ABOVE the try: and escaped exactly like
    the stage bodies once did — the same defect one scope out. No audit row is possible here: the
    failure is upstream of any usable session, so the structured dict is the whole honest answer.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    marker = f"{failing}-setup-fixture"

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError(marker)

    monkeypatch.setattr(upgrade_service, failing, _boom)

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "FAILED", out
    assert out["stage"] == "orchestration", out
    assert marker in str(out["reason"])


async def test_upgrade_disposal_failure_does_not_replace_the_verdict(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must never become the result.

    ``engine.dispose()`` runs in ``finally``, so a bare await there raises straight out and REPLACES
    an already-computed return — reporting a concluded upgrade as an exception, the worst direction
    for this command. Here the upgrade fails at a known stage AND disposal fails; the caller must
    still receive the stage verdict, not the disposal error.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    stage_marker = "orchestration-fixture"
    dispose_marker = "dispose-must-not-surface-fixture"
    real_create = upgrade_service.create_async_engine
    dispose_called = False

    class _FailingDisposeEngine:
        """Delegates everything to a real engine but fails on ``dispose``.

        ``AsyncEngine.dispose`` is read-only, so ``monkeypatch.setattr(engine, "dispose", ...)``
        raises ``AttributeError`` inside ``create_async_engine`` — which the orchestration guard
        then correctly reports, proving nothing about disposal. A delegating wrapper puts the
        failure where the test actually means it.
        """

        def __init__(self, inner: AsyncEngine) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        async def dispose(self, *_a: object, **_k: object) -> None:
            nonlocal dispose_called
            dispose_called = True
            await self._inner.dispose()
            raise RuntimeError(dispose_marker)

    def _engine_with_failing_dispose(*a: object, **k: object) -> object:
        return _FailingDisposeEngine(real_create(*a, **k))  # type: ignore[arg-type]

    def _head_boom() -> str:
        raise RuntimeError(stage_marker)

    monkeypatch.setattr(upgrade_service, "create_async_engine", _engine_with_failing_dispose)
    monkeypatch.setattr(upgrade_service, "_alembic_head", _head_boom)

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "FAILED", out
    assert out["stage"] == "orchestration", out
    assert stage_marker in str(out["reason"]), "the STAGE failure must be what the caller sees"
    assert dispose_marker not in str(out["reason"]), "cleanup must not overwrite the verdict"
    assert dispose_called is True, "the test must exercise engine disposal"


async def test_upgrade_session_close_failure_preserves_committed_success(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session cleanup cannot rewrite a committed terminal result.

    SQLAlchemy runs ``AsyncSession.close()`` from the session context manager's ``__aexit__``. If
    close raises after ``UPGRADE_COMPLETED`` commits, that cleanup error must be logged without
    converting the already-decided OK result into a contradictory orchestration failure.
    """
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    close_marker = "session-close-after-success-fixture"
    events: list[str] = []
    close_calls = 0

    async def _fixture_destination(_session: AsyncSession, _org_id: uuid.UUID) -> str:
        return "fixture://upgrade-session-close"

    def _record_emit(_session: AsyncSession, *, event_type: str, **_kwargs: object) -> None:
        events.append(event_type)

    def _verified_backup(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "archive": "fixture://upgrade-session-close.tar.enc",
            "verified": True,
            "encrypted": True,
            "legs": {},
        }

    async def _all_green() -> list[dict[str, object]]:
        return [{"name": "postgres", "ready": True}, {"name": "alembic", "ready": True}]

    async def _close_boom(_session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        raise RuntimeError(close_marker)

    monkeypatch.setattr(upgrade_service, "_alembic_head", lambda: "fixture-head")
    monkeypatch.setattr(upgrade_service, "_backup_destination", _fixture_destination)
    monkeypatch.setattr(upgrade_service, "_emit", _record_emit)
    monkeypatch.setattr(upgrade_service, "build_durable_backup", _verified_backup)
    monkeypatch.setattr(upgrade_service, "_run_alembic_upgrade", lambda: None)
    monkeypatch.setattr(upgrade_service, "check_all", _all_green)
    monkeypatch.setattr(AsyncSession, "close", _close_boom)

    out = await upgrade_service.run_upgrade(org_id)

    assert out["result"] == "OK", out
    assert events == ["UPGRADE_STARTED", "UPGRADE_COMPLETED"]
    assert close_calls == 1


@pytest.mark.parametrize(
    ("signal_type", "signal_marker"),
    [
        (asyncio.CancelledError, "upgrade-cancelled-fixture"),
        (SystemExit, "upgrade-system-exit-fixture"),
    ],
    ids=["cancelled-error", "system-exit"],
)
async def test_upgrade_session_close_failure_does_not_replace_base_exception(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    signal_type: type[BaseException],
    signal_marker: str,
) -> None:
    """An ordinary cleanup error must not transform cancellation/exit into a FAILED verdict."""
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    close_calls = 0

    async def _interrupt_destination(_session: AsyncSession, _org_id: uuid.UUID) -> str:
        raise signal_type(signal_marker)

    async def _close_boom(_session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        raise RuntimeError("session-close-during-cancellation-fixture")

    monkeypatch.setattr(upgrade_service, "_alembic_head", lambda: "fixture-head")
    monkeypatch.setattr(upgrade_service, "_backup_destination", _interrupt_destination)
    monkeypatch.setattr(upgrade_service, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(AsyncSession, "close", _close_boom)

    with pytest.raises(signal_type, match=signal_marker):
        await upgrade_service.run_upgrade(org_id)
    assert close_calls == 1


@pytest.mark.parametrize(
    ("signal_type", "signal_marker"),
    [
        (asyncio.CancelledError, "failure-audit-cancelled-fixture"),
        (SystemExit, "failure-audit-system-exit-fixture"),
    ],
    ids=["cancelled-error", "system-exit"],
)
async def test_upgrade_failure_audit_close_failure_does_not_swallow_base_exception(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    signal_type: type[BaseException],
    signal_marker: str,
) -> None:
    """Failure auditing propagates cancellation/exit even when its cleanup also fails."""
    from easysynq_api.services import upgrade as upgrade_service

    org_id = await _org_id()
    close_calls = 0

    def _head_boom() -> str:
        raise RuntimeError("orchestration-before-failure-audit-fixture")

    def _interrupt_emit(*_args: object, **_kwargs: object) -> None:
        raise signal_type(signal_marker)

    async def _close_boom(_session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        raise RuntimeError("failure-audit-session-close-fixture")

    monkeypatch.setattr(upgrade_service, "_alembic_head", _head_boom)
    monkeypatch.setattr(upgrade_service, "_emit", _interrupt_emit)
    monkeypatch.setattr(AsyncSession, "close", _close_boom)

    with pytest.raises(signal_type, match=signal_marker):
        await upgrade_service.run_upgrade(org_id)
    assert close_calls == 1


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
