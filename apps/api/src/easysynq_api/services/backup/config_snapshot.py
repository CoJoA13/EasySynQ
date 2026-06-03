"""Config snapshot for the backup archive (slice S11, doc 08 §8.1).

A portability / disaster-recovery reference JSON of the install's config rows, so an operator
restoring onto a fresh host can re-establish configuration. No plaintext secrets are duplicated:
federation secrets live in Keycloak (captured by the realm export), the off-host sink credential is
a SEPARATE Docker secret (D-8) and ``audit_checkpoint_sink.connection`` holds non-secret config,
and the bootstrap secret is stored salted-hashed. The snapshot can still carry sensitive config, so
it rides INSIDE the encrypted archive.

Runs as the OWNER role (a sync psycopg read inside ``asyncio.to_thread``, like the rest of the
drill). Never raises into the caller's hot path — ``build_durable_backup`` wraps it best-effort.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from .dsn import conn_kwargs

# Fixed allow-list of per-install config tables (NOT user input → the f-string SELECT is safe).
_TABLES = (
    "organization",
    "system_config",
    "storage_config",
    "backup_policy",
    "audit_checkpoint_sink",
)


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """UUID / datetime / bytes → str so ``json.dumps`` succeeds when packing the archive."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID | datetime.datetime | datetime.date):
            out[key] = str(value)
        elif isinstance(value, bytes | memoryview):
            out[key] = bytes(value).hex()
        else:
            out[key] = value
    return out


def build_config_snapshot(owner_dsn: str) -> dict[str, Any]:
    """Read the config rows under the owner role; return a JSON-serializable snapshot dict."""
    import psycopg
    from psycopg.rows import dict_row

    out: dict[str, Any] = {"snapshot_version": 1, "tables": {}}
    with psycopg.connect(**conn_kwargs(owner_dsn), row_factory=dict_row) as conn:
        for table in _TABLES:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table}")  # noqa: S608 — fixed allow-list, no user input
                out["tables"][table] = [_jsonable(r) for r in cur.fetchall()]
    return out
