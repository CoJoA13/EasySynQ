"""S6 integration proofs — the append-only, hash-chained, tamper-evident audit trail.

[AC#6a] every gated state-change writes exactly one ``audit_event`` row in the same transaction,
and the running app (the non-owner ``easysynq_app`` role) is **structurally** denied UPDATE/DELETE
on ``audit_event`` AND ``signature_event`` (SQLSTATE 42501 — the REVOKE actually bites).

[AC#6b] the chain-linker (the dedicated ``easysynq_linker`` role) stamps prev_hash/row_hash/
chained_at; ``verify-chain`` recomputes and matches; a row mutated out-of-band by a privileged
operator is detected as the **first broken link**; the linker is idempotent. Plus the off-host
checkpoint push lands a signed object, and the tamper-evidence soft-gate (R13) stays false on a
same-host sink.

The app runs as ``easysynq_app`` (see conftest); tests open dedicated engines as the ``app`` /
``linker`` / ``owner`` roles via the ``dsns`` fixture to exercise the real grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from easysynq_api.db.models._audit_enums import CheckpointSinkKind
from easysynq_api.db.models.audit_checkpoint import AuditCheckpoint
from easysynq_api.db.models.audit_checkpoint_sink import AuditCheckpointSink
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.audit.checkpoint import (
    anchor_checkpoint,
    load_signing_key,
    load_verify_key,
    tamper_evidence_attested,
    verify_offhost_checkpoint,
)
from easysynq_api.services.audit.linker import link_all

from . import s5_helpers as s5
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_EXPECTED_STEPS = {
    "DOCUMENT_CREATED",
    "CHECKOUT",
    "CHECKIN",
    "SUBMITTED_FOR_REVIEW",
    "APPROVED",
    "RELEASED",
}


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _grant_audit_read(subject: str) -> None:
    """Grant ``system.audit_log.read`` at SYSTEM scope so the actor can read the trail."""
    async with get_sessionmaker()() as s:
        from easysynq_api.db.models.authz_grant import PermissionOverride

        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == "system.audit_log.read"))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def _drive_to_effective(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> str:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    rel = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"audit-trail-content")
    return str(rel["id"]) if "id" in rel else ""


async def _link_as_linker(dsns: dict[str, str]) -> int:
    engine = create_async_engine(dsns["linker"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await link_all(session)
            return result.linked
    finally:
        await engine.dispose()


# --- AC#6a -------------------------------------------------------------------------------


async def test_ac6a_every_step_writes_a_row_and_trail_is_immutable(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
) -> None:
    """[AC#6a] Every gated lifecycle step produces a row (read via the API), and the running app
    role is denied UPDATE/DELETE on audit_event AND signature_event at the DB layer (42501)."""
    await _drive_to_effective(app_client, token_factory, subj)
    await _grant_audit_read(subj.a)

    listing = await app_client.get(
        "/api/v1/audit-events?limit=200", headers=_auth(token_factory, subj.a)
    )
    assert listing.status_code == 200, listing.text
    event_types = {e["event_type"] for e in listing.json()["events"]}
    assert _EXPECTED_STEPS <= event_types, f"missing audit rows: {_EXPECTED_STEPS - event_types}"

    # The running app connects as the NON-OWNER easysynq_app role → REVOKE actually bites.
    engine = create_async_engine(dsns["app"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for stmt in (
                "UPDATE audit_event SET reason = 'forged'",
                "DELETE FROM audit_event",
                "UPDATE signature_event SET intent = 'forged'",
                "DELETE FROM signature_event",
            ):
                with pytest.raises(DBAPIError) as exc:
                    await session.execute(text(stmt))
                    await session.commit()
                assert getattr(exc.value.orig, "sqlstate", None) == "42501", stmt
                await session.rollback()
    finally:
        await engine.dispose()


async def test_ac6a_no_write_verbs_on_the_api(app_client: AsyncClient) -> None:
    """[AC#6a] The audit API exposes no write verbs — append-only is a system invariant."""
    for method, path in (
        ("post", "/api/v1/audit-events"),
        ("patch", "/api/v1/audit-events/1"),
        ("delete", "/api/v1/audit-events/1"),
    ):
        resp = await app_client.request(method, path)
        assert resp.status_code in (404, 405), f"{method} {path} -> {resp.status_code}"


# --- AC#6b -------------------------------------------------------------------------------


async def test_ac6b_linker_chains_verify_matches_and_tamper_is_first_broken_link(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
) -> None:
    """[AC#6b] The linker stamps the hash columns; verify-chain matches; a mutated row is detected
    as the first broken link; the linker is idempotent."""
    await _drive_to_effective(app_client, token_factory, subj)
    await _grant_audit_read(subj.a)
    headers = _auth(token_factory, subj.a)

    linked = await _link_as_linker(dsns)
    assert linked >= len(_EXPECTED_STEPS)
    # CR-2 safe-prefix watermark: a rollback gap in the shared DB's id sequence (any earlier test's
    # rolled-back INSERT burns an IDENTITY value) is provably skipped only on the NEXT tick — the
    # two-snapshot rollback proof is fundamental — so a single link call need not chain the whole
    # backlog. Drain to the fixed point; a call returning 0 IS the idempotency proof (the linker
    # never re-links a chained row). A rollback-gap batch clears in <=2 ticks, so 10 is ample.
    drained = False
    for _ in range(10):
        if await _link_as_linker(dsns) == 0:
            drained = True
            break
    assert drained, "linker never reached a fixed point (idempotent no-op)"

    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    select(AuditEvent.id)
                    .where(AuditEvent.chained_at.is_not(None))
                    .order_by(AuditEvent.id)
                )
            )
            .scalars()
            .all()
        )
    assert rows, "no chained rows after linking"

    ok = await app_client.get("/api/v1/audit-events/verify-chain", headers=headers)
    assert ok.status_code == 200, ok.text
    # Field assertions (not an exact-dict match) — the response now also carries the Batch-7
    # ``checkpoint`` attestation (None here with no verify key configured, or a status if a prior
    # test persisted a signing key), orthogonal to this linker/walk proof.
    ok_body = ok.json()
    assert ok_body["verified"] is True
    assert ok_body["checked"] == len(rows)
    assert ok_body["pending"] == 0 and ok_body["breaks"] == []

    # A privileged operator (the OWNER role) mutates a row out-of-band — bypassing the app grant.
    victim = rows[len(rows) // 2]
    owner_engine = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            original_reason = (
                await session.execute(
                    text("SELECT reason FROM audit_event WHERE id = :id"), {"id": victim}
                )
            ).scalar_one()
            await session.execute(
                text("UPDATE audit_event SET reason = 'TAMPERED' WHERE id = :id"), {"id": victim}
            )
            await session.commit()

        broken = await app_client.get("/api/v1/audit-events/verify-chain", headers=headers)
        body = broken.json()
        assert body["verified"] is False
        assert body["breaks"], "tamper not detected"
        assert body["breaks"][0]["at_id"] == victim, "tamper not reported as the first broken link"

        # Restore the victim's original reason so the SHARED session DB chain is clean again for
        # later whole-chain consumers (the S11 restore re-verify re-walks the ENTIRE chain, not just
        # this org's verify-chain window — a committed-and-not-undone tamper would otherwise break
        # every subsequent full-chain re-verify). The stored row_hash was computed over the original
        # reason, so restoring it makes the recompute match the stored hash again (chain clean).
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            await session.execute(
                text("UPDATE audit_event SET reason = :reason WHERE id = :id"),
                {"reason": original_reason, "id": victim},
            )
            await session.commit()
    finally:
        await owner_engine.dispose()

    healed = await app_client.get("/api/v1/audit-events/verify-chain", headers=headers)
    assert healed.json()["verified"] is True, "chain not clean after restoring the tampered row"


async def test_ac6b_checkpoint_push_and_soft_gate(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
) -> None:
    """[AC#6b/R13] A worm_bucket checkpoint push lands a signed object; the tamper-evidence
    soft-gate is false for a same-host (off_host=false) sink and true once off-host is asserted."""
    import boto3

    from easysynq_api.config import get_settings

    await _drive_to_effective(app_client, token_factory, subj)
    await _grant_audit_read(subj.a)
    await _link_as_linker(dsns)
    org_id = await s5.default_org_id()

    async with get_sessionmaker()() as s:
        s.add(
            AuditCheckpointSink(
                org_id=org_id,
                kind=CheckpointSinkKind.worm_bucket,
                connection={"bucket": "audit-checkpoints", "off_host": False},
                enabled=True,
            )
        )
        await s.commit()

    async with get_sessionmaker()() as s:
        checkpoint = await anchor_checkpoint(s, org_id, signing_key=load_signing_key())
    assert checkpoint is not None

    async with get_sessionmaker()() as s:
        cp_count = (await s.execute(select(func.count()).select_from(AuditCheckpoint))).scalar_one()
        assert cp_count == 1
        attested_same_host = await tamper_evidence_attested(s, org_id)
    assert attested_same_host is False  # same-host bucket must NOT attest tamper-evidence (R13)

    # FAIL-CLOSED (doc 12 §4.4): with only a SAME-host sink the INDEPENDENT read-back is UNAVAILABLE
    # — it must report offhost_configured=False + verified=False (never a vacuous "verified" pass),
    # so the nightly beat defers to the R13 soft-gate rather than alarming on a missing witness.
    verify_key = load_verify_key()
    assert verify_key is not None
    async with get_sessionmaker()() as s:
        unavailable = await verify_offhost_checkpoint(s, org_id, verify_key=verify_key)
    assert unavailable.offhost_configured is False
    assert unavailable.verified is False
    assert unavailable.sinks_read == 0

    # The signed object actually landed in the off-host bucket.
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    listed = client.list_objects_v2(Bucket="audit-checkpoints", Prefix=f"checkpoints/{org_id}/")
    assert listed.get("KeyCount", 0) >= 1, "no checkpoint object pushed to the off-host bucket"

    # GET /audit/status reflects the soft-gate (false on a same-host sink).
    status = await app_client.get("/api/v1/audit/status", headers=_auth(token_factory, subj.a))
    assert status.status_code == 200, status.text
    sbody: dict[str, Any] = status.json()
    assert sbody["sink_enabled"] is True
    assert sbody["tamper_evidence_attested"] is False

    # Asserting a genuinely off-host endpoint flips the gate true (fresh last_anchored_at).
    async with get_sessionmaker()() as s:
        sink = (await s.execute(select(AuditCheckpointSink))).scalars().one()
        sink.connection = {"bucket": "audit-checkpoints", "off_host": True}
        await s.commit()
        assert await tamper_evidence_attested(s, org_id) is True

    # Now that a genuine off-host witness exists, the INDEPENDENT read-back reads the pushed object
    # back with the SEPARATE read creds, verifies its Ed25519 signature + freshness, and matches it
    # against the live chain — the witness the in-DB walk cannot be (a DB owner cannot reach it).
    async with get_sessionmaker()() as s:
        attested = await verify_offhost_checkpoint(s, org_id, verify_key=verify_key)
    assert attested.offhost_configured is True
    assert attested.sinks_read == 1
    assert attested.verified is True, attested.reasons

    # The freshness gate: a witness that stopped advancing cannot attest rows anchored after it.
    # Re-evaluating the SAME fresh object at a far-future `now` makes it stale → verified False,
    # so a stalled off-host push is caught rather than silently trusted.
    import datetime

    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=6000)
    async with get_sessionmaker()() as s:
        stale = await verify_offhost_checkpoint(s, org_id, verify_key=verify_key, now=future)
    assert stale.offhost_configured is True
    assert stale.verified is False
    assert any("stale" in r for r in stale.reasons), stale.reasons

    # THE DB-OWNER WIPE (doc 12 §4.4): a privileged owner deletes chain rows but cannot reach the
    # off-host copy, which still holds a signed checkpoint for a now-missing chain row. Simulate it
    # by pushing a validly-signed object at a latest_id ABOVE the live head: its signature+freshness
    # pass, but the referenced chain row does not exist → verified=False, sinks_read==1 (an ALARM
    # verdict) even though the local walk is self-consistent. This is what the nightly beat now
    # consults UNCONDITIONALLY — the fail-open was gating this read-back on the wiped chain itself.
    import base64
    import json

    from easysynq_api.services.audit import checkpoint as cp
    from easysynq_api.services.audit.sink import push_checkpoint

    ghost_id = 10_000_000
    ghost_ts = datetime.datetime.now(datetime.UTC)
    ghost_payload = cp._payload(org_id, ghost_id, b"\x11" * 32, ghost_ts)
    ghost_sig = load_signing_key().sign(ghost_payload)
    ghost_body = json.dumps(
        {"checkpoint": json.loads(ghost_payload), "signature": base64.b64encode(ghost_sig).decode()}
    ).encode()
    ghost_key = f"checkpoints/{org_id}/{ghost_id}-{ghost_ts.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    push_checkpoint(
        "worm_bucket", {"bucket": "audit-checkpoints", "off_host": True}, ghost_key, ghost_body
    )
    async with get_sessionmaker()() as s:
        wiped = await verify_offhost_checkpoint(s, org_id, verify_key=verify_key)
    assert wiped.offhost_configured is True
    assert wiped.sinks_read == 1
    assert wiped.verified is False
    assert any("missing" in r or "deletion" in r for r in wiped.reasons), wiped.reasons


async def test_ac6b_linker_safe_prefix_never_reorders_across_an_open_txn(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
) -> None:
    """[CR-2] A lower id sitting uncommitted in an open transaction must NOT let a higher, already-
    committed id be linked ahead of it — that reorder permanently breaks verify_chain's id-order
    walk. The linker holds the watermark below the uncommitted row until it commits, then links
    in id order. (The pure watermark algorithm is exhaustively unit-tested; this proves the wiring +
    the live race.)"""
    import datetime

    from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType

    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))
        ).scalar_one()

    def _row() -> AuditEvent:
        return AuditEvent(
            org_id=org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_type=ActorType.system,
            event_type=EventType.STAGE_ADVANCED,
            object_type=AuditObjectType.workflow_instance,
        )

    # Settle the watermark at the current frontier first: else a pre-existing FRESH rollback gap
    # below id_low would stall the linker below it, so `high_chained is None` (below) could pass
    # without the linker ever reaching id_low's gap — the CR-2 hold-back must be proven AT id_low.
    for _ in range(10):
        if await _link_as_linker(dsns) == 0:
            break

    engine = create_async_engine(dsns["owner"])
    low_session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        # A: insert the LOW row and hold the txn OPEN (uncommitted → invisible to the linker).
        low = _row()
        low_session.add(low)
        await low_session.flush()
        id_low = low.id

        # Session B: insert the HIGH row after it and COMMIT (id_high > id_low, and it is visible).
        async with async_sessionmaker(engine, expire_on_commit=False)() as high_session:
            high = _row()
            high_session.add(high)
            await high_session.commit()
            id_high = high.id
        assert id_high > id_low

        # Link while id_low is uncommitted: the linker must NOT chain id_high ahead of the gap.
        await _link_as_linker(dsns)
        async with get_sessionmaker()() as s:
            high_chained = (
                await s.execute(
                    text("SELECT chained_at FROM audit_event WHERE id = :id"), {"id": id_high}
                )
            ).scalar_one()
        assert high_chained is None, "linked a higher id ahead of a lower uncommitted one (CR-2)"

        # Commit id_low; now both are visible + contiguous → the linker links them in id order.
        await low_session.commit()
    finally:
        await low_session.close()
        await engine.dispose()

    for _ in range(3):
        await _link_as_linker(dsns)
    async with get_sessionmaker()() as s:
        chained = dict(
            (
                await s.execute(
                    text("SELECT id, chained_at FROM audit_event WHERE id IN (:a, :b)"),
                    {"a": id_low, "b": id_high},
                )
            ).all()
        )
    assert chained[id_low] is not None and chained[id_high] is not None, "not linked after commit"

    # The whole chain still verifies — no reorder break was introduced.
    await _grant_audit_read(subj.a)
    verify = await app_client.get(
        "/api/v1/audit-events/verify-chain", headers=_auth(token_factory, subj.a)
    )
    assert verify.json()["verified"] is True, verify.text


async def test_ac6b_linker_drains_multiple_windows_in_one_tick(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[CR-2 review, P2] A gap-free backlog larger than one _ID_WINDOW drains in a SINGLE linker
    tick, not one window per tick. Drain the shared backlog first, add a contiguous committed run,
    then force a tiny window so the run spans several windows; one link_all call must still take
    the whole run (proving the within-tick multi-window drain)."""
    import datetime

    from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
    from easysynq_api.services.audit import linker as linker_mod

    # Settle the watermark at the current frontier (a rollback gap needs a second tick, so loop).
    for _ in range(10):
        if await _link_as_linker(dsns) == 0:
            break

    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))
        ).scalar_one()

    n = 8
    async with get_sessionmaker()() as s:
        for _ in range(n):
            s.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=datetime.datetime.now(datetime.UTC),
                    actor_type=ActorType.system,
                    event_type=EventType.STAGE_ADVANCED,
                    object_type=AuditObjectType.workflow_instance,
                )
            )
        await s.commit()

    # Force several windows across the n-row run; one tick must still drain the whole gap-free run.
    monkeypatch.setattr(linker_mod, "_ID_WINDOW", 2)
    per_call = []
    for _ in range(50):
        c = await _link_as_linker(dsns)
        per_call.append(c)
        if c == 0:
            break
    assert max(per_call) >= n, f"no single tick drained the {n}-row gap-free run: {per_call}"

    async with get_sessionmaker()() as s:
        pending = (
            await s.execute(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.chained_at.is_(None))
            )
        ).scalar_one()
    # An absolute (not delta) assertion is legal ONLY because this test settles the whole table
    # itself — the leading drain loop + the drain-to-fixed-point above leave nothing pending. Keep
    # that leading settle if editing, else this turns flaky under the shared-DB backlog.
    assert pending == 0, "gap-free backlog not fully drained"


async def test_checkpoint_signature_catches_consistent_chain_rewrite(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    dsns: dict[str, str],
) -> None:
    """Batch 7 (doc 12 §4.4) — the headline detection control. A privileged DB OWNER who rewrites an
    audit row AND recomputes its row_hash yields a SELF-CONSISTENT chain the walk passes clean; only
    the Ed25519 signature on the checkpoint (which the owner cannot forge over the rewritten
    latest_row_hash) exposes it. Mutation-distinguishing: verify_chain WITHOUT the verify key passes
    the rewrite; WITH the key it breaks on the checkpoint↔chain hash mismatch."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from sqlalchemy import delete

    from easysynq_api.services.audit.canonical import (
        GENESIS_HASH,
        audit_row_from_orm,
        compute_row_hash,
    )
    from easysynq_api.services.audit.checkpoint import anchor_checkpoint
    from easysynq_api.services.audit.verify import verify_chain

    await _drive_to_effective(app_client, token_factory, subj)
    await _grant_audit_read(subj.a)
    await _link_as_linker(dsns)
    org_id = await s5.default_org_id()

    owner_engine = create_async_engine(dsns["owner"])
    orig_reason: str | None = None
    orig_hash = b""
    head_id = 0
    captured = False  # RELEASED events have reason=None, so track capture separately from the value
    try:
        # Clean slate: as OWNER, drop any checkpoint another test left (it would be signed by a
        # DIFFERENT key and fail against my verify key), so mine is the sole/newest one.
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
            await s.execute(delete(AuditCheckpoint).where(AuditCheckpoint.org_id == org_id))
            await s.commit()

        key = Ed25519PrivateKey.generate()
        async with get_sessionmaker()() as s:
            cp = await anchor_checkpoint(s, org_id, signing_key=key, push=False)
        assert cp is not None
        head_id = cp.latest_id

        # Baseline: the chain + its signed checkpoint attest clean.
        async with get_sessionmaker()() as s:
            good = await verify_chain(s, org_id, verify_key=key.public_key())
        assert good.verified is True
        assert good.checkpoint is not None
        assert good.checkpoint.signature_ok is True and good.checkpoint.hash_match is True

        # The OWNER rewrites the head row's payload AND recomputes its row_hash so the WALK stays
        # self-consistent (the exact finding threat) — capture the originals to restore the chain.
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
            row = (await s.execute(select(AuditEvent).where(AuditEvent.id == head_id))).scalar_one()
            orig_reason, orig_hash = row.reason, bytes(row.row_hash)
            captured = True
            row.reason = "OWNER-REWRITE"
            row.row_hash = compute_row_hash(
                audit_row_from_orm(row), row.prev_hash or GENESIS_HASH, version=1
            )
            await s.commit()

        # The self-consistent walk is fooled (mutation-distinguishing) ...
        async with get_sessionmaker()() as s:
            walk_only = await verify_chain(s, org_id)
        assert walk_only.verified is True, "the walk alone must NOT see a consistent rewrite"

        # ... but the signed checkpoint's latest_row_hash no longer matches → caught.
        async with get_sessionmaker()() as s:
            caught = await verify_chain(s, org_id, verify_key=key.public_key())
        assert caught.verified is False
        assert caught.checkpoint is not None and caught.checkpoint.hash_match is False
        assert any("latest_row_hash mismatch" in b.reason for b in caught.breaks)

        # And a FORGED checkpoint (owner rewrites the checkpoint's own latest_row_hash to match the
        # rewritten chain) fails the SIGNATURE — the owner cannot re-sign the new payload.
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
            await s.execute(
                text("UPDATE audit_checkpoint SET latest_row_hash = :h WHERE org_id = :o"),
                {"h": row.row_hash, "o": org_id},
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            forged = await verify_chain(s, org_id, verify_key=key.public_key())
        assert forged.verified is False
        assert forged.checkpoint is not None and forged.checkpoint.signature_ok is False
    finally:
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
            if captured:  # restore even when the original reason was legitimately None (RELEASED)
                restore = (
                    await s.execute(select(AuditEvent).where(AuditEvent.id == head_id))
                ).scalar_one_or_none()
                if restore is not None:
                    restore.reason = orig_reason
                    restore.row_hash = orig_hash
            await s.execute(delete(AuditCheckpoint).where(AuditCheckpoint.org_id == org_id))
            await s.commit()
        await owner_engine.dispose()
