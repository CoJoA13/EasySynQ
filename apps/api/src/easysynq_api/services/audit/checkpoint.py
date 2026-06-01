"""Signed audit-checkpoint anchoring + the tamper-evidence soft-gate (slice S6, R13, doc 12 §4.3).

``anchor_checkpoint`` writes a signed ``audit_checkpoint`` ``(latest_id, latest_row_hash,
timestamp)`` for an org and mirrors it to every enabled off-host ``audit_checkpoint_sink``. The
signature is Ed25519 over the RFC-8785 canonical payload, using a dev-grade key the ``beat``
container holds (the Part-11 crypto path stays reserved).

``tamper_evidence_attested`` is the honest soft-gate (R13): it returns True ONLY when an enabled
sink is **off-host** (the operator has asserted genuine host/credential separation) AND its last
push is fresh. A same-host dev bucket therefore reports **false** + the persistent "NOT
tamper-evident" UI warning — an install with no genuine off-host anchor must never claim
tamper-evidence.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models.audit_checkpoint import AuditCheckpoint
from ...db.models.audit_checkpoint_sink import AuditCheckpointSink
from ...db.models.audit_event import AuditEvent
from .sink import push_checkpoint

logger = logging.getLogger("easysynq.audit.checkpoint")

# A sink whose last push is older than this is treated as stale (not attesting). ~3 anchoring
# cycles at the 15-minute Beat cadence.
_FRESHNESS_SECONDS = 2700


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def load_signing_key() -> Ed25519PrivateKey:
    """Load the Ed25519 checkpoint-signing key from its path; generate + persist a dev key if
    absent, or fall back to an ephemeral in-memory key if the path is not writable (dev-grade)."""
    path = Path(get_settings().audit_checkpoint_signing_key_path)
    if path.exists():
        loaded = load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):  # pragma: no cover - defensive
            raise TypeError("audit checkpoint signing key is not an Ed25519 private key")
        return loaded
    key = Ed25519PrivateKey.generate()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    except OSError:
        logger.warning("audit signing key path not writable; using an ephemeral dev key")
    return key


def _payload(org_id: Any, latest_id: int, latest_row_hash: bytes, ts: datetime.datetime) -> bytes:
    return rfc8785.dumps(
        {
            "org_id": str(org_id),
            "latest_id": latest_id,
            "latest_row_hash": latest_row_hash.hex(),
            "timestamp": ts.astimezone(datetime.UTC).isoformat(),
        }
    )


async def _latest_chained(session: AsyncSession, org_id: Any) -> tuple[int, bytes] | None:
    row = (
        await session.execute(
            select(AuditEvent.id, AuditEvent.row_hash)
            .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_not(None))
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).first()
    if row is None or row[1] is None:
        return None
    return int(row[0]), bytes(row[1])


async def anchor_checkpoint(
    session: AsyncSession,
    org_id: Any,
    *,
    signing_key: Ed25519PrivateKey,
    push: bool = True,
) -> AuditCheckpoint | None:
    """Write + sign one checkpoint for ``org_id`` (None if nothing is chained yet) and mirror it to
    every enabled off-host sink (best-effort: a push failure is logged, not fatal)."""
    latest = await _latest_chained(session, org_id)
    if latest is None:
        return None
    latest_id, latest_row_hash = latest
    ts = _now()
    payload = _payload(org_id, latest_id, latest_row_hash, ts)
    signature = signing_key.sign(payload)

    checkpoint = AuditCheckpoint(
        org_id=org_id,
        latest_id=latest_id,
        latest_row_hash=latest_row_hash,
        timestamp=ts,
        app_signature=signature,
    )
    session.add(checkpoint)
    await session.commit()

    if push:
        await _mirror_to_sinks(session, org_id, latest_id, ts, payload, signature)
    return checkpoint


async def _mirror_to_sinks(
    session: AsyncSession,
    org_id: Any,
    latest_id: int,
    ts: datetime.datetime,
    payload: bytes,
    signature: bytes,
) -> None:
    sinks = (
        (
            await session.execute(
                select(AuditCheckpointSink).where(
                    AuditCheckpointSink.org_id == org_id,
                    AuditCheckpointSink.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not sinks:
        return
    body = json.dumps(
        {
            "checkpoint": json.loads(payload),
            "signature": base64.b64encode(signature).decode(),
        }
    ).encode()
    key = f"checkpoints/{org_id}/{latest_id}-{ts.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    for sink in sinks:
        try:
            await asyncio.to_thread(push_checkpoint, sink.kind.value, sink.connection, key, body)
            sink.last_anchored_at = _now()
        except Exception as exc:  # noqa: BLE001 - a sink outage must not crash the anchor run
            logger.error(
                "audit.checkpoint.sink_push_failed",
                extra={"extra_fields": {"sink_id": str(sink.id), "error": str(exc)}},
            )
    await session.commit()


async def tamper_evidence_attested(session: AsyncSession, org_id: Any) -> bool:
    """The honest soft-gate (R13): True only if an enabled, off-host sink anchored recently."""
    sinks = (
        (
            await session.execute(
                select(AuditCheckpointSink).where(
                    AuditCheckpointSink.org_id == org_id,
                    AuditCheckpointSink.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    now = _now()
    for sink in sinks:
        off_host = bool((sink.connection or {}).get("off_host"))
        last = sink.last_anchored_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=datetime.UTC)
        fresh = last is not None and (now - last).total_seconds() <= _FRESHNESS_SECONDS
        if off_host and fresh:
            return True
    return False
