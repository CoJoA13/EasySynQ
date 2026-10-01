"""Scheduled capture/replay serialized beyond the lifetime of a database connection.

The persistent policy lock inode must not be removed: an old to_thread writer can continue after
its task loses the database row lock. Destinations must support shared exclusive advisory locks.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from ...config import Settings
from . import archive, crypto, drill


def _candidate(
    path: Path,
    settings: Settings,
    family: UUID,
) -> tuple[datetime, dict[str, Any]] | None:
    encrypted = crypto.key_is_configured(settings.backup_encryption_key)
    if path.name.endswith(".enc") != encrypted or not archive.verify_archive(path):
        return None
    with TemporaryDirectory() as tmp:
        plain = (
            crypto.decrypt_archive(
                path, Path(tmp) / "replay.tar", secret=settings.backup_encryption_key
            )
            if encrypted
            else path
        )
        manifest = archive.read_manifest(plain)
    metadata = manifest["config"]["scheduled_attempt"]
    started = datetime.fromisoformat(metadata["started_at"])
    if (
        started.tzinfo is None
        or metadata["family"] != family.hex
        or manifest["encrypted"] != encrypted
    ):
        return None
    return started.astimezone(UTC), {
        "archive": str(path),
        "blobs": len(manifest["blobs"]),
        "verified": True,
        "encrypted": encrypted,
        "legs": manifest["legs"],
    }


def build_scheduled_backup(
    settings: Settings,
    *,
    destination: str,
    policy_id: UUID,
    family_id: UUID,
    clock: Callable[[], datetime],
    is_due: Callable[[datetime], bool],
    reusable: Callable[[datetime, datetime], bool],
) -> tuple[datetime, dict[str, Any]] | None:
    """Return the consumed capture time and result; busy/not due consumes no schedule time.

    A checksummed/authenticated completed candidate is reusable only when the caller confirms
    that no newer occurrence is due. Invalid/old candidates remain retained, never overwritten.
    """
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / f".easysynq-backup-{policy_id.hex}.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        # All other lock failures are real backup errors. Close releases the lock even on error;
        # the inode remains so a later opener cannot bypass an older in-flight writer.
        started = clock().astimezone(UTC)
        if not is_due(started):
            return None
        for path in sorted(dest.glob(f"easysynq-backup-*-{family_id.hex}-*.tar*"), reverse=True):
            if not drill._DURABLE_ARCHIVE_RE.fullmatch(path.name):
                continue
            try:
                candidate = _candidate(path, settings, family_id)
            except (
                archive.BackupError,
                crypto.BackupCryptoError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
            ):
                continue
            if candidate is not None and reusable(candidate[0], started):
                return candidate
        stamp = started.strftime("%Y%m%dT%H%M%SZ") + f"-{family_id.hex}-{uuid4().hex[:8]}"
        with TemporaryDirectory(prefix=".easysynq-scheduled-", dir=dest) as staging:
            result = drill._build_durable_backup(
                settings,
                destination=staging,
                stamp=stamp,
                scheduled_attempt={"family": family_id.hex, "started_at": started.isoformat()},
            )
            if not result.get("verified", False):
                raise archive.BackupError("scheduled archive failed checksum verification")
            staged = Path(result["archive"])
            final = dest / staged.name
            # Cooperating scheduled writers hold the same policy lock, including continued old
            # threads. Refuse even a forced UUID collision rather than replacing retained bytes.
            if os.path.lexists(final) or os.path.lexists(final.with_name(final.name + ".sha256")):
                raise archive.BackupError("scheduled archive destination already exists")
            staged.rename(final)
            staged.with_name(staged.name + ".sha256").rename(
                final.with_name(final.name + ".sha256")
            )
            return started, {
                **result,
                "archive": str(final),
                "verified": archive.verify_archive(final),
            }
