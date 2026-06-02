"""Backup archive primitives — ``pg_dump``/``pg_restore`` subprocesses + tar/checksum packing
(slice S8b2). Pure of DB state + MinIO (those live in ``drill.py``); these wrap binaries + the FS so
they are easy to reason about and the missing-binary/timeout failure modes are explicit.

A backup archive is a single ``tar`` containing the custom-format ``pg_dump`` (``db.dump``) + a
``manifest.json`` (the blob-snapshot: sha256/size/bucket per position, doc 18 §337), written to the
configured destination with a sibling ``.sha256`` checksum. Blob *bytes* stay in MinIO/WORM
(separately durable); the manifest references them. Archive **envelope encryption** is recorded
(``encryption_key_ref``) but deferred to S11.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from .dsn import libpq_env

# pg_dump/pg_restore can be slow on a large DB; cap so a hung binary FAILs the drill (≤ RTO target,
# doc 08 §8.2) rather than hanging the worker. Generous for MVP-scale data.
DUMP_TIMEOUT_S = 1800
RESTORE_TIMEOUT_S = 1800

_DUMP_NAME = "db.dump"
_MANIFEST_NAME = "manifest.json"


class BackupError(Exception):
    """A backup/restore subprocess failed (missing binary, non-zero exit, or timeout). Carries a
    short, user-safe reason; the drill turns this into a RESTORE_TEST_FAILED, never a 500."""


@dataclasses.dataclass(frozen=True, slots=True)
class BlobRef:
    sha256: str
    size_bytes: int
    bucket: str
    object_key: str


def _run(cmd: list[str], *, env_extra: dict[str, str], timeout: int, what: str) -> None:
    import os

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell; env carries the PG* creds
            cmd,
            env={**os.environ, **env_extra},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError(
            f"{what}: '{cmd[0]}' not found (postgresql-client not installed)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{what}: timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise BackupError(f"{what}: exit {proc.returncode}: {' / '.join(tail)[:300]}")


def dump_database(owner_dsn: str, dump_path: Path, *, snapshot: str | None = None) -> None:
    """``pg_dump -Fc`` the owner DB to ``dump_path``. ``snapshot`` (from ``pg_export_snapshot()``)
    ties the dump to a captured row-count snapshot for race-free parity (doc 08 §8.2)."""
    cmd = ["pg_dump", "-Fc", "-f", str(dump_path)]
    if snapshot:
        cmd.append(f"--snapshot={snapshot}")
    _run(cmd, env_extra=libpq_env(owner_dsn), timeout=DUMP_TIMEOUT_S, what="pg_dump")


def restore_database(owner_dsn: str, scratch_db: str, dump_path: Path) -> None:
    """``pg_restore`` the archive into the fresh scratch DB. ``--no-owner --no-privileges`` so the
    restore does not depend on re-applying role grants/ownership (the scratch DB is owned by the
    restoring role); ``--exit-on-error`` so a partial restore FAILs loudly rather than silently."""
    _run(
        [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "-d",
            scratch_db,
            str(dump_path),
        ],
        env_extra=libpq_env(owner_dsn, dbname=scratch_db),
        timeout=RESTORE_TIMEOUT_S,
        what="pg_restore",
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(blobs: list[BlobRef], *, config: dict[str, Any]) -> dict[str, Any]:
    """The MinIO blob-snapshot manifest (doc 18 §337) + recorded backup config."""
    return {
        "manifest_version": 1,
        "config": config,
        "blobs": [dataclasses.asdict(b) for b in blobs],
    }


def pack_archive(dump_path: Path, manifest: dict[str, Any], dest_dir: Path, *, stamp: str) -> Path:
    """Tar the dump + manifest into ``{dest_dir}/easysynq-backup-{stamp}.tar`` and write a sibling
    ``.sha256``. ``dest_dir`` is created if absent (the configured destination, a mounted path)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dump_path.parent / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    archive = dest_dir / f"easysynq-backup-{stamp}.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(dump_path, arcname=_DUMP_NAME)
        tar.add(manifest_path, arcname=_MANIFEST_NAME)
    archive.with_suffix(".tar.sha256").write_text(sha256_file(archive))
    return archive


def verify_archive(archive: Path) -> bool:
    """Re-read the archive and compare to its committed ``.sha256`` sidecar (doc 08 §8.2 'checksum
    verified'). False if the sidecar is missing or the bytes do not match."""
    sidecar = archive.with_suffix(".tar.sha256")
    if not sidecar.exists():
        return False
    return sha256_file(archive) == sidecar.read_text().strip()


def unpack_dump(archive: Path, into: Path) -> Path:
    """Extract ``db.dump`` (+ manifest) from a verified archive; return the dump path."""
    with tarfile.open(archive, "r") as tar:
        tar.extract(_DUMP_NAME, path=into, filter="data")
    return into / _DUMP_NAME
