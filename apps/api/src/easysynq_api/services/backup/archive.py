"""Backup archive primitives — ``pg_dump``/``pg_restore`` subprocesses + tar/checksum packing
(slice S8b2). Pure of DB state + MinIO (those live in ``drill.py``); these wrap binaries + the FS so
they are easy to reason about and the missing-binary/timeout failure modes are explicit.

A backup archive (manifest v2, S11) is a ``tar`` containing the custom-format ``pg_dump``
(``db.dump``) + a ``manifest.json`` (the blob-snapshot: sha256/size/bucket per position, doc 18
§337; plus per-table row counts for the restore triad and the ``legs`` presence markers) + the
optional legs ``realm.json`` / ``config.json`` / ``audit_checkpoint.json``. With a configured key,
the durable on-disk archive is AES-256-GCM encrypted to ``…tar.enc`` (``crypto.py``); without one it
is a plaintext ``.tar`` and omits secret-bearing legs. The restore-into-scratch DRILL always stays
plaintext-internal. Each on-disk artifact has a sibling ``.sha256`` over its bytes. Blob
*bytes* remain only in the configured source object store; the archive merely references them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
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
# S11 archive legs (arcnames inside the tar; each is optional + degrades gracefully if absent).
REALM_NAME = "realm.json"
CONFIG_NAME = "config.json"
CHECKPOINT_NAME = "audit_checkpoint.json"


class BackupError(Exception):
    """A backup/restore subprocess failed (missing binary, non-zero exit, or timeout). Carries a
    short, user-safe reason; the drill turns this into a RESTORE_TEST_FAILED, never a 500."""


@dataclasses.dataclass(frozen=True, slots=True)
class BlobRef:
    sha256: str
    size_bytes: int
    bucket: str
    object_key: str
    #: The exact stored version this generation references, and how the row obtained it
    #: (``promotion``/``write``/``backfill``, or ``unversioned`` for a bucket that has no versions).
    #: Both are ``None`` for a row written before the binding existed; a v2 manifest carries
    #: neither, and reading one leaves them ``None`` rather than inferring a version.
    object_version_id: str | None = None
    object_version_source: str | None = None


#: Every referenced object is bound to its exact version, and every binding came from the write.
BINDING_SEALED = "sealed"
#: Every bindable object is bound, but at least one binding was observed later by a backfill.
BINDING_OBSERVED = "observed"
#: At least one bindable object has no binding at all.
BINDING_PARTIAL = "partial"
#: A manifest that predates the binding, or a generation with no blobs to bind.
BINDING_ABSENT = "absent"


def binding_state(blobs: list[BlobRef], *, manifest_version: int | None = None) -> str:
    """How completely a generation binds its objects to exact versions.

    ``unversioned`` blobs are counted as bindable-with-nothing-to-bind: the renditions bucket has no
    versions, and those objects are derived and rebuildable, so they neither seal nor spoil a
    generation. A blob with no binding at all does make the generation ``partial``.

    ⚠ ``manifest_version`` is not optional information for a restore: an archive written before the
    binding existed carries blobs that all read back unbound, which is indistinguishable BY THE BLOB
    LIST ALONE from a current generation whose rows were never backfilled. Those are different facts
    — "this archive predates version binding" versus "this generation has unbacked objects" — and
    the state is the only one an operator sees, so the caller passes the version it read. Omitting
    it (as ``build_manifest`` does, writing v3 by construction) judges by the blobs alone.
    """
    if manifest_version is not None and manifest_version < 3:
        return BINDING_ABSENT
    bindable = [b for b in blobs if b.object_version_source != "unversioned"]
    if not bindable:
        return BINDING_ABSENT
    if any(b.object_version_source is None for b in bindable):
        return BINDING_PARTIAL
    if any(b.object_version_source == "backfill" for b in bindable):
        return BINDING_OBSERVED
    return BINDING_SEALED


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


def build_manifest(
    blobs: list[BlobRef],
    *,
    config: dict[str, Any],
    realm_export: str = "absent",
    config_snapshot: str = "absent",
    audit_checkpoint: str = "absent",
    encryption_key_ref: str | None = None,
    encrypted: bool = False,
) -> dict[str, Any]:
    """The MinIO blob-snapshot manifest (doc 18 §337) + recorded backup config + the S11 ``legs``
    presence markers, encryption state, and ``encryption_key_ref``. ``config`` may carry
    ``table_counts`` so the restore triad has point-in-time expected counts without re-reading the
    (gone) source DB.

    v3 adds the per-object version binding and records the generation's ``version_binding``
    state in ``config``. A v2 manifest stays readable and restores exactly as before: absent
    bindings are read as absent, never inferred."""
    return {
        "manifest_version": 3,
        "config": {**config, "version_binding": binding_state(blobs)},
        "blobs": [dataclasses.asdict(b) for b in blobs],
        "legs": {
            "realm_export": realm_export,
            "config_snapshot": config_snapshot,
            "audit_checkpoint": audit_checkpoint,
        },
        "encrypted": encrypted,
        "encryption_key_ref": encryption_key_ref,
    }


def blob_refs_from_manifest(manifest: dict[str, Any]) -> list[BlobRef]:
    """Rehydrate the manifest's blob list, including the version binding when the manifest carries
    one. A v2 manifest has no binding keys, and this reads them as absent rather than inferring a
    version — a generation cannot become exact retroactively."""
    refs: list[BlobRef] = []
    for b in manifest.get("blobs", []):
        source = b.get("object_version_source")
        version = b.get("object_version_id")
        refs.append(
            BlobRef(
                sha256=b["sha256"],
                size_bytes=int(b["size_bytes"]),
                bucket=b["bucket"],
                object_key=b["object_key"],
                object_version_id=str(version) if version else None,
                object_version_source=str(source) if source else None,
            )
        )
    return refs


def _sidecar(path: Path) -> Path:
    """The ``.sha256`` checksum sidecar for ``path`` — appends ``.sha256`` to the FULL name so it is
    correct for both ``foo.tar`` and ``foo.tar.enc`` (``Path.with_suffix`` would mangle the ``.enc``
    case, yielding ``foo.tar.sha256``), so always go through this helper."""
    return path.with_name(path.name + ".sha256")


def write_sidecar(path: Path) -> Path:
    """Write the ``.sha256`` sidecar for ``path`` (over the on-disk bytes); return the sidecar."""
    sidecar = _sidecar(path)
    sidecar.write_text(sha256_file(path))
    return sidecar


def pack_archive(
    dump_path: Path,
    manifest: dict[str, Any],
    dest_dir: Path,
    *,
    stamp: str,
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    """Tar the dump + manifest (+ optional ``extra_files`` legs) into
    ``{dest_dir}/easysynq-backup-{stamp}.tar`` and write a sibling ``.sha256``. ``dest_dir`` is
    created if absent. The output is the PLAINTEXT tar; the durable path encrypts it afterwards."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dump_path.parent / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    archive = dest_dir / f"easysynq-backup-{stamp}.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(dump_path, arcname=_DUMP_NAME)
        tar.add(manifest_path, arcname=_MANIFEST_NAME)
        for arcname, data in (extra_files or {}).items():
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    write_sidecar(archive)
    return archive


def verify_archive(archive: Path) -> bool:
    """Re-read the archive and compare to its committed ``.sha256`` sidecar (doc 08 §8.2 'checksum
    verified'). Works for both ``.tar`` and ``.tar.enc`` (the sidecar is over the on-disk bytes).
    False if the sidecar is missing or the bytes do not match."""
    sidecar = _sidecar(archive)
    if not sidecar.exists():
        return False
    return sha256_file(archive) == sidecar.read_text().strip()


def unpack_dump(archive: Path, into: Path) -> Path:
    """Extract ``db.dump`` (+ manifest) from a verified PLAINTEXT archive; return the dump path."""
    with tarfile.open(archive, "r") as tar:
        tar.extract(_DUMP_NAME, path=into, filter="data")
    return into / _DUMP_NAME


def read_manifest(archive: Path) -> dict[str, Any]:
    """Extract + parse ``manifest.json`` from a PLAINTEXT archive (the blob-snapshot, table counts,
    legs, encryption_key_ref the restore needs). Raises ``BackupError`` if missing/unreadable."""
    try:
        with tarfile.open(archive, "r") as tar:
            member = tar.extractfile(_MANIFEST_NAME)
            if member is None:
                raise BackupError("archive has no manifest.json")
            data: dict[str, Any] = json.loads(member.read().decode("utf-8"))
            return data
    except (tarfile.TarError, json.JSONDecodeError, OSError) as exc:
        raise BackupError(f"manifest read failed: {exc}"[:200]) from exc
