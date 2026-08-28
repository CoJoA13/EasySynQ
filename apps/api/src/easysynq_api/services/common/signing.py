"""Signing-key availability — the fail-closed gate (RES-SECRETS-VOLUME-SILENT-EPHEMERAL-KEY).

Two Ed25519 keys must be durably persisted for the product's tamper-evidence claims to hold:

* the **verify-token** key (``verify_token_signing_key_path``) — the worker mints controlled-copy
  QR tokens with it and the api verifies them, so a key that does not outlive the process makes
  every printed QR read UNKNOWN;
* the **audit-checkpoint** key (``audit_checkpoint_signing_key_path``) — every off-host checkpoint
  already anchored under a previous key becomes unverifiable if this one is regenerated (R13/D-8).

Both loaders used to catch ``OSError`` and continue with an ephemeral in-memory key behind a log
warning. Nothing failed: readiness probes PostgreSQL, Redis, MinIO, Keycloak and Alembic — not
these paths — so an upgrade reported green while both keys were silently worthless. That became
easy to hit once the containers stopped running as root (audit U33): a ``secrets`` volume created
by an earlier root-running build stays root-owned across an upgrade.

The fallback is now development-only and must be asked for explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

from ...config import Settings, get_settings


class SigningKeyUnavailable(RuntimeError):
    """A signing key cannot be persisted and the ephemeral fallback is not permitted."""


def describe_unpersistable(name: str, path: Path, problem: str) -> str:
    return (
        f"{name} cannot be persisted at {path}: {problem}. Refusing an ephemeral key — it would "
        f"leave every signature unverifiable while every health check stayed green. Fix the "
        f"volume's ownership (the container runs as uid 10001; see the volume table in "
        f"docs/runbooks/install-online.md), or set ALLOW_EPHEMERAL_SIGNING_KEYS=1 if this "
        f"is a development environment where losing signatures on restart is acceptable."
    )


def check_signing_key_path(name: str, path: Path) -> str | None:
    """Return a human-readable problem with ``path``, or ``None`` when the key is usable.

    Pure apart from creating the parent directory, which the loaders do anyway.
    """
    if path.exists():
        return None if os.access(path, os.R_OK) else "the existing key file is not readable"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"its directory cannot be created ({exc.strerror or exc})"
    if not os.access(path.parent, os.W_OK | os.X_OK):
        return "its directory is not writable"
    return None


def ensure_signing_keys_persistable(settings: Settings | None = None) -> None:
    """Raise :class:`SigningKeyUnavailable` unless both signing keys can be durably held.

    Called from the Celery ``worker_init``/``beat_init`` signals so a misconfigured deployment
    fails at start, loudly, instead of at the first Beat fire — or worse, silently succeeding with
    a key that dies with the process.
    """
    resolved = settings or get_settings()
    if resolved.allow_ephemeral_signing_keys:
        return
    for name, raw in (
        ("the verify-token signing key", resolved.verify_token_signing_key_path),
        ("the audit-checkpoint signing key", resolved.audit_checkpoint_signing_key_path),
    ):
        path = Path(raw)
        problem = check_signing_key_path(name, path)
        if problem is not None:
            raise SigningKeyUnavailable(describe_unpersistable(name, path, problem))
