"""The restore-into-scratch drill + integrity triad (slice S8b2, doc 08 §8.2 / AC#5).

``run_drill`` produces a real backup archive at the configured destination, restores it into a fresh
scratch DATABASE, copies the manifested blobs into a non-WORM scratch bucket, runs the integrity
triad on the RESTORED copy, and tears the scratch namespace down — returning a PASS/FAIL verdict
(never raising; a crash is an honest FAIL, not a 500). The steps are composable so the negative test
can inject a post-restore fault via ``after_restore`` without any production hook.

Runs as the OWNER DB role (``settings.sync_dsn``) — the runtime ``easysynq_app`` role can neither
``pg_dump`` the whole DB nor ``CREATE DATABASE``. Row-count parity is race-free: counts are captured
under a single ``REPEATABLE READ`` snapshot that ``pg_dump --snapshot`` then uses (the 423 setup
latch also keeps the DB quiescent during first-run setup).

NB doc 08 §8.2 describes the scratch namespace as "a temporary PG schema"; we use an isolated
temporary DATABASE instead — it is ``pg_restore``'s natural unit (a whole-DB custom-format dump does
not restore cleanly into a renamed schema), gives the strongest isolation, and tears down with one
``DROP DATABASE``. The §8.2 wording is reconciled as illustrative of "an isolated namespace" (owner
sign-off, this slice; note back-propagated to doc 08 §8.2).
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ...config import Settings
from . import archive, config_snapshot, crypto, realm_export
from .archive import BackupError, BlobRef
from .dsn import conn_kwargs

logger = logging.getLogger("easysynq.backup.drill")

_SCRATCH_PREFIX = "scratch_easysynq_"


@dataclasses.dataclass(frozen=True, slots=True)
class ScratchHandle:
    """The torn-down-after handle to one drill's scratch namespace (a DB + a bucket prefix)."""

    owner_dsn: str
    scratch_db: str
    scratch_bucket: str
    object_prefix: str
    expected_counts: dict[str, int]


@dataclasses.dataclass(frozen=True, slots=True)
class DrillResult:
    result: str  # "PASS" | "FAIL"
    reason: str
    details: dict[str, Any] = dataclasses.field(default_factory=dict)


# --- boto3 (the same MinIO the vault uses; arbitrary bucket ops the drill needs) ---------------


def _s3(settings: Settings) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
    )


# --- psycopg maintenance + queries (sync; called inside asyncio.to_thread) ---------------------


def _capture_and_dump(owner_dsn: str, dump_path: Path) -> tuple[dict[str, int], list[BlobRef]]:
    """Under ONE REPEATABLE READ snapshot: export the snapshot, count every public table, read the
    blob rows, and ``pg_dump --snapshot`` (the txn stays open so the snapshot is valid). The
    restored scratch must then match these exact counts."""
    import psycopg
    from psycopg import IsolationLevel, sql

    conn = psycopg.connect(**conn_kwargs(owner_dsn))
    try:
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        with conn.cursor() as cur:
            cur.execute("SELECT pg_export_snapshot()")
            row = cur.fetchone()
            snapshot = row[0] if row else None
            cur.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                "AND c.relispartition = false ORDER BY c.relname"
            )
            tables = [r[0] for r in cur.fetchall()]
            counts: dict[str, int] = {}
            for t in tables:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
                cr = cur.fetchone()
                counts[t] = int(cr[0]) if cr else 0
            cur.execute("SELECT sha256, size_bytes, bucket, object_key FROM blob")
            blobs = [
                BlobRef(sha256=r[0], size_bytes=int(r[1]), bucket=r[2], object_key=r[3])
                for r in cur.fetchall()
            ]
        archive.dump_database(owner_dsn, dump_path, snapshot=snapshot)
        conn.rollback()
    finally:
        conn.close()
    return counts, blobs


def _autocommit(owner_dsn: str, *, dbname: str | None = None) -> Any:
    import psycopg

    return psycopg.connect(**conn_kwargs(owner_dsn, dbname=dbname), autocommit=True)


def _sweep_stale_scratch(owner_dsn: str) -> None:
    """Best-effort: drop leftover scratch DBs from a crashed prior drill (no live connection holds
    them; FORCE terminates any straggler)."""
    from psycopg import sql

    try:
        with _autocommit(owner_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (_SCRATCH_PREFIX + "%",)
            )
            for (name,) in cur.fetchall():
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
    except Exception:  # noqa: BLE001 — best-effort cleanup; a sweep failure must not fail the drill
        logger.warning("restore-drill: stale-scratch sweep skipped", exc_info=True)


def _create_scratch_db(owner_dsn: str, scratch_db: str) -> None:
    from psycopg import sql

    with _autocommit(owner_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch_db)))


def _drop_scratch_db(owner_dsn: str, scratch_db: str) -> None:
    from psycopg import sql

    with _autocommit(owner_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch_db))
        )


def _scratch_counts(handle: ScratchHandle) -> dict[str, int | None]:
    """Count the SAME tables in the restored scratch DB; ``None`` if a table is missing (a partial
    restore) → parity fails."""
    import psycopg
    from psycopg import sql

    out: dict[str, int | None] = {}
    with _autocommit(handle.owner_dsn, dbname=handle.scratch_db) as conn:
        for t in handle.expected_counts:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
                    r = cur.fetchone()
                    out[t] = int(r[0]) if r else 0
            except psycopg.Error:
                out[t] = None
    return out


def _fk_orphans(handle: ScratchHandle) -> int:
    """document_version → blob FK integrity in scratch: rows whose source/rendition blob is gone."""
    with _autocommit(handle.owner_dsn, dbname=handle.scratch_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM document_version dv "
            "LEFT JOIN blob b1 ON dv.source_blob_sha256 = b1.sha256 "
            "WHERE b1.sha256 IS NULL "
            "OR (dv.rendition_blob_sha256 IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM blob b2 WHERE b2.sha256 = dv.rendition_blob_sha256))"
        )
        r = cur.fetchone()
        return int(r[0]) if r else 0


def _scratch_blob_shas(handle: ScratchHandle) -> list[str]:
    with _autocommit(handle.owner_dsn, dbname=handle.scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM blob")
        return [r[0] for r in cur.fetchall()]


# --- blob copy + re-hash -----------------------------------------------------------------------


def _copy_blobs(settings: Settings, blobs: list[BlobRef], bucket: str, prefix: str) -> None:
    client = _s3(settings)
    for b in blobs:
        client.copy_object(
            Bucket=bucket,
            Key=f"{prefix}{b.sha256}",
            CopySource={"Bucket": b.bucket, "Key": b.object_key},
        )


def _rehash_scratch_blobs(settings: Settings, handle: ScratchHandle) -> list[str]:
    """Fetch each restored scratch-bucket blob and confirm its bytes hash to its content-address PK.
    Returns the shas that are missing or mismatched (empty → all intact)."""
    client = _s3(settings)
    bad: list[str] = []
    for sha in _scratch_blob_shas(handle):
        try:
            body = client.get_object(
                Bucket=handle.scratch_bucket, Key=f"{handle.object_prefix}{sha}"
            )["Body"].read()
        except Exception:  # noqa: BLE001 — a missing/unreadable restored object is a failed restore
            bad.append(sha)
            continue
        if hashlib.sha256(body).hexdigest() != sha:
            bad.append(sha)
    return bad


def _delete_scratch_objects(settings: Settings, bucket: str, prefix: str) -> None:
    # Single-object deletes: the S3 multi-delete (DeleteObjects) requires a Content-MD5 header that
    # MinIO enforces and recent botocore no longer auto-adds. A per-drill prefix holds few objects,
    # so one delete each is fine and avoids that incompatibility.
    client = _s3(settings)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])


# --- the triad ---------------------------------------------------------------------------------


def run_triad(settings: Settings, handle: ScratchHandle) -> DrillResult:
    """All three legs on the RESTORED copy; any failure → FAIL (doc 08 §8.2)."""
    actual = _scratch_counts(handle)
    mismatches = {
        t: {"expected": exp, "actual": actual.get(t)}
        for t, exp in handle.expected_counts.items()
        if actual.get(t) != exp
    }
    if mismatches:
        return DrillResult("FAIL", "row-count parity failed", {"row_count_mismatch": mismatches})

    orphans = _fk_orphans(handle)
    if orphans:
        return DrillResult("FAIL", "document_version→blob FK check failed", {"fk_orphans": orphans})

    bad = _rehash_scratch_blobs(settings, handle)
    if bad:
        return DrillResult("FAIL", "blob SHA-256 re-hash failed", {"bad_blobs": bad[:20]})

    return DrillResult(
        "PASS",
        "restore verified",
        {"tables": len(handle.expected_counts), "blobs": len(_scratch_blob_shas(handle))},
    )


# --- durable backup (the scheduled / CLI archive; no restore) ---------------------------------


def _latest_checkpoint_bundle(owner_dsn: str) -> bytes | None:
    """Serialize the newest signed ``audit_checkpoint`` row to JSON (doc 12 §8.1 'audit checkpoint
    in every backup'). Best-effort → ``None`` if the table is empty/unreadable; never fails the
    backup. A forward-seam — the restore checkpoint-not-ahead check reads the restored DB + the
    off-host sink, not this bundle."""
    import psycopg

    try:
        with psycopg.connect(**conn_kwargs(owner_dsn)) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT org_id, latest_id, latest_row_hash, timestamp, app_signature "
                "FROM audit_checkpoint ORDER BY latest_id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            return json.dumps(
                {
                    "org_id": str(row[0]),
                    "latest_id": int(row[1]),
                    "latest_row_hash": bytes(row[2]).hex() if row[2] is not None else None,
                    "timestamp": row[3].isoformat() if row[3] is not None else None,
                    "app_signature": (
                        base64.b64encode(bytes(row[4])).decode() if row[4] is not None else None
                    ),
                }
            ).encode()
    except Exception:  # noqa: BLE001 — best-effort; a checkpoint bundle is reference-only
        logger.warning("backup: audit-checkpoint bundle read failed", exc_info=True)
        return None


def build_durable_backup(settings: Settings, *, destination: str) -> dict[str, Any]:
    """Write a real, timestamped, checksum-verified backup archive to ``destination`` — the durable
    artifact the nightly Beat job + ``easysynq backup run`` produce. The archive (v2) carries the
    pg_dump + blob manifest (per-table counts) + the latest audit checkpoint, and — ONLY when
    ``BACKUP_ENCRYPTION_KEY`` is set — the Keycloak realm export + a config snapshot, AES-256-GCM
    encrypted to ``.tar.enc``. With NO key it falls back to a PLAINTEXT ``.tar`` and OMITS the
    realm + config legs (they carry secrets and must never land in cleartext, doc 12 §6.2). No
    restore (that is the drill, plaintext-internal). Runs as the OWNER role; raises ``BackupError``
    on a dump/pack failure. Retention pruning + S3-destination stay v1.x (D-6)."""
    owner_dsn = settings.sync_dsn
    stamp = (
        datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    )
    dest_dir = Path(destination)
    dest_dir.mkdir(parents=True, exist_ok=True)
    encrypt = crypto.key_is_configured(settings.backup_encryption_key)
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dump_path = tmp_path / "db.dump"
        counts, blobs = _capture_and_dump(owner_dsn, dump_path)

        # --- the v2 legs (each degrades gracefully; a failure never blocks the backup) ----------
        extra: dict[str, bytes] = {}
        legs = {"realm_export": "absent", "config_snapshot": "absent", "audit_checkpoint": "absent"}
        # The realm export + config snapshot can carry secrets, so they ride ONLY inside an
        # encrypted archive (doc 12 §6.2). With no key they are OMITTED, not written in cleartext.
        if encrypt:
            realm = realm_export.export_realm(
                base_url=settings.keycloak_admin_url,
                realm=realm_export.realm_name_from_issuer(settings.oidc_issuer),
                admin_user=settings.keycloak_admin_user,
                admin_password=settings.keycloak_admin_password,
            )
            if realm is not None:
                extra[archive.REALM_NAME] = json.dumps(realm, sort_keys=True).encode()
                legs["realm_export"] = "present"
            try:
                extra[archive.CONFIG_NAME] = json.dumps(
                    config_snapshot.build_config_snapshot(owner_dsn), sort_keys=True
                ).encode()
                legs["config_snapshot"] = "present"
            except Exception:  # noqa: BLE001 — snapshot is reference-only; never block the backup
                logger.warning("backup: config snapshot failed", exc_info=True)
        else:
            logger.warning(
                "backup: BACKUP_ENCRYPTION_KEY unset/placeholder — writing an UNENCRYPTED archive "
                "and OMITTING the Keycloak realm + config snapshot (they carry secrets; set "
                "BACKUP_ENCRYPTION_KEY to capture them inside an encrypted archive)."
            )
        # The audit-checkpoint bundle is a signed public checkpoint (no secrets) → always included.
        ckpt = _latest_checkpoint_bundle(owner_dsn)
        if ckpt is not None:
            extra[archive.CHECKPOINT_NAME] = ckpt
            legs["audit_checkpoint"] = "present"

        manifest = archive.build_manifest(
            blobs,
            config={
                "source": "scheduled-backup",
                "blob_count": len(blobs),
                "table_counts": counts,
            },
            realm_export=legs["realm_export"],
            config_snapshot=legs["config_snapshot"],
            audit_checkpoint=legs["audit_checkpoint"],
            encryption_key_ref=crypto.ENCRYPTION_KEY_REF if encrypt else None,
        )

        if encrypt:
            plain = archive.pack_archive(
                dump_path, manifest, tmp_path / "pack", stamp=stamp, extra_files=extra
            )
            final = crypto.encrypt_archive(
                plain,
                dest_dir / f"easysynq-backup-{stamp}.tar.enc",
                secret=settings.backup_encryption_key,
            )
            archive.write_sidecar(final)
        else:
            # No key → plaintext .tar (the unencrypted-fallback warning + the sensitive-leg omission
            # were already logged above when the legs were built).
            final = archive.pack_archive(
                dump_path, manifest, dest_dir, stamp=stamp, extra_files=extra
            )
        verified = archive.verify_archive(final)
    return {
        "archive": str(final),
        "blobs": len(blobs),
        "verified": verified,
        "encrypted": encrypt,
        "legs": legs,
    }


# --- orchestration -----------------------------------------------------------------------------


def run_drill(
    settings: Settings,
    *,
    destination: str,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> DrillResult:
    """Backup → restore-into-scratch → integrity triad → teardown. Writes a real, checksum-verified
    archive to ``destination`` and restores FROM it (proving the destination round-trips, doc 08
    §8.2). Never raises — returns PASS/FAIL. ``after_restore`` is a TEST-ONLY fault injector run
    after the restore + blob copy, before the triad (the negative AC#5 proof)."""
    owner_dsn = settings.sync_dsn
    drill_id = uuid.uuid4().hex
    scratch_db = f"{_SCRATCH_PREFIX}{drill_id}"
    handle: ScratchHandle | None = None
    try:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_path = tmp_path / "db.dump"
            counts, blobs = _capture_and_dump(owner_dsn, dump_path)

            manifest = archive.build_manifest(
                blobs, config={"source": "restore-drill", "blob_count": len(blobs)}
            )
            archive_path = archive.pack_archive(
                dump_path, manifest, Path(destination), stamp=drill_id
            )
            if not archive.verify_archive(archive_path):
                return DrillResult("FAIL", "archive checksum verification failed")

            restore_dump = archive.unpack_dump(archive_path, tmp_path / "restore")
            _sweep_stale_scratch(owner_dsn)
            _create_scratch_db(owner_dsn, scratch_db)
            handle = ScratchHandle(
                owner_dsn=owner_dsn,
                scratch_db=scratch_db,
                scratch_bucket=settings.s3_bucket_restore_scratch,
                object_prefix=f"{drill_id}/",
                expected_counts=counts,
            )
            archive.restore_database(owner_dsn, scratch_db, restore_dump)
            _copy_blobs(settings, blobs, handle.scratch_bucket, handle.object_prefix)

            if after_restore is not None:
                after_restore(handle)

            return run_triad(settings, handle)
    except BackupError as exc:
        return DrillResult("FAIL", str(exc))
    except Exception as exc:
        logger.exception("restore-drill crashed")
        return DrillResult("FAIL", f"drill error: {type(exc).__name__}: {exc}"[:300])
    finally:
        if handle is not None:
            try:
                _drop_scratch_db(owner_dsn, scratch_db)
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.warning("restore-drill: scratch DB teardown failed", exc_info=True)
            try:
                _delete_scratch_objects(settings, handle.scratch_bucket, handle.object_prefix)
            except Exception:  # noqa: BLE001
                logger.warning("restore-drill: scratch bucket teardown failed", exc_info=True)
