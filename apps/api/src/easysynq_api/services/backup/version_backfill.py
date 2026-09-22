"""Backfill the blob→object version binding for rows written before it existed (migration 0093).

⚠ What a backfilled binding attests, and what it does not: it records the version that is CURRENT
for that object at backfill time. It cannot prove that version is the one originally sealed, because
the row carried no version to compare against — that is the whole reason the column now exists.
Generations over backfilled rows are therefore reported ``observed``, never ``sealed``; only
generations whose blobs were bound by their own writes can be ``sealed``.

Runs as the OWNER role from the worker image, like the rest of this package, and never raises: a
missing or unreadable object is reported and leaves its row unbound, never guessed.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from ...config import Settings, get_settings
from ..vault import version_binding
from .drill import _autocommit, _s3

logger = logging.getLogger("easysynq.backup")


@dataclasses.dataclass(frozen=True, slots=True)
class BackfillReport:
    """Counts only — never object keys or bytes, matching the rest of this package's reporting."""

    examined: int = 0
    bound: int = 0
    unversioned: int = 0
    missing: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


def _unbound_rows(settings: Settings) -> list[tuple[str, str, str]]:
    with _autocommit(settings.sync_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sha256, bucket, object_key FROM blob WHERE object_version_source IS NULL"
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def _record(settings: Settings, sha256: str, columns: dict[str, str | None]) -> None:
    # `WHERE object_version_source IS NULL` makes this one-way: a binding already on the row — in
    # particular one written by its own promotion — is never overwritten by a later observation,
    # even if this command runs twice or races a concurrent write.
    with _autocommit(settings.sync_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE blob SET object_version_id = %s, object_version_source = %s"
            " WHERE sha256 = %s AND object_version_source IS NULL",
            (columns["object_version_id"], columns["object_version_source"], sha256),
        )


def backfill_version_bindings() -> dict[str, Any]:
    """Bind every unbound blob row to the version its object currently has.

    Idempotent: rows that already carry a binding are not examined at all. Returns counts plus a
    reminder of what the result means, so a caller cannot read `bound` as `sealed`.
    """
    settings = get_settings()
    try:
        return _backfill(settings)
    except Exception as exc:
        # The WHOLE body, not merely the catalog read: client construction from misconfigured
        # settings, and anything the per-row loop reaches, must produce the same clean FAIL this
        # command gives everywhere else — the CLI has no handler of its own, so a leak here is a
        # traceback in an operator's terminal.
        logger.exception("version backfill failed")
        return {"result": "FAIL", "reason": f"{type(exc).__name__}: {exc}"[:200]}


def _backfill(settings: Settings) -> dict[str, Any]:
    rows = _unbound_rows(settings)
    client = _s3(settings)
    examined = bound = unversioned = missing = failed = 0
    for sha256, bucket, object_key in rows:
        examined += 1
        try:
            head = client.head_object(Bucket=bucket, Key=object_key)
        except Exception as exc:  # noqa: BLE001 — absent/unreadable object stays unbound
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                missing += 1
            else:
                failed += 1
            continue
        version_id = head.get("VersionId")
        columns = version_binding.write_binding(
            version_id if isinstance(version_id, str) and version_id else None
        )
        if columns["object_version_source"] == version_binding.UNVERSIONED:
            unversioned += 1
        else:
            # Observed now, not proven sealed: record the weaker provenance deliberately.
            columns = version_binding.binding(
                columns["object_version_id"], source=version_binding.BACKFILL
            )
            bound += 1
        try:
            _record(settings, sha256, columns)
        except Exception:
            logger.exception("version backfill could not record a binding")
            failed += 1
            if columns["object_version_source"] == version_binding.BACKFILL:
                bound -= 1
            else:
                unversioned -= 1

    report = BackfillReport(
        examined=examined, bound=bound, unversioned=unversioned, missing=missing, failed=failed
    )
    return {
        "result": "FAIL" if failed else "OK",
        **report.as_dict(),
        "attests": "version observed at backfill time, not the version originally sealed",
    }
