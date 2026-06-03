"""Operator-grade WORM-aware restore (slice S11, doc 12 §8.2 / R37, doc 18 §7/§9).

``easysynq restore <archive>`` restores TO A VERIFIED TARGET: it decrypts + verifies the archive,
restores PostgreSQL into a FRESH scratch DATABASE, copies the manifested blobs into the FRESH,
non-WORM ``restore-scratch`` bucket (the blob bytes are READ from their content-addressed source —
the live object-locked vault is never mutated), then runs the integrity triad, a
**checkpoint-not-ahead** tamper check, and a **restored-chain re-verify** — then LEAVES THE TARGET
STANDING for the operator to cut over to. It NEVER mutates the live vault, NEVER auto-cuts-over.

    HARDENING TODO (S11+): automated in-place LIVE cutover (repoint DATABASE_URL + the MinIO bucket,
    re-import the Keycloak realm + config, then reindex + mirror-sync) is a tracked hardening-stage
    item. The owner decision (S11) is restore-to-VERIFIED-TARGET: the production cutover stays a
    DOCUMENTED OPERATOR STEP (docs/runbooks/backup-restore.md, "Cut over"), never automated here.
    reindex + mirror-sync therefore run POST-cutover (they would corrupt the live index/mirror with
    not-yet-cut-over data if run now); the realm + config legs are recorded for that step.

Reuses the S8b2 drill primitives wholesale (scratch-DB create/teardown, blob copy, the triad). Runs
as the OWNER role (``settings.sync_dsn``) like the drill, off the event loop, and NEVER raises — a
missing binary / crash / wrong key is an honest FAIL, never a 500.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ...config import Settings
from . import archive, crypto, drill
from .archive import BackupError
from .crypto import BackupCryptoError
from .drill import ScratchHandle
from .dsn import conn_kwargs

logger = logging.getLogger("easysynq.backup.restore")

_RESTORE_PREFIX = "restore_easysynq_"

# Seam: fetch the latest off-host checkpoint latest_id for an org (separate custody, R13/D-8). The
# real impl lists the off-host audit-checkpoints bucket; tests inject a deterministic value.
FetchOffHost = Callable[[Settings, uuid.UUID], "int | None"]


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreResult:
    """The outcome of a restore-to-verified-target.

    ``PASS`` — a verified, ready-to-cutover target is left standing (scratch_db/bucket/prefix set).
    ``FLAGGED`` — checkpoint-not-ahead tamper-suspicion; the target is torn down; re-run with
    ``audit_checkpoint_ack=True`` to proceed (the acknowledgement is audited). ``FAIL`` — archive /
    restore / triad / chain failure; the target is torn down. Never raises."""

    result: str  # "PASS" | "FAIL" | "FLAGGED"
    reason: str
    scratch_db: str | None = None
    scratch_bucket: str | None = None
    object_prefix: str | None = None
    restored_head_id: int | None = None
    checkpoint_check: dict[str, Any] = dataclasses.field(default_factory=dict)
    chain_verify: dict[str, Any] = dataclasses.field(default_factory=dict)
    triad: dict[str, Any] = dataclasses.field(default_factory=dict)
    details: dict[str, Any] = dataclasses.field(default_factory=dict)


# --- the checkpoint-not-ahead verdict (PURE — unit-tested exhaustively) ------------------------


def checkpoint_verdict(
    restored_head: int, bundled: int | None, off_host: int | None
) -> tuple[str, list[str]]:
    """The headline tamper guard (doc 12 §8.2 / R37). Compares the RESTORED chain head against the
    bundled checkpoint AND the SEPARATELY-CUSTODIED off-host checkpoint.

    A checkpoint whose latest_id exceeds the restored head means the restore is missing audit rows
    that were anchored — either a stale/older-than-checkpoint backup, a deliberate point-in-time
    target, or a TRUNCATED tail (tamper). We cannot tell which, so we FLAG (require an audited
    operator ack), never silently PASS. The off-host leg compares against ``restored_head`` (the
    real restored head), NOT against ``bundled`` — so a tamperer who truncated the tail AND rebuilt
    a matching bundled checkpoint is still caught by the independently-custodied off-host one. A
    MISSING off-host checkpoint is ``UNVERIFIABLE`` → also FLAGGED (an install with no genuine
    off-host anchor cannot prove the restored chain is complete; R13). Returns (verdict, flags)."""
    flags: list[str] = []
    if bundled is not None and bundled > restored_head:
        flags.append(
            f"bundled checkpoint latest_id {bundled} is ahead of restored head {restored_head}"
        )
    if off_host is None:
        flags.append("no reachable off-host checkpoint — cannot rule out a truncated audit tail")
    elif off_host > restored_head:
        flags.append(
            f"off-host checkpoint latest_id {off_host} is ahead of restored head {restored_head}"
        )
    return ("FLAGGED" if flags else "OK"), flags


# --- restored scratch-DB reads (sync; owner role) ----------------------------------------------


def _scratch_max_audit_id(owner_dsn: str, scratch_db: str) -> int:
    import psycopg

    with (
        psycopg.connect(**conn_kwargs(owner_dsn, dbname=scratch_db), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT coalesce(max(id), 0) FROM audit_event")
        row = cur.fetchone()
        return int(row[0]) if row else 0


def _scratch_max_bundled_checkpoint(owner_dsn: str, scratch_db: str) -> int | None:
    import psycopg

    with (
        psycopg.connect(**conn_kwargs(owner_dsn, dbname=scratch_db), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT max(latest_id) FROM audit_checkpoint")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _scratch_canonical_version(owner_dsn: str, scratch_db: str) -> int:
    """The pinned canonical_serialize version FROM THE RESTORED DB (do not hardcode 1 — a future v2
    chain must re-verify under its own spec)."""
    import psycopg

    with (
        psycopg.connect(**conn_kwargs(owner_dsn, dbname=scratch_db), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        try:
            cur.execute("SELECT canonical_serialize_version FROM system_config LIMIT 1")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 1
        except psycopg.Error:
            return 1


# --- off-host checkpoint fetch (default real impl; separate custody) ---------------------------


def _off_host_buckets(settings: Settings, org_id: uuid.UUID) -> list[str]:
    """The bucket(s) to scan for off-host checkpoints. Mirrors the S6 writer (sink.py): each enabled
    ``worm_bucket`` sink writes to ``connection.bucket`` or the default audit-checkpoints bucket, so
    the reader MUST honour the same per-sink override, else a custom-bucket sink is never found
    (always FLAGGED). Reads the live install's sink config; best-effort + always includes the
    default so a fresh-host restore with no sink still scans the default location."""
    buckets = {settings.s3_bucket_audit_checkpoints}
    try:
        import psycopg

        with (
            psycopg.connect(**conn_kwargs(settings.sync_dsn), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT connection FROM audit_checkpoint_sink "
                "WHERE org_id = %s AND enabled = true AND kind = 'worm_bucket'",
                (org_id,),
            )
            for (connection,) in cur.fetchall():
                bucket = (connection or {}).get("bucket")
                if bucket:
                    buckets.add(bucket)
    except Exception:  # noqa: BLE001 — best-effort; always falls back to the default bucket
        logger.warning("restore: off-host sink bucket lookup failed", exc_info=True)
    return sorted(buckets)


def _default_fetch_off_host(settings: Settings, org_id: uuid.UUID) -> int | None:
    """Scan the off-host audit-checkpoint bucket(s) and return the max ``latest_id`` for ``org_id``
    (parsed from the ``checkpoints/{org}/{latest_id}-{ts}.json`` key the S6 sink writes). Uses the
    SEPARATE audit-sink credentials (falling back to the vault creds only as a dev convenience, as
    the S6 sink does) and honours each sink's ``connection.bucket`` override. Returns ``None`` if
    unreachable/empty — which the verdict treats as UNVERIFIABLE → FLAGGED, never a silent PASS."""
    try:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.audit_sink_access_key or settings.s3_access_key,
            aws_secret_access_key=settings.audit_sink_secret_key or settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        paginator = client.get_paginator("list_objects_v2")
        prefix = f"checkpoints/{org_id}/"
        best: int | None = None
        for bucket in _off_host_buckets(settings, org_id):
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    name = obj["Key"].rsplit("/", 1)[-1]  # "{latest_id}-{ts}.json"
                    head = name.split("-", 1)[0]
                    if head.isdigit():
                        value = int(head)
                        best = value if best is None else max(best, value)
        return best
    except Exception:  # noqa: BLE001 — an unreachable off-host anchor is UNVERIFIABLE, not a crash
        logger.warning("restore: off-host checkpoint fetch failed", exc_info=True)
        return None


def _restored_org_id(owner_dsn: str, scratch_db: str) -> uuid.UUID | None:
    """The single-org id (D1) in the restored DB, for the off-host fetch + chain re-verify."""
    import psycopg

    with (
        psycopg.connect(**conn_kwargs(owner_dsn, dbname=scratch_db), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT id FROM organization ORDER BY id LIMIT 1")
        row = cur.fetchone()
        return uuid.UUID(str(row[0])) if row else None


# --- restored-chain re-verify (reuses the FROZEN verify_chain over the scratch DB) -------------


def _reverify_chain(owner_dsn: str, scratch_db: str, version: int) -> dict[str, Any]:
    """Re-walk the restored audit chain with the frozen ``verify_chain`` (over an owner session on
    the scratch DB), per org. Pending (unlinked) tail is reported, not a break. Returns a summary.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ..audit.verify import verify_chain

    async def _run() -> dict[str, Any]:
        url = make_url(owner_dsn).set(database=scratch_db)
        engine = create_async_engine(url)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sm() as session:
                org_ids = (
                    await session.execute(text("SELECT DISTINCT org_id FROM audit_event"))
                ).all()
                verified = True
                checked = pending = 0
                breaks: list[dict[str, Any]] = []
                for (org_id,) in org_ids:
                    result = await verify_chain(session, org_id, version=version)
                    verified = verified and result.verified
                    checked += result.checked
                    pending += result.pending
                    breaks.extend({"at_id": b.at_id, "reason": b.reason} for b in result.breaks)
                return {
                    "verified": verified,
                    "checked": checked,
                    "pending": pending,
                    "breaks": breaks[:20],
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


# --- orchestration -----------------------------------------------------------------------------


def _sweep_stale_restore(owner_dsn: str) -> None:
    """Best-effort: drop leftover ``restore_easysynq_*`` targets from a prior restore (a new restore
    supersedes an un-cut-over one). Distinct from the drill's ``scratch_easysynq_*`` sweep, so the
    nightly drill never destroys a standing verified target (and vice-versa)."""
    import psycopg
    from psycopg import sql

    try:
        with (
            psycopg.connect(**conn_kwargs(owner_dsn), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (_RESTORE_PREFIX + "%",)
            )
            for (name,) in cur.fetchall():
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
    except Exception:  # noqa: BLE001 — best-effort cleanup must not fail the restore
        logger.warning("restore: stale-target sweep skipped", exc_info=True)


def run_restore(
    settings: Settings,
    *,
    archive_path: str,
    audit_checkpoint_ack: bool = False,
    fetch_off_host: FetchOffHost | None = None,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> RestoreResult:
    """Restore ``archive_path`` to a verified target. Never raises. ``after_restore`` is a TEST-ONLY
    fault injector (run after restore + blob copy, before the triad); ``fetch_off_host`` overrides
    the off-host checkpoint fetch (tests inject a deterministic value)."""
    owner_dsn = settings.sync_dsn
    fetch = fetch_off_host or _default_fetch_off_host
    src = Path(archive_path)
    restore_id = uuid.uuid4().hex
    scratch_db = f"{_RESTORE_PREFIX}{restore_id}"
    handle: ScratchHandle | None = None
    keep_standing = False
    try:
        if not src.exists():
            return RestoreResult("FAIL", f"archive not found: {archive_path}")
        if not archive.verify_archive(src):
            return RestoreResult("FAIL", "archive checksum verification failed")

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 1. decrypt if encrypted → a plaintext tar the existing primitives can read
            if crypto.is_encrypted_archive(src):
                plain = crypto.decrypt_archive(
                    src, tmp_path / "archive.tar", secret=settings.backup_encryption_key
                )
            else:
                plain = src

            # 2. read the manifest (point-in-time blob set + table counts + legs)
            manifest = archive.read_manifest(plain)
            blobs = [
                archive.BlobRef(
                    sha256=b["sha256"],
                    size_bytes=int(b["size_bytes"]),
                    bucket=b["bucket"],
                    object_key=b["object_key"],
                )
                for b in manifest.get("blobs", [])
            ]
            counts = (manifest.get("config") or {}).get("table_counts") or {}
            legs = manifest.get("legs") or {}

            # 3. restore PG into a FRESH scratch DB
            restore_dump = archive.unpack_dump(plain, tmp_path / "restore")
            _sweep_stale_restore(owner_dsn)
            drill._create_scratch_db(owner_dsn, scratch_db)
            handle = ScratchHandle(
                owner_dsn=owner_dsn,
                scratch_db=scratch_db,
                scratch_bucket=settings.s3_bucket_restore_scratch,
                object_prefix=f"{restore_id}/",
                expected_counts=counts,
            )
            archive.restore_database(owner_dsn, scratch_db, restore_dump)

            # 4. copy blobs into the FRESH non-WORM bucket (source = a READ; the locked vault is
            #    never written). Destination is asserted non-vault for defence-in-depth.
            if handle.scratch_bucket == settings.s3_bucket_documents:  # pragma: no cover - guard
                return RestoreResult("FAIL", "refusing to restore into the WORM documents bucket")
            drill._copy_blobs(settings, blobs, handle.scratch_bucket, handle.object_prefix)

            if after_restore is not None:
                after_restore(handle)

            # 5. integrity triad (row-count parity skipped for a legacy archive with no counts)
            triad = drill.run_triad(settings, handle)
            triad_detail = {"reason": triad.reason, **triad.details}
            if not counts:
                triad_detail["row_count_parity"] = "skipped (legacy archive, no manifest counts)"
            if triad.result == "FAIL":
                return RestoreResult(
                    "FAIL", f"integrity triad failed: {triad.reason}", triad=triad_detail
                )

            # 6. checkpoint-not-ahead (BEFORE chain re-verify, so a tamper flag surfaces first)
            head = _scratch_max_audit_id(owner_dsn, scratch_db)
            bundled = _scratch_max_bundled_checkpoint(owner_dsn, scratch_db)
            org_id = _restored_org_id(owner_dsn, scratch_db)
            off_host = fetch(settings, org_id) if org_id is not None else None
            verdict, flags = checkpoint_verdict(head, bundled, off_host)
            ckpt_detail = {
                "verdict": verdict,
                "restored_head": head,
                "bundled": bundled,
                "off_host": off_host,
                "flags": flags,
                "acknowledged": audit_checkpoint_ack if verdict == "FLAGGED" else False,
            }
            if verdict == "FLAGGED" and not audit_checkpoint_ack:
                return RestoreResult(
                    "FLAGGED",
                    "audit checkpoint is ahead of the restored target — re-run with "
                    "--audit-checkpoint-ack to proceed (the acknowledgement is audited)",
                    restored_head_id=head,
                    checkpoint_check=ckpt_detail,
                    triad=triad_detail,
                )

            # 7. restored-chain re-verify (frozen verify_chain; version from the restored DB)
            version = _scratch_canonical_version(owner_dsn, scratch_db)
            chain = _reverify_chain(owner_dsn, scratch_db, version)
            if not chain["verified"]:
                return RestoreResult(
                    "FAIL",
                    "restored audit chain re-verify failed",
                    restored_head_id=head,
                    checkpoint_check=ckpt_detail,
                    chain_verify=chain,
                    triad=triad_detail,
                )

            # 8. PASS → leave the verified target standing for the documented operator cutover
            keep_standing = True
            return RestoreResult(
                "PASS",
                "restore verified — target ready for operator cutover",
                scratch_db=scratch_db,
                scratch_bucket=handle.scratch_bucket,
                object_prefix=handle.object_prefix,
                restored_head_id=head,
                checkpoint_check=ckpt_detail,
                chain_verify=chain,
                triad=triad_detail,
                details={
                    "blobs": len(blobs),
                    "legs": legs,
                    "post_cutover_actions": ["reindex", "mirror-sync", "realm/config re-import"],
                },
            )
    except BackupCryptoError as exc:
        return RestoreResult("FAIL", f"decrypt failed: {exc}")
    except BackupError as exc:
        return RestoreResult("FAIL", str(exc))
    except Exception as exc:
        logger.exception("restore crashed")
        return RestoreResult("FAIL", f"restore error: {type(exc).__name__}: {exc}"[:300])
    finally:
        if handle is not None and not keep_standing:
            try:
                drill._drop_scratch_db(owner_dsn, scratch_db)
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.warning("restore: scratch DB teardown failed", exc_info=True)
            try:
                drill._delete_scratch_objects(settings, handle.scratch_bucket, handle.object_prefix)
            except Exception:  # noqa: BLE001
                logger.warning("restore: scratch bucket teardown failed", exc_info=True)


def discard_target(settings: Settings, scratch_db: str) -> None:
    """Tear down a left-standing verified target (operator ``--discard``) — BOTH legs: the scratch
    DB AND the copied blobs under its prefix in the non-WORM restore-scratch bucket (else a
    discarded restore orphans a copy of the org's Effective blob set). The prefix is derived from
    the DB name (scratch_db = _RESTORE_PREFIX + restore_id; object prefix = restore_id/)."""
    drill._drop_scratch_db(settings.sync_dsn, scratch_db)
    prefix = scratch_db.removeprefix(_RESTORE_PREFIX) + "/"
    try:
        drill._delete_scratch_objects(settings, settings.s3_bucket_restore_scratch, prefix)
    except Exception:  # noqa: BLE001 — best-effort object cleanup must not fail the discard
        logger.warning("restore: discard scratch-object cleanup failed", exc_info=True)
