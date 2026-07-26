"""Celery/Beat tasks for the tamper-evident audit trail (slice S6, R12/R13, doc 12 §4).

* ``chain_link`` — the decoupled chain-linker; runs continuously (~30 s) under a PG advisory lock,
  as the dedicated ``easysynq_linker`` role (the only role that may stamp the hash columns).
* ``verify_chain`` — nightly re-walk; alarms on the first broken link.
* ``roll_partitions`` — daily; keeps the rolling monthly-partition runway ≥2 months ahead.

Each task uses its own disposed async engine so a fresh event loop per ``asyncio.run`` is safe
(reusing the S4 ``release_due`` idiom). The linker connects with the LINKER DSN; verify/rotation use
the app DSN.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.audit_event import AuditEvent
from ..db.models.organization import Organization
from ..services.audit.checkpoint import (
    OffHostCheckpointResult,
    anchor_checkpoint,
    load_signing_key,
    load_verify_key,
    verify_offhost_checkpoint,
)
from ..services.audit.linker import link_all
from ..services.audit.partitions import ensure_partitions
from ..services.audit.verify import VerifyResult, verify_chain
from ..services.common.pg_locks import LOCK_CHAIN_LINK, pg_advisory_lock
from ..services.notifications.constants import EVENT_INTEGRITY_ALARM
from ..services.notifications.ops_channel import OperatorAlert, send_operator_alert
from ..services.notifications.ops_events import (
    CHECK_CHAIN_VERIFY,
    CHECK_OFFHOST_WITNESS,
    CHECK_VERIFY_KEY,
    CHECK_WITNESS_REQUIRED,
    emit_integrity_alarm,
)
from .app import task

logger = logging.getLogger("easysynq.audit.tasks")


def _should_alarm_offhost(
    offhost: OffHostCheckpointResult, *, witness_required: bool = False
) -> bool:
    """Decide whether the INDEPENDENT off-host witness should raise CHAIN_VERIFY_FAIL. Alarm only
    when a CONFIGURED witness produced a genuine failure — an object was read back and REJECTED
    (tamper / stale / a wipe leaving a chain-less object → attest_failures>0), a read itself
    failed (an unreachable witness → read_failed), or a sink has been enabled past the grace window
    without EVER anchoring (unanchored_overdue>0 — a witness configured and silently dead). A
    not-yet-anchored empty sink inside the grace window is NOT a failure (keyed on attest_failures,
    not the global sinks_read, so a fresh second witness alongside a healthy one never
    false-alarms).

    ``witness_required`` is the Batch 11 OUT-OF-DB declaration (``AUDIT_WITNESS_REQUIRED``). Without
    it, an org with no off-host sink stays quiet and defers to the R13 soft-gate's persistent 'NOT
    tamper-evident' warning — correct for a fresh install that never configured one. With it, the
    absence of a witness is itself the alarm: the row a privileged DB owner would DELETE to go dark
    is exactly ``audit_checkpoint_sink``, so the requirement has to be asserted from outside the
    database or deleting the witness also deletes the evidence that one was expected."""
    if witness_required and not offhost.offhost_configured:
        return True
    return offhost.offhost_configured and (
        offhost.attest_failures > 0 or offhost.read_failed or offhost.unanchored_overdue > 0
    )


def _emit_chain_verify_fail(
    session: AsyncSession, org_id: object, result: VerifyResult, offhost_reasons: list[str]
) -> None:
    """Append a CHAIN_VERIFY_FAIL audit row (system actor, object_type ``audit``) so a detected
    tamper — whether from the in-DB walk/checkpoint or the INDEPENDENT off-host read — leaves a
    durable in-DB alarm alongside the structured log. Batch 11 hangs the high-severity operator
    NOTIFICATION (``integrity.alarm``) + the out-of-band channel off this same detection point, so
    the audit row is the record and the alarm is the reach. Hashes stay NULL until the chain-linker
    fills them (R12)."""
    reasons = sorted({b.reason for b in result.breaks} | set(offhost_reasons))
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=EventType.CHAIN_VERIFY_FAIL,
            object_type=AuditObjectType.audit,
            object_id=org_id,
            after={
                "first_break_at_id": result.breaks[0].at_id if result.breaks else None,
                "break_count": len(result.breaks),
                "reasons": reasons,
                "checkpoint_reason": (
                    result.checkpoint.reason if result.checkpoint is not None else None
                ),
                "offhost_reasons": offhost_reasons,
            },
        )
    )


async def _run_chain_link() -> int:
    engine = create_async_engine(get_settings().audit_linker_database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_CHAIN_LINK) as held:
            if not held:
                logger.debug("audit.chain_link: another linker holds the lock; skipping")
                return 0
            result = await link_all(session)
            return result.linked
    finally:
        await engine.dispose()


async def _run_verify_chain() -> int:
    settings = get_settings()
    # Engine construction is INSIDE the guarded region (Codex P2): create_async_engine() raises on a
    # malformed DSN or an unavailable dialect/driver, and Settings.database_url is only typed as a
    # string so nothing rejects that earlier. Built above the try, such a failure would exit the
    # nightly detection control without the "could not run" alarm it promises.
    engine: AsyncEngine | None = None
    # The nightly verify is the AUTHORITATIVE detection control (doc 12 §4.4): it holds the
    # beat-only signing key, so load_verify_key() always resolves (derives the public key) here and
    # the signed-checkpoint attestation runs — unlike the api/CLI, which may only walk.
    verify_key = load_verify_key()
    no_key_detail = (
        "no checkpoint verify key — signed-checkpoint + off-host attestation DISABLED; "
        "nightly verify degraded to a chain walk only"
    )
    if verify_key is None:
        # A None key here means the beat holds no persistent signing key (a non-writable path fell
        # back to an ephemeral key, or the secret is unmounted) — the authoritative detector then
        # only walks the self-consistent chain, with BOTH the signed-checkpoint and off-host
        # attestations DISABLED. Batch 11 turns this from a log line into an active alarm: an
        # integrity.alarm per org (below) + the out-of-band operator channel. It writes NO
        # CHAIN_VERIFY_FAIL audit row — that event means "a tamper was DETECTED", and a missing key
        # is a misconfiguration; recording it as a detection would poison the tamper signal.
        logger.error(
            "audit.verify_chain.no_verify_key", extra={"extra_fields": {"detail": no_key_detail}}
        )
        await send_operator_alert(
            settings,
            OperatorAlert(
                event=EVENT_INTEGRITY_ALARM,
                severity="critical",
                summary="audit verify key missing — checkpoint attestation is DISABLED",
                detail={"check": CHECK_VERIFY_KEY, "reason": no_key_detail},
            ),
        )
    total_breaks = 0
    offhost_alarms = 0
    # ``dirty`` covers BOTH the CHAIN_VERIFY_FAIL audit rows and the integrity.alarm notification
    # rows. Keying the commit on the audit rows alone (the pre-Batch-11 ``emitted``) would silently
    # discard the missing-verify-key notifications, which write no audit row by design.
    dirty = False
    try:
        engine = create_async_engine(settings.database_url)
        sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                if verify_key is None:
                    created = await emit_integrity_alarm(
                        session,
                        org_id=org_id,
                        check=CHECK_VERIFY_KEY,
                        reasons=[no_key_detail],
                        break_count=0,
                        now=datetime.datetime.now(datetime.UTC),
                    )
                    dirty = dirty or created > 0
                result = await verify_chain(session, org_id, verify_key=verify_key)
                total_breaks += len(result.breaks)
                # The INDEPENDENT off-host read-back runs whenever a verify key is held — NOT gated
                # on local chain/checkpoint state, since a privileged DB owner can DELETE every
                # audit_event AND audit_checkpoint row (a full wipe → checked==0, verified==True)
                # while the unreachable off-host copy still holds the signed checkpoint that exposes
                # the deletion. Gating on the wiped chain would suppress the only surviving witness.
                offhost_reasons: list[str] = []
                alarm_offhost = False
                witness_absent = False  # a REQUIRED witness that isn't configured at all
                if verify_key is not None:
                    offhost = await verify_offhost_checkpoint(
                        session, org_id, verify_key=verify_key
                    )
                    alarm_offhost = _should_alarm_offhost(
                        offhost, witness_required=settings.audit_witness_required
                    )
                    if alarm_offhost:
                        # A witness-required alarm on a MISSING sink has no per-sink reasons to
                        # report beyond the "unavailable" note, so name the declaration explicitly —
                        # otherwise the audit row and the notification both read as empty.
                        offhost_reasons = list(offhost.reasons)
                        witness_absent = not offhost.offhost_configured
                        if witness_absent:
                            offhost_reasons.append(
                                "AUDIT_WITNESS_REQUIRED is set but this org has NO enabled "
                                "off-host checkpoint sink — the declared witness is absent"
                            )
                        offhost_alarms += 1
                if not result.verified or alarm_offhost:
                    logger.error(
                        "audit.verify_chain.broken",
                        extra={
                            "extra_fields": {
                                "org_id": str(org_id),
                                "first_break_at_id": (
                                    result.breaks[0].at_id if result.breaks else None
                                ),
                                "break_count": len(result.breaks),
                                "checkpoint_reason": (
                                    result.checkpoint.reason
                                    if result.checkpoint is not None
                                    else None
                                ),
                                "offhost_reasons": offhost_reasons,
                            }
                        },
                    )
                    _emit_chain_verify_fail(session, org_id, result, offhost_reasons)
                    dirty = True
                    # Batch 11: the durable audit row is no longer the end of the line. Notify the
                    # org's System Administrators in-app/by email (integrity.alarm is CRITICAL, so
                    # immediate + quiet-hours-piercing), and fire the out-of-band operator channel
                    # so the alarm survives a later DB outage or an admin nobody is watching.
                    reasons = sorted({b.reason for b in result.breaks} | set(offhost_reasons))
                    check = CHECK_CHAIN_VERIFY if not result.verified else CHECK_OFFHOST_WITNESS
                    if witness_absent:
                        check = CHECK_WITNESS_REQUIRED
                    await emit_integrity_alarm(
                        session,
                        org_id=org_id,
                        check=check,
                        reasons=reasons,
                        break_count=len(result.breaks),
                        now=datetime.datetime.now(datetime.UTC),
                    )
                    await send_operator_alert(
                        settings,
                        OperatorAlert(
                            event=EVENT_INTEGRITY_ALARM,
                            severity="critical",
                            summary="audit-trail integrity alarm — the chain or its independent "
                            "witness failed verification",
                            detail={
                                "check": check,
                                "break_count": len(result.breaks),
                                "reasons": reasons,
                            },
                            org_id=str(org_id),
                        ),
                    )
            if dirty:
                await session.commit()
        # Return chain breaks PLUS off-host alarms so an off-host-only failure (walk clean but the
        # independent witness is stale/corrupt/unreachable/rewritten) is never reported as 0=intact
        # to the Redis result backend — any detected integrity failure yields a non-zero result.
        return total_breaks + offhost_alarms
    except Exception as exc:
        # The nightly detection control could not RUN — PostgreSQL unreachable, the app role
        # revoked, a statement timeout. Silence here is indistinguishable from "chain intact", which
        # is precisely the mode the in-DB alarm path cannot report on. Fire the out-of-band channel,
        # then RE-RAISE so Celery still records a failed task (no observability is traded away).
        await send_operator_alert(
            settings,
            OperatorAlert(
                event=EVENT_INTEGRITY_ALARM,
                severity="critical",
                summary="nightly audit chain verification could not run",
                detail={"check": CHECK_CHAIN_VERIFY, "error": str(exc)[:500]},
            ),
        )
        raise
    finally:
        if engine is not None:  # unbound when create_async_engine itself failed
            await engine.dispose()


async def _run_roll_partitions() -> list[str]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            return await ensure_partitions(session)
    finally:
        await engine.dispose()


async def _run_checkpoint_anchor() -> int:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    signing_key = load_signing_key()
    anchored = 0
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                if await anchor_checkpoint(session, org_id, signing_key=signing_key) is not None:
                    anchored += 1
    finally:
        await engine.dispose()
    return anchored


@task(name="easysynq.audit.chain_link")
def chain_link() -> int:
    """Link unchained audit rows; returns the count linked this run."""
    return asyncio.run(_run_chain_link())


@task(name="easysynq.audit.verify_chain")
def verify_chain_task() -> int:
    """Re-walk + verify the chain; returns the count of detected integrity findings — chain breaks
    PLUS independent off-host witness alarms (0 = intact)."""
    return asyncio.run(_run_verify_chain())


@task(name="easysynq.audit.roll_partitions")
def roll_partitions() -> list[str]:
    """Ensure the rolling monthly-partition runway; returns the month labels ensured."""
    return asyncio.run(_run_roll_partitions())


@task(name="easysynq.audit.checkpoint_anchor")
def checkpoint_anchor() -> int:
    """Write + mirror a signed checkpoint per org; returns the count anchored."""
    return asyncio.run(_run_checkpoint_anchor())
