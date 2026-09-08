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
import binascii
import dataclasses
import datetime
import json
import logging
from pathlib import Path
from time import monotonic
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
from ..common.signing import SigningKeyUnavailable, describe_unpersistable
from .sink import (
    SinkReadError,
    list_offhost_checkpoint_versions_page,
    push_checkpoint,
    read_offhost_checkpoint_version,
)

logger = logging.getLogger("easysynq.audit.checkpoint")

# A sink whose last push is older than this is treated as stale (not attesting). ~3 anchoring
# cycles at the 15-minute Beat cadence.
_FRESHNESS_SECONDS = 2700
_HISTORY_MAX_PAGES = 1024
_HISTORY_MAX_ENTRIES = 524_288
_HISTORY_REASON_LIMIT = 20
_HISTORY_SCAN_SECONDS = 300


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
        # Write atomically (temp + rename) so a concurrent verifier never reads a half-written PEM —
        # a truncated file would make load_verify_key raise instead of failing closed.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        tmp.replace(path)
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
    except OSError as exc:
        # Fail closed. Regenerating this key makes every off-host checkpoint already anchored under
        # the previous one unverifiable (R13/D-8) — the detection control the product claims.
        if not get_settings().allow_ephemeral_signing_keys:
            raise SigningKeyUnavailable(
                describe_unpersistable(
                    "the audit-checkpoint signing key", path, exc.strerror or str(exc)
                )
            ) from exc
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
    try:
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
            logger.warning("audit checkpoint public key is not Ed25519; ignoring")
    except Exception:  # noqa: BLE001 - a malformed/truncated/unreadable key FAILS CLOSED (→ None)
        # Returning None degrades the nightly verify to a walk-only pass (which then logs the
        # no-verify-key warning) instead of raising — a truncated exported PEM or an unreadable file
        # must not abort the detection control or 500 the API verify endpoint.
        logger.warning("audit checkpoint verify key unreadable/malformed; treating as unavailable")
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
    try:
        payload = _payload(org_id, latest_id, latest_row_hash, timestamp)
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


def unanchored_is_overdue(
    enabled_at: datetime.datetime, now: datetime.datetime, grace: datetime.timedelta
) -> bool:
    """Has a sink that has NEVER anchored been enabled long enough to count as a dead witness?

    Pure, so the boundary is unit-testable without a database or a live object store (the house
    rule for date/calendar logic). STRICTLY greater than the grace window: at exactly the boundary
    the sink is still given the benefit of the doubt, so a 24h grace means "alarm from 24h+1s", not
    "alarm at 24h". A naive ``enabled_at`` is read as UTC rather than raising — this decides whether
    to raise an alarm, so it must not itself become the reason the nightly verify crashes.
    """
    if enabled_at.tzinfo is None:
        enabled_at = enabled_at.replace(tzinfo=datetime.UTC)
    return now - enabled_at > grace


@dataclasses.dataclass(frozen=True, slots=True)
class OffHostCheckpointResult:
    """Outcome of the INDEPENDENT off-host read-back (doc 12 §4.4). FAIL-CLOSED: ``verified`` is
    True only when a genuinely OFF-HOST sink returned a fresh, signature-valid checkpoint matching
    the live chain. ``offhost_configured`` is True iff ≥1 enabled ``off_host`` sink exists — the
    beat alarms only when a CONFIGURED witness fails; a MISSING witness is the R13 soft-gate's
    persistent 'NOT tamper-evident' warning, not a nightly alarm. ``sinks_read`` counts sinks that
    returned an object; ``attest_failures`` counts those whose object FAILED attestation
    (tamper/stale/wipe); ``read_failed`` is True when a sink's read threw (unreachable witness). The
    beat alarms on ``attest_failures`` or ``read_failed`` — NOT on a mere not-yet-anchored empty
    sink — so a freshly added second witness alongside a healthy one never false-alarms.

    ``unanchored_overdue`` (Batch 11) counts sinks that are ENABLED, have NEVER anchored, and whose
    ``enabled_at`` is older than the configured grace window. A not-yet-anchored sink stays benign
    INSIDE the window (it may simply not have hit its first 15-minute anchor); past it, a witness
    that was configured and never once produced is an operator failure, not a fresh install — that
    is the case that used to be benign forever, so a dead witness could never be distinguished from
    a new one."""

    offhost_configured: bool
    sinks_read: int
    verified: bool
    reasons: list[str]
    read_failed: bool = False
    attest_failures: int = 0
    unanchored_overdue: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class AuthenticatedCheckpoint:
    latest_id: int
    row_hash: bytes
    timestamp: datetime.datetime


@dataclasses.dataclass(slots=True)
class _HistoryFailures:
    details: list[str] = dataclasses.field(default_factory=list)
    omitted: int = 0
    attestation: bool = False

    def record(self, reason: str, *, attestation: bool = False) -> None:
        self.attestation = self.attestation or attestation
        if len(self.details) < _HISTORY_REASON_LIMIT:
            self.details.append(reason)
        else:
            self.omitted += 1


def _authenticate_offhost_doc(
    org_id: Any,
    verify_key: Ed25519PublicKey,
    doc: dict[str, Any],
) -> tuple[AuthenticatedCheckpoint | None, str | None]:
    """Strictly parse and authenticate one legacy checkpoint without applying freshness."""
    if set(doc) != {"checkpoint", "signature"}:
        return None, "malformed off-host checkpoint object"
    ckpt = doc.get("checkpoint")
    sig_b64 = doc.get("signature")
    if not isinstance(ckpt, dict) or not isinstance(sig_b64, str):
        return None, "malformed off-host checkpoint object"
    if set(ckpt) != {"org_id", "latest_id", "latest_row_hash", "timestamp"}:
        return None, "malformed off-host checkpoint payload"
    doc_org = ckpt.get("org_id")
    latest_id = ckpt.get("latest_id")
    hash_hex = ckpt.get("latest_row_hash")
    timestamp_text = ckpt.get("timestamp")
    if not isinstance(doc_org, str) or not doc_org:
        return None, "malformed off-host checkpoint payload"
    if type(latest_id) is not int or latest_id < 1:
        return None, "malformed off-host checkpoint payload"
    if not isinstance(hash_hex, str) or len(hash_hex) != 64:
        return None, "malformed off-host checkpoint payload"
    if not isinstance(timestamp_text, str):
        return None, "malformed off-host checkpoint payload"
    try:
        row_hash = bytes.fromhex(hash_hex)
        timestamp = datetime.datetime.fromisoformat(timestamp_text)
        signature = base64.b64decode(sig_b64, validate=True)
    except (ValueError, TypeError, binascii.Error):
        return None, "malformed off-host checkpoint payload"
    if len(row_hash) != 32 or len(signature) != 64:
        return None, "malformed off-host checkpoint payload"
    try:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime.UTC)
        timestamp = timestamp.astimezone(datetime.UTC)
    except (OverflowError, ValueError):
        return None, "malformed off-host checkpoint payload"
    if doc_org != str(org_id):
        return None, "off-host checkpoint org mismatch"
    if not verify_checkpoint_signature(
        verify_key,
        org_id=doc_org,
        latest_id=latest_id,
        latest_row_hash=row_hash,
        timestamp=timestamp,
        signature=signature,
    ):
        return None, "off-host checkpoint signature invalid (forged/corrupt)"
    return AuthenticatedCheckpoint(latest_id, row_hash, timestamp), None


async def _compare_offhost_checkpoint(
    session: AsyncSession,
    org_id: Any,
    checkpoint: AuthenticatedCheckpoint,
) -> str | None:
    stored = (
        await session.execute(
            select(AuditEvent.row_hash).where(
                AuditEvent.org_id == org_id,
                AuditEvent.id == checkpoint.latest_id,
                AuditEvent.chained_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if stored is None:
        return "off-host checkpoint references a missing/unchained chain row (deletion)"
    if bytes(stored) != checkpoint.row_hash:
        return "off-host checkpoint latest_row_hash mismatch (chain rewritten)"
    return None


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
    checkpoint, reason = _authenticate_offhost_doc(org_id, verify_key, doc)
    if reason is not None:
        return reason
    if checkpoint is None:  # defensive: authentication never returns (None, None)
        return "malformed off-host checkpoint payload"
    age = (now - checkpoint.timestamp).total_seconds()
    if age > _FRESHNESS_SECONDS:
        return f"off-host checkpoint is stale ({int(age)}s old) — pushes may have stopped"
    return await _compare_offhost_checkpoint(session, org_id, checkpoint)


def _eligible_legacy_key(key: str) -> bool:
    name = key.rsplit("/", 1)[-1]
    head, separator, _suffix = name.partition("-")
    return bool(separator and head and all("0" <= char <= "9" for char in head))


def _check_history_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise TimeoutError("off-host checkpoint history scan deadline exceeded")


def _history_read_reason(exc: Exception) -> str:
    if isinstance(exc, SinkReadError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "off-host checkpoint history scan deadline exceeded"
    return "off-host checkpoint history read failed"


async def verify_offhost_checkpoint(
    session: AsyncSession,
    org_id: Any,
    *,
    verify_key: Ed25519PublicKey,
    now: datetime.datetime | None = None,
) -> OffHostCheckpointResult:
    """Verify every retained eligible legacy version at each enabled off-host witness."""
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
    attest_failures = 0
    unanchored_overdue = 0
    grace = datetime.timedelta(hours=max(0, get_settings().audit_witness_grace_hours))
    for sink in offhost:
        failures = _HistoryFailures()
        sink_read_failed = False
        parsed_any = False
        scan_complete = False
        latest: tuple[datetime.datetime, int] | None = None

        deadline = monotonic() + _HISTORY_SCAN_SECONDS
        page_count = 0
        entry_count = 0
        key_marker: str | None = None
        version_marker: str | None = None
        seen_cursors: set[tuple[str | None, str | None]] = set()
        try:
            async with asyncio.timeout(_HISTORY_SCAN_SECONDS):
                while True:
                    _check_history_deadline(deadline)
                    page = await asyncio.to_thread(
                        list_offhost_checkpoint_versions_page,
                        sink.kind.value,
                        sink.connection,
                        org_id,
                        key_marker=key_marker,
                        version_id_marker=version_marker,
                    )
                    _check_history_deadline(deadline)
                    page_count += 1
                    if page_count >= _HISTORY_MAX_PAGES:
                        raise SinkReadError("off-host checkpoint history page limit reached")
                    entry_count += len(page.versions) + len(page.delete_markers)
                    if entry_count >= _HISTORY_MAX_ENTRIES:
                        raise SinkReadError("off-host checkpoint history entry limit reached")
                    for _marker in page.delete_markers:
                        failures.record(
                            "retained delete marker found in checkpoint namespace",
                            attestation=True,
                        )
                    for ref in page.versions:
                        if not _eligible_legacy_key(ref.key):
                            continue
                        _check_history_deadline(deadline)
                        doc = await asyncio.to_thread(
                            read_offhost_checkpoint_version,
                            sink.kind.value,
                            sink.connection,
                            ref,
                        )
                        _check_history_deadline(deadline)
                        parsed_any = True
                        checkpoint, reason = _authenticate_offhost_doc(org_id, verify_key, doc)
                        if reason is not None:
                            failures.record(reason, attestation=True)
                            continue
                        # Defensive fail-closed guard: authentication never returns (None, None).
                        if checkpoint is None:
                            failures.record(
                                "malformed off-host checkpoint payload",
                                attestation=True,
                            )
                            continue
                        candidate = (checkpoint.timestamp, checkpoint.latest_id)
                        latest = candidate if latest is None else max(latest, candidate)
                        _check_history_deadline(deadline)
                        mismatch = await _compare_offhost_checkpoint(session, org_id, checkpoint)
                        _check_history_deadline(deadline)
                        if mismatch is not None:
                            failures.record(mismatch, attestation=True)
                    _check_history_deadline(deadline)
                    if not page.truncated:
                        scan_complete = True
                        break
                    cursor = (page.next_key_marker, page.next_version_id_marker)
                    if cursor in seen_cursors:
                        raise SinkReadError(
                            "off-host checkpoint history pagination cursor repeated"
                        )
                    seen_cursors.add(cursor)
                    key_marker, version_marker = cursor
        except Exception as exc:  # noqa: BLE001 - every incomplete scan fails closed
            sink_read_failed = True
            read_failed = True
            failures.record(_history_read_reason(exc))

        if scan_complete and latest is not None:
            age = (now - latest[0]).total_seconds()
            if age > _FRESHNESS_SECONDS:
                failures.record(
                    f"off-host checkpoint is stale ({int(age)}s old) — pushes may have stopped",
                    attestation=True,
                )

        if parsed_any:
            read += 1
        if scan_complete and not parsed_any and not failures.attestation and not sink_read_failed:
            if sink.last_anchored_at is not None:
                # A sink that HAS anchored before but now returns NO object — its WORM objects were
                # deleted, the bucket was replaced, or reads point at the wrong bucket. A witness
                # that was producing has gone dark: an attestation FAILURE, not the benign
                # not-yet-anchored case. (last_anchored_at is DB state a determined owner could
                # null; the trusted-lineage closure of that residual is the sink.py:106 thread.)
                failures.record(
                    "previously-anchored witness now returns no object "
                    "(WORM objects deleted / bucket replaced?)",
                    attestation=True,
                )
            elif unanchored_is_overdue(sink.enabled_at, now, grace):
                # Never anchored, and declared long enough ago that "it hasn't reached its first
                # 15-minute anchor yet" is no longer a credible explanation. A witness that was
                # configured and never once produced is an operator failure — it alarms (Batch 11).
                # Before enabled_at existed this case was benign FOREVER, so a permanently dead
                # witness was indistinguishable from a freshly added one.
                failures.record(
                    f"enabled at {sink.enabled_at.isoformat()} but has NEVER "
                    "anchored a checkpoint (past the configured grace window)"
                )
                unanchored_overdue += 1
            else:
                # Never anchored, still INSIDE the grace window (a genuinely fresh sink) — benign;
                # NOT an attestation failure, so it never alarms even read alongside a healthy
                # sibling. It still lands in ``reasons`` (verified=False) so the CLI reports it
                # isn't producing.
                failures.record("no off-host checkpoint object found")
        if failures.attestation:
            attest_failures += 1
        reasons.extend(f"sink {sink.id}: {reason}" for reason in failures.details)
        if failures.omitted:
            reasons.append(f"sink {sink.id}: {failures.omitted} additional failure reasons omitted")
    return OffHostCheckpointResult(
        True,
        read,
        not reasons,
        reasons,
        read_failed=read_failed,
        attest_failures=attest_failures,
        unanchored_overdue=unanchored_overdue,
    )
