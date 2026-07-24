"""The evidence-packs use-case layer (slice S-pack-1, doc 06 §7): preview + generate orchestration.

Two transaction owners run synchronously in the request:

* ``create_pack_with_preview`` — validate the scope, persist the pack header (DRAFT), resolve the
  candidate set, classify each candidate (R28: INCLUDED / EXCLUDED_PERMISSION / EXCLUDED_ABSENCE),
  persist the ``pack_item`` rows + the gap/exclusion summaries. The preview is **advisory** — the
  audited "generated" event fires only at seal (in the worker).
* ``generate_pack`` — flip DRAFT/FAILED → BUILDING (under ``FOR UPDATE``) and enqueue the worker
  build (strictly after commit so the worker never races an uncommitted row).

The R28 classification reuses the SAME deny-by-default engine the search/records row-filter uses
(``gather_grants`` + ``authorize``) — a pack can never contain an artifact the generator couldn't
read, and every dropped candidate is surfaced (a silent drop is a spec-defined defect).

Pack lifecycle audit events key on ``AuditObjectType.evidence_pack`` (the pack header id), NOT
``record`` — the pre-seal failed build has no record id yet, and the header is its own table.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import uuid
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._pack_enums import PackInclusionStatus, PackItemType, PackScopeKind, PackStatus
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_pack import EvidencePack
from ...db.models.pack_item import PackItem
from ...db.models.pack_share_link import PackShareLink
from ...db.models.record import Record
from ...domain.authz import RequestContext, ResourceContext, authorize
from ...logging import request_id_var
from ...problems import ProblemException
from ..authz import AuthzAuditSink, enforce, gather_grants
from ..records import repository as records_repo
from ..reports.checklist import compute_checklist
from ..vault import repository as vault_repo
from . import repository as repo
from . import share_token

_SCOPE_SELECTOR_KEY = {
    "CLAUSE": "clause_ids",
    "PROCESS": "process_ids",
    "FINDING": "finding_ids",  # S-aud-capa-pack
    "CAPA": "capa_ids",  # S-aud-capa-pack
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _validation_error(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


# --- audit emission ----------------------------------------------------------------------


def emit_pack_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    pack_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a pack ``audit_event`` (object_type=evidence_pack) BEFORE commit, so the mutation +
    its audit row commit atomically (hashes NULL for the S6 linker)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.evidence_pack,
            object_id=pack_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def emit_pack_event_system(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: EventType,
    pack_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """A *system*-actor pack event (actor_id NULL) — used by the stalled-build reaper (a time-driven
    Beat job with no initiating user). The human-initiated build attributes to its generator."""
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=_now(),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=event_type,
            object_type=AuditObjectType.evidence_pack,
            object_id=pack_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


# --- R28 classification (shared by preview + build) --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ClassifiedRecord:
    record: Record
    base: DocumentedInformation
    status: PackInclusionStatus
    reason: str | None


async def classify_candidates(
    session: AsyncSession,
    generator: AppUser,
    candidates: list[tuple[Record, DocumentedInformation]],
) -> list[ClassifiedRecord]:
    """Run each candidate through the generator's deny-by-default ``record.read`` (the
    search/records row-filter), then the genuine-absence (destroy-tombstone) check. The
    ``ResourceContext`` carries the record's process_ids + framework so a genuinely PROCESS-scoped
    grant is honored (not a blanket EXCLUDED_PERMISSION)."""
    grants = await gather_grants(session, generator.id, generator.org_id, "record.read")
    ctx = RequestContext(now=_now())
    out: list[ClassifiedRecord] = []
    for record, base in candidates:
        # The ONE source of truth (S-records-R): the same effective binding the records read gate
        # uses (leg A + leg B + the R3-1 correction fallback), so the pack classifier and
        # ``/records`` agree on what a PROCESS-scoped caller may read.
        process_ids = await records_repo.record_process_ids_effective(session, record)
        resource = ResourceContext(
            artifact_id=str(record.id),
            kind="RECORD",
            folder_path=base.folder_path,
            process_ids=frozenset(process_ids),
            framework_id=str(base.framework_id),
        )
        if not authorize(grants, "record.read", resource, ctx).allow:
            out.append(
                ClassifiedRecord(
                    record, base, PackInclusionStatus.EXCLUDED_PERMISSION, "not entitled to read"
                )
            )
        elif await repo.has_destroy_tombstone(session, record.id):
            out.append(
                ClassifiedRecord(
                    record,
                    base,
                    PackInclusionStatus.EXCLUDED_ABSENCE,
                    "evidence physically destroyed (disposition)",
                )
            )
        else:
            out.append(ClassifiedRecord(record, base, PackInclusionStatus.INCLUDED, None))
    return out


def exclusion_summary(classified: list[ClassifiedRecord]) -> dict[str, Any]:
    """The R28 exclusion report shape — permission-vs-absence, distinct from the gap report."""
    perm = [
        str(c.record.id) for c in classified if c.status is PackInclusionStatus.EXCLUDED_PERMISSION
    ]
    absent = [
        str(c.record.id) for c in classified if c.status is PackInclusionStatus.EXCLUDED_ABSENCE
    ]
    return {
        "permission": perm,
        "absence": absent,
        "permission_count": len(perm),
        "absence_count": len(absent),
    }


async def gap_summary(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    scope_kind: str,
    scope_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """The gap report (doc 06 §7.3 item 6): in-scope mandatory ★ clauses lacking current evidence.
    Reuses the org-wide ``compute_checklist`` (an objective rule, NOT permission-filtered — distinct
    from the exclusion report), intersected with the in-scope clause set. For PROCESS scope the
    clause set is derived transitively (process → linked docs → clause_mappings). FINDING/CAPA scope
    has no clause-coverage concept — it returns an explicit ``applicable: False`` marker (and skips
    the org-wide checklist entirely) so the cover/manifest read N/A, never a misleading 0-of-0."""
    if scope_kind in ("FINDING", "CAPA"):
        return {
            "in_scope_star_clauses": 0,
            "gap_count": 0,
            "clauses": [],
            "applicable": False,
            "note": "Gap analysis is not applicable for finding/CAPA scope.",
        }
    checklist = await compute_checklist(session, org_id)
    if scope_kind == "CLAUSE":
        in_scope = {str(c) for c in scope_ids}
    elif scope_kind == "PROCESS":
        in_scope = await repo.process_clause_ids(session, org_id, scope_ids)
    else:  # pragma: no cover - fail-closed; unknown kinds are rejected upstream
        raise ValueError(f"unknown pack scope_kind: {scope_kind}")
    rows = [r for r in checklist["rows"] if r["clause_id"] in in_scope]
    gaps = [r for r in rows if r["status"] in ("GAP", "PARTIAL")]
    return {
        "in_scope_star_clauses": len(rows),
        "gap_count": len(gaps),
        "clauses": [
            {"number": r["number"], "title": r["title"], "status": r["status"]} for r in gaps
        ],
    }


# --- scope validation --------------------------------------------------------------------


async def _validate_scope(
    session: AsyncSession, org_id: uuid.UUID, scope_kind: str, scope_selector: dict[str, Any]
) -> list[uuid.UUID]:
    key = _SCOPE_SELECTOR_KEY[scope_kind]
    raw = scope_selector.get(key)
    if not isinstance(raw, list) or not raw:
        raise _validation_error("scope_selector", "required", f"scope_selector.{key} is required")
    ids: list[uuid.UUID] = []
    for value in raw:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError as exc:
            raise _validation_error(key, "invalid", f"{key} must be UUIDs") from exc
    if scope_kind == "CLAUSE":
        for cid in ids:
            clause = await vault_repo.get_clause(session, cid)
            if clause is None:
                raise _validation_error(key, "not_found", f"Clause {cid} not found")
    elif scope_kind == "PROCESS":
        for pid in ids:
            process = await vault_repo.get_process(session, pid)
            if process is None or process.org_id != org_id:
                raise _validation_error(key, "not_found", f"Process {pid} not found")
    elif scope_kind == "FINDING":
        for fid in ids:
            finding = await repo.get_finding(session, fid)
            if finding is None or finding.org_id != org_id:
                raise _validation_error(key, "not_found", f"Finding {fid} not found")
    elif scope_kind == "CAPA":
        for cid in ids:
            capa = await repo.get_capa(session, cid)
            if capa is None or capa.org_id != org_id:
                raise _validation_error(key, "not_found", f"CAPA {cid} not found")
    else:  # pragma: no cover - fail-closed; unknown kinds are rejected upstream
        raise _validation_error("scope_kind", "invalid", f"unknown scope_kind {scope_kind}")
    return ids


async def _authorize_pack_subjects(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    scope_kind: str,
    scope_ids: list[uuid.UUID],
) -> None:
    """Refuse (403) creating a FINDING/CAPA pack over a subject the caller cannot READ at its own
    scope. The build serializes the finding/CAPA SUBJECT dossier (its narrative + the CAPA stage
    trail + e-signatures) — that IS the deliverable — but the build is worker-async with no request
    caller, so the read gate lives HERE, at create. ``classify_candidates`` R28-filters the evidence
    CANDIDATES, but the subject is excluded from that set, so without this gate a holder of
    ``report.evidence_pack.generate`` (independent of finding/capa read) could bundle a finding/CAPA
    they cannot read. Mirrors each subject's own single-read surface (no new authority):
    ``finding.read`` at SYSTEM (GET /findings/{id}), ``capa.read`` at the CAPA's PROCESS scope
    (GET /capas/{id}) — plus ``finding.read`` for a CAPA's ORIGIN finding, whose metadata the CAPA
    dossier embeds. Refuse-ANY — the subject IS the whole pack, so one unreadable subject fails
    (excluding it would leave an empty pack). CLAUSE/PROCESS packs carry no subject dossier.

    Routed through the ``enforce`` PEP (not a bare ``authorize``) so the subject-read decision —
    ALLOW **and** DENY — lands in the ``AuthzAuditSink`` durable authz trail (the PEP's audit
    invariant; a denied attempt must not be invisible), and the request source IP is threaded so an
    ``ip_allow`` read grant evaluates exactly as the subject's own GET does."""
    if scope_kind == "FINDING":
        # finding.read is SYSTEM-enforced (GET /findings/{id}) → one check gates every subject.
        if scope_ids:
            await enforce(
                session, authz_sink, request, caller, "finding.read", ResourceContext.system()
            )
    elif scope_kind == "CAPA":
        for cid in scope_ids:
            capa = await repo.get_capa(session, cid)
            if capa is None:  # pragma: no cover - _validate_scope already 404'd a missing subject
                continue
            resource = (
                ResourceContext(process_ids=frozenset({str(capa.process_id)}))
                if capa.process_id is not None
                else ResourceContext.system()
            )
            await enforce(session, authz_sink, request, caller, "capa.read", resource)
            if capa.origin_finding_id is not None:
                # The CAPA dossier embeds the origin finding's type/severity/summary/identifier
                # (dossier._capa_subject) — content GET /capas/{id} does NOT expose — so bundling
                # this CAPA also requires reading that finding (finding.read is SYSTEM-enforced,
                # GET /findings/{id}); else a capa.read-only holder harvests the finding data.
                await enforce(
                    session, authz_sink, request, caller, "finding.read", ResourceContext.system()
                )


def _build_items(
    org_id: uuid.UUID,
    pack_id: uuid.UUID,
    classified: list[ClassifiedRecord],
) -> tuple[list[PackItem], int]:
    """Materialise the pack_item rows from a classification: one RECORD row per candidate (carrying
    its inclusion status) + one DOCUMENT_VERSION row per distinct pinned governing version of an
    INCLUDED record. Returns (items, included_count)."""
    items: list[PackItem] = []
    seen_versions: set[uuid.UUID] = set()
    included = 0
    for c in classified:
        items.append(
            PackItem(
                org_id=org_id,
                pack_id=pack_id,
                item_type=PackItemType.RECORD,
                record_id=c.record.id,
                inclusion_status=c.status,
                exclusion_reason=c.reason,
                content_hash_at_seal=c.record.content_hash,
            )
        )
        if c.status is PackInclusionStatus.INCLUDED:
            included += 1
            vid = c.record.source_version_id
            if vid is not None and vid not in seen_versions:
                seen_versions.add(vid)
                items.append(
                    PackItem(
                        org_id=org_id,
                        pack_id=pack_id,
                        item_type=PackItemType.DOCUMENT_VERSION,
                        version_id=vid,
                        inclusion_status=PackInclusionStatus.INCLUDED,
                    )
                )
    return items, included + len(seen_versions)


# --- transaction owners ------------------------------------------------------------------


async def create_pack_with_preview(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    *,
    title: str,
    scope_kind: str,
    scope_selector: dict[str, Any],
    period_start: datetime.date | None = None,
    period_end: datetime.date | None = None,
) -> EvidencePack:
    """Create a pack (DRAFT) and compute its preview synchronously: resolve + classify candidates,
    persist the membership + gap/exclusion summaries. One commit."""
    try:
        kind = PackScopeKind(scope_kind)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in PackScopeKind)
        raise _validation_error(
            "scope_kind", "invalid", f"scope_kind must be one of: {allowed}"
        ) from exc
    if period_start is not None and period_end is not None and period_start > period_end:
        raise _validation_error("period_end", "invalid", "period_end precedes period_start")

    framework = await vault_repo.get_framework(session, caller.org_id)
    if framework is None:
        raise ProblemException(status=422, code="validation_error", title="No framework configured")
    scope_ids = await _validate_scope(session, caller.org_id, kind.value, scope_selector)
    # R28: a FINDING/CAPA pack must not bundle a subject the caller cannot read (the subject dossier
    # is built worker-side with no caller, so the read gate lives here at create).
    await _authorize_pack_subjects(session, authz_sink, request, caller, kind.value, scope_ids)

    pack = EvidencePack(
        org_id=caller.org_id,
        framework_id=framework.id,
        title=title,
        scope_kind=kind,
        scope_selector=scope_selector,
        period_start=period_start,
        period_end=period_end,
        status=PackStatus.DRAFT,
        created_by=caller.id,
    )
    session.add(pack)
    await session.flush()  # populate pack.id

    candidates = await repo.resolve_candidates(
        session,
        caller.org_id,
        scope_kind=kind.value,
        scope_ids=scope_ids,
        period_start=period_start,
        period_end=period_end,
    )
    classified = await classify_candidates(session, caller, candidates)
    items, included = _build_items(caller.org_id, pack.id, classified)
    session.add_all(items)
    pack.exclusion_summary = exclusion_summary(classified)
    pack.gap_summary = await gap_summary(
        session, caller.org_id, scope_kind=kind.value, scope_ids=scope_ids
    )
    pack.item_count = included
    await session.commit()
    await session.refresh(pack)
    return pack


async def generate_pack(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    pack_id: uuid.UUID,
) -> EvidencePack:
    """Flip a DRAFT/FAILED pack → BUILDING and enqueue the worker build (after commit). Idempotent
    re-trigger from FAILED; a SEALED pack is terminal (409); a BUILDING pack is already in flight
    (409) — the reaper recovers a stalled build."""
    pack = await repo.get_pack(session, pack_id, for_update=True)
    if pack is None or pack.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Evidence pack not found")
    if pack.status is PackStatus.SEALED:
        raise ProblemException(status=409, code="conflict", title="Pack is already sealed")
    if pack.status is PackStatus.BUILDING:
        raise ProblemException(status=409, code="conflict", title="Pack build already in progress")
    # Re-authorize the FINDING/CAPA subject at generate (request-aware): the create-time gate is
    # stale if the generator's finding.read/capa.read was revoked before this seal — including a
    # retry from FAILED long after create. Mirrors the evidence's seal-time re-check; closes the
    # create→seal read-authz TOCTOU (the worker build itself has no caller to re-check).
    scope_ids = await _validate_scope(
        session, pack.org_id, pack.scope_kind.value, pack.scope_selector
    )
    await _authorize_pack_subjects(
        session, authz_sink, request, caller, pack.scope_kind.value, scope_ids
    )
    pack.status = PackStatus.BUILDING
    pack.build_started_at = _now()
    pack.error = None
    await session.commit()
    await session.refresh(pack)

    # Enqueue AFTER the commit so the worker never reads an uncommitted BUILDING row.
    from ...tasks.packs import build_evidence_pack

    build_evidence_pack.delay(str(pack.id))
    return pack


# Default stall window before the reaper gives up on a BUILDING pack (a hard worker kill between the
# BUILDING commit and the build's own try/except strands it; acks_late re-delivery is best-effort).
STALL_TIMEOUT_SECONDS = 3600


async def reap_stalled_builds(
    session: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    max_age_seconds: int = STALL_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Flip packs stuck in BUILDING past the stall window → FAILED (a system-actor event), so the
    operator can re-generate. A Beat job (``easysynq.packs.reap_stalled_builds``) drives this; tests
    call it directly. ``FOR UPDATE SKIP LOCKED`` avoids racing an in-flight build."""
    now = now or _now()
    cutoff = now - datetime.timedelta(seconds=max_age_seconds)
    stalled = (
        (
            await session.execute(
                select(EvidencePack)
                .where(
                    EvidencePack.status == PackStatus.BUILDING,
                    EvidencePack.build_started_at.is_not(None),
                    EvidencePack.build_started_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for pack in stalled:
        pack.status = PackStatus.FAILED
        pack.error = "build_timeout"
        emit_pack_event_system(
            session,
            pack.org_id,
            EventType.PACK_BUILD_FAILED,
            pack.id,
            after={"error": "build_timeout"},
        )
    await session.commit()
    return {"reaped": len(stalled)}


# --- external delivery: time-boxed Ed25519 share links (S-pack-2, doc 06 §7.4, UJ-7) ------


def _token_digest(token: str) -> str:
    """The stored fingerprint of a share token (the raw token is never persisted)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _resolve_expiry(
    *,
    now: datetime.datetime,
    ttl_days: int | None,
    expires_at: datetime.datetime | None,
) -> datetime.datetime:
    """Compute a link's expiry, clamped to [now+1d, now+max]. An explicit ``expires_at`` wins; else
    ``ttl_days`` (default when unset). The time-box + revoke are the controls (it rides a URL)."""
    settings = get_settings()
    max_at = now + datetime.timedelta(days=settings.pack_share_max_ttl_days)
    min_at = now + datetime.timedelta(days=1)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.UTC)
        chosen = expires_at
    else:
        days = ttl_days if ttl_days is not None else settings.pack_share_default_ttl_days
        chosen = now + datetime.timedelta(days=max(1, days))
    return min(max(chosen, min_at), max_at)


async def create_share_link(
    session: AsyncSession,
    caller: AppUser,
    pack_id: uuid.UUID,
    *,
    ttl_days: int | None = None,
    expires_at: datetime.datetime | None = None,
    recipient: str | None = None,
) -> tuple[PackShareLink, str]:
    """Mint a time-boxed Ed25519 share link for a SEALED pack + persist the ``pack_share_link`` row.
    Returns ``(link, raw_token)`` — the raw token is shown to the caller **once** (only its digest
    is stored). 404 if the pack is missing/another org; 409 if not SEALED; 503 if the signing key is
    not provisioned (a share link must be verifiable). Audits PACK_SHARED."""
    pack = await repo.get_pack(session, pack_id)
    if pack is None or pack.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Evidence pack not found")
    if pack.status is not PackStatus.SEALED:
        raise ProblemException(
            status=409, code="conflict", title="Pack must be sealed before sharing"
        )
    if await repo.pack_has_destroyed_member(session, pack):
        # A record INCLUDED in this pack (or its FINDING/CAPA subject) was destroyed after sealing —
        # refuse BEFORE minting so we don't emit a misleading PACK_SHARED audit + a dead token the
        # serve gate rejects on its first use (the resolver's fail-closed UNAVAILABLE check).
        raise ProblemException(
            status=409,
            code="pack_evidence_destroyed",
            title="Pack contains evidence destroyed after sealing",
            detail="A record in this pack was destroyed (disposition); it can no longer be shared.",
        )

    now = _now()
    exp = _resolve_expiry(now=now, ttl_days=ttl_days, expires_at=expires_at)
    link = PackShareLink(
        org_id=caller.org_id,
        pack_id=pack.id,
        token_digest="",  # set after the id is assigned (the token binds the link id)
        recipient=recipient,
        expires_at=exp,
        created_by=caller.id,
    )
    session.add(link)
    await session.flush()  # populate link.id

    try:
        token = share_token.mint(pack.id, link.id, int(exp.timestamp()))
    except share_token.SigningKeyUnavailable as exc:
        raise ProblemException(
            status=503,
            code="signing_key_unavailable",
            title="Share-link signing key is not provisioned",
            detail=str(exc),
        ) from exc
    link.token_digest = _token_digest(token)

    emit_pack_event(
        session,
        caller,
        EventType.PACK_SHARED,
        pack.id,
        after={
            "share_link_id": str(link.id),
            "recipient": recipient,
            "expires_at": exp.isoformat(),
            "token_digest": link.token_digest,
        },
    )
    await session.commit()
    await session.refresh(link)
    return link, token


async def revoke_share_link(
    session: AsyncSession,
    caller: AppUser,
    pack_id: uuid.UUID,
    link_id: uuid.UUID,
    *,
    reason: str | None = None,
) -> PackShareLink:
    """Revoke a share link (immediate — the public endpoint re-checks the row on every access). 404
    if missing/other org/other pack; 409 if already revoked. Audits PACK_SHARE_REVOKED. The sealed
    pack is untouched (doc 06 §7.4 "frozen snapshot")."""
    link = await repo.get_share_link(session, link_id, for_update=True)
    if link is None or link.org_id != caller.org_id or link.pack_id != pack_id:
        raise ProblemException(status=404, code="not_found", title="Share link not found")
    if link.revoked_at is not None:
        raise ProblemException(status=409, code="conflict", title="Share link already revoked")
    link.revoked_at = _now()
    link.revoked_by = caller.id
    link.revoke_reason = reason
    emit_pack_event(
        session,
        caller,
        EventType.PACK_SHARE_REVOKED,
        pack_id,
        after={"share_link_id": str(link.id), "reason": reason},
    )
    await session.commit()
    await session.refresh(link)
    return link


@dataclasses.dataclass(frozen=True, slots=True)
class ShareResolution:
    """The outcome of verifying a public share token against its DB row. ``status`` is OK only when
    the signature is valid AND the link is live AND the pack is SEALED — the others map to 403 with
    an honest reason (a legitimate auditor sees "expired"/"revoked", not a vague error)."""

    status: Literal["OK", "INVALID", "REVOKED", "EXPIRED", "UNAVAILABLE"]
    link: PackShareLink | None = None
    pack: EvidencePack | None = None


async def resolve_share_token(
    session: AsyncSession, token: str, *, now: datetime.datetime | None = None
) -> ShareResolution:
    """Public-path resolver (no auth): Ed25519-verify the token, bind it to its ``pack_share_link``
    row (constant-time digest compare), then enforce the **authoritative, revocable** state —
    revoked, expired, or pack-not-SEALED. Never raises (the endpoint maps status to 403/HTML)."""
    now = now or _now()
    claims = share_token.verify(token)
    if claims is None:
        return ShareResolution("INVALID")
    link = await repo.get_share_link(session, claims.share_link_id)
    if (
        link is None
        or link.pack_id != claims.pack_id
        or not hmac.compare_digest(link.token_digest, _token_digest(token))
    ):
        return ShareResolution("INVALID")
    if link.revoked_at is not None:
        return ShareResolution("REVOKED", link=link)
    if now >= link.expires_at:
        return ShareResolution("EXPIRED", link=link)
    pack = await repo.get_pack(session, link.pack_id)
    if pack is None or pack.status is not PackStatus.SEALED or pack.zip_blob_sha256 is None:
        return ShareResolution("UNAVAILABLE", link=link)
    if await repo.pack_has_destroyed_member(session, pack):
        # Fail-closed AFTER the seal: a record INCLUDED in this pack (or its FINDING/CAPA subject)
        # was physically destroyed (DESTROY / R27 WORM-destroy), so the cached ZIP / portfolio must
        # not keep serving erased evidence / dossier narrative via this public link. Physically
        # purging the derived artifacts on disposition is a heavier R27 policy decision tracked as a
        # fast-follow — this closes the reachability now.
        return ShareResolution("UNAVAILABLE", link=link)
    return ShareResolution("OK", link=link, pack=pack)


async def record_share_download(
    session: AsyncSession,
    link: PackShareLink,
    pack: EvidencePack,
    *,
    fmt: str,
    client_ip: str | None,
) -> None:
    """Account + audit a successful guest download (PACK_DOWNLOADED, system-actor — a bearer-token
    guest has no app_user). Commits its own short transaction before the bytes stream."""
    link.download_count += 1
    link.last_downloaded_at = _now()
    emit_pack_event_system(
        session,
        pack.org_id,
        EventType.PACK_DOWNLOADED,
        pack.id,
        after={
            "share_link_id": str(link.id),
            "format": fmt,
            "recipient": link.recipient,
            "client_ip": client_ip,
            "token_digest": link.token_digest,
        },
    )
    await session.commit()
