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
import dataclasses
import datetime
import json
import logging
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models.audit_checkpoint import AuditCheckpoint
from ...db.models.audit_checkpoint_sink import AuditCheckpointSink
from ...db.models.audit_event import AuditEvent
from .sink import fetch_latest_offhost_checkpoint, push_checkpoint

logger = logging.getLogger("easysynq.audit.checkpoint")

# A sink whose last push is older than this is treated as stale (not attesting). ~3 anchoring
# cycles at the 15-minute Beat cadence.
_FRESHNESS_SECONDS = 2700


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _export_public_key(public_key: Ed25519PublicKey) -> None:
    """Best-effort export of the checkpoint PUBLIC key to its path, so api/CLI/off-host verifiers
    attest a signature without the beat-only private key. Idempotent write-once; a non-writable or
    unset path is non-fatal (the beat verify still derives the key from the private)."""
    raw = get_settings().audit_checkpoint_public_key_path
    if not raw:
        return
    path = Path(raw)
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    except OSError:
        logger.warning("audit checkpoint public key path not writable; verifiers must derive it")


def load_signing_key() -> Ed25519PrivateKey:
    """Load the Ed25519 checkpoint-signing key from its path; generate + persist a dev key if
    absent, or fall back to an ephemeral in-memory key if the path is not writable (dev-grade). Also
    exports the public half (best-effort) so lower-trust verifiers can attest without the secret."""
    path = Path(get_settings().audit_checkpoint_signing_key_path)
    if path.exists():
        loaded = load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):  # pragma: no cover - defensive
            raise TypeError("audit checkpoint signing key is not an Ed25519 private key")
        _export_public_key(loaded.public_key())
        return loaded
    key = Ed25519PrivateKey.generate()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    except OSError:
        logger.warning("audit signing key path not writable; using an ephemeral dev key")
    _export_public_key(key.public_key())
    return key


def load_verify_key() -> Ed25519PublicKey | None:
    """Load the checkpoint VERIFY (public) key for the detection control (doc 12 §4.4). Prefers
    DERIVING it from the private signing key when THIS process holds it (the beat — this ALWAYS
    matches the actual signer, even against a stale exported public key); else loads the exported
    PUBLIC key (the api/CLI/off-host verifier, which lacks the secret); else ``None`` — the caller
    then walks the chain only and cannot attest the checkpoint."""
    settings = get_settings()
    priv_path = Path(settings.audit_checkpoint_signing_key_path)
    if priv_path.exists():
        loaded_priv = load_pem_private_key(priv_path.read_bytes(), password=None)
        if isinstance(loaded_priv, Ed25519PrivateKey):
            return loaded_priv.public_key()
    raw_pub = settings.audit_checkpoint_public_key_path
    if raw_pub and Path(raw_pub).exists():
        loaded = load_pem_public_key(Path(raw_pub).read_bytes())
        if isinstance(loaded, Ed25519PublicKey):
            return loaded
        logger.warning("audit checkpoint public key is not Ed25519; ignoring")  # pragma: no cover
    return None


def verify_checkpoint_signature(
    public_key: Ed25519PublicKey,
    *,
    org_id: Any,
    latest_id: int,
    latest_row_hash: bytes,
    timestamp: datetime.datetime,
    signature: bytes | None,
) -> bool:
    """``True`` iff ``signature`` is a valid Ed25519 signature (from the trusted key) over the
    checkpoint's canonical payload. A forged/absent signature, or a rewritten latest_id /
    latest_row_hash / timestamp (changing the payload the attacker cannot re-sign) → ``False``.
    Fail-closed: any malformed input returns False rather than raising."""
    if signature is None:
        return False
    payload = _payload(org_id, latest_id, latest_row_hash, timestamp)
    try:
        public_key.verify(bytes(signature), payload)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001 - malformed key/sig → fail closed  # pragma: no cover
        return False


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


@dataclasses.dataclass(frozen=True, slots=True)
class OffHostCheckpointResult:
    """Outcome of the INDEPENDENT off-host read-back (doc 12 §4.4). FAIL-CLOSED: ``verified`` is
    True only when a genuinely OFF-HOST sink returned a fresh, signature-valid checkpoint matching
    the live chain. ``offhost_configured`` is True iff ≥1 enabled ``off_host`` sink exists — the
    beat alarms only when a CONFIGURED witness fails; a MISSING witness is the R13 soft-gate's
    persistent 'NOT tamper-evident' warning, not a nightly alarm. ``sinks_read`` counts sinks that
    returned an object (a wipe leaves a readable object → sinks_read>0 → the failure alarms);
    ``read_failed`` is True when a sink's read threw (unreachable witness) so it can fail closed
    even though nothing was read back."""

    offhost_configured: bool
    sinks_read: int
    verified: bool
    reasons: list[str]
    read_failed: bool = False


async def _attest_offhost_doc(
    session: AsyncSession,
    org_id: Any,
    verify_key: Ed25519PublicKey,
    doc: dict[str, Any],
    *,
    now: datetime.datetime,
) -> str | None:
    """Attest one off-host ``{checkpoint, signature}`` object: parse it, verify the sig, confirm it
    is FRESH (a witness that stopped advancing cannot attest rows anchored after it), then compare
    its signed ``latest_row_hash`` against the stored hash at ``latest_id``. ``None`` when it
    attests, else a human reason."""
    ckpt = doc.get("checkpoint")
    sig_b64 = doc.get("signature")
    if not isinstance(ckpt, dict) or not isinstance(sig_b64, str):
        return "malformed off-host checkpoint object"
    try:
        doc_org = str(ckpt["org_id"])
        latest_id = int(ckpt["latest_id"])
        row_hash = bytes.fromhex(str(ckpt["latest_row_hash"]))
        ts = datetime.datetime.fromisoformat(str(ckpt["timestamp"]))
        signature = base64.b64decode(sig_b64)
    except (KeyError, ValueError, TypeError):
        return "malformed off-host checkpoint payload"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    if doc_org != str(org_id):
        return "off-host checkpoint org mismatch"
    if not verify_checkpoint_signature(
        verify_key,
        org_id=doc_org,
        latest_id=latest_id,
        latest_row_hash=row_hash,
        timestamp=ts,
        signature=signature,
    ):
        return "off-host checkpoint signature invalid (forged/corrupt)"
    age = (now - ts).total_seconds()
    if age > _FRESHNESS_SECONDS:
        return f"off-host checkpoint is stale ({int(age)}s old) — pushes may have stopped"
    stored = (
        await session.execute(
            select(AuditEvent.row_hash).where(
                AuditEvent.org_id == org_id,
                AuditEvent.id == latest_id,
                AuditEvent.chained_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if stored is None:
        return "off-host checkpoint references a missing/unchained chain row (deletion)"
    if bytes(stored) != row_hash:
        return "off-host checkpoint latest_row_hash mismatch (chain rewritten)"
    return None


async def verify_offhost_checkpoint(
    session: AsyncSession,
    org_id: Any,
    *,
    verify_key: Ed25519PublicKey,
    now: datetime.datetime | None = None,
) -> OffHostCheckpointResult:
    """Read every enabled OFF-HOST sink's NEWEST signed checkpoint back with the SEPARATE read
    creds, verify the Ed25519 signature + freshness, and compare it against the live chain (doc 12
    §4.4). This is the independent witness the in-DB check cannot be: even a DB owner who rewrites
    BOTH the chain and the in-DB checkpoint — or deletes the rows — cannot reach the off-host copy.
    FAIL-CLOSED: only genuinely ``off_host`` sinks count (a same-host bucket is not an independent
    witness — R13), and 'no off-host sink' is UNAVAILABLE (verified=False), never a vacuous pass."""
    now = now or _now()
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
    offhost = [s for s in sinks if bool((s.connection or {}).get("off_host"))]
    if not offhost:
        return OffHostCheckpointResult(
            False, 0, False, ["no off-host sink configured — independent attestation unavailable"]
        )
    reasons: list[str] = []
    read = 0
    read_failed = False
    for sink in offhost:
        try:
            doc = await asyncio.to_thread(
                fetch_latest_offhost_checkpoint, sink.kind.value, sink.connection, org_id
            )
        except Exception as exc:  # noqa: BLE001 - ANY read failure must alarm, never attest
            reasons.append(f"sink {sink.id}: off-host read failed ({exc})")
            read_failed = True
            continue
        if doc is None:
            reasons.append(f"sink {sink.id}: no off-host checkpoint object found")
            continue
        read += 1
        reason = await _attest_offhost_doc(session, org_id, verify_key, doc, now=now)
        if reason is not None:
            reasons.append(f"sink {sink.id}: {reason}")
    return OffHostCheckpointResult(True, read, not reasons, reasons, read_failed=read_failed)
