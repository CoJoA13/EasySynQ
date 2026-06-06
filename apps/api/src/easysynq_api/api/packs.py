"""The evidence-packs surface (slice S-pack-1, doc 06 §7, doc 13 §7.3): preview + generate + get.

An Evidence Pack (UJ-7) is an on-demand, scope-limited, **immutable, self-verifying** bundle of
records + their evidence + a traceability manifest, sealed and registered as a RETAIN_PERMANENT
EVIDENCE Record. A pack is immutable once sealed — deliberately **no PUT/PATCH/DELETE** on this
router (``POST …/generate`` is a sanctioned build trigger, not a content edit; the route-inventory
proof enforces it). Generate/read gate on ``report.evidence_pack.generate`` (seeded but reaching no
concrete process at its seeded scope → grant via a SYSTEM override until the role UI, the
``record.*`` precedent); download gates on ``report.export``. External delivery is S-pack-2.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models._pack_enums import PackStatus
from ..db.models.app_user import AppUser
from ..db.models.evidence_pack import EvidencePack
from ..db.models.pack_item import PackItem
from ..db.models.pack_share_link import PackShareLink
from ..db.session import get_session
from ..problems import ProblemException
from ..services.authz import require
from ..services.packs import (
    create_pack_with_preview,
    create_share_link,
    generate_pack,
    revoke_share_link,
)
from ..services.packs import repository as packs_repo
from ..services.vault import repository as vault_repo
from ..services.vault import storage

router = APIRouter(prefix="/api/v1", tags=["evidence-packs"])


# --- request bodies ---------------------------------------------------------------------


class PackCreate(BaseModel):
    title: str
    scope_kind: Literal["CLAUSE", "PROCESS", "FINDING", "CAPA"]
    clause_ids: list[uuid.UUID] = []
    process_ids: list[uuid.UUID] = []
    finding_ids: list[uuid.UUID] = []  # S-aud-capa-pack (scope_kind=FINDING)
    capa_ids: list[uuid.UUID] = []  # S-aud-capa-pack (scope_kind=CAPA)
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None

    def scope_selector(self) -> dict[str, Any]:
        if self.scope_kind == "CLAUSE":
            return {"clause_ids": [str(c) for c in self.clause_ids]}
        if self.scope_kind == "PROCESS":
            return {"process_ids": [str(p) for p in self.process_ids]}
        if self.scope_kind == "FINDING":
            return {"finding_ids": [str(f) for f in self.finding_ids]}
        return {"capa_ids": [str(c) for c in self.capa_ids]}


class ShareCreate(BaseModel):
    ttl_days: int | None = None
    expires_at: datetime.datetime | None = None
    recipient: str | None = None


class ShareRevoke(BaseModel):
    reason: str | None = None


# --- serializers ------------------------------------------------------------------------


def _pack(pack: EvidencePack) -> dict[str, Any]:
    return {
        "id": str(pack.id),
        "title": pack.title,
        "scope_kind": pack.scope_kind.value,
        "scope_selector": pack.scope_selector,
        "period_start": pack.period_start.isoformat() if pack.period_start else None,
        "period_end": pack.period_end.isoformat() if pack.period_end else None,
        "status": pack.status.value,
        "item_count": pack.item_count,
        "gap_summary": pack.gap_summary,
        "exclusion_summary": pack.exclusion_summary,
        "content_hash": pack.content_hash,
        "zip_blob_sha256": pack.zip_blob_sha256,
        "pack_record_id": str(pack.pack_record_id) if pack.pack_record_id else None,
        "error": pack.error,
        "created_at": pack.created_at.isoformat() if pack.created_at else None,
        "generated_at": pack.generated_at.isoformat() if pack.generated_at else None,
    }


def _pack_item(item: PackItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "item_type": item.item_type.value,
        "record_id": str(item.record_id) if item.record_id else None,
        "version_id": str(item.version_id) if item.version_id else None,
        "inclusion_status": item.inclusion_status.value,
        "exclusion_reason": item.exclusion_reason,
        "content_hash_at_seal": item.content_hash_at_seal,
    }


def _share_link(link: PackShareLink) -> dict[str, Any]:
    """A management view of a share link — never the raw token (only its digest prefix)."""
    now = datetime.datetime.now(datetime.UTC)
    return {
        "id": str(link.id),
        "pack_id": str(link.pack_id),
        "recipient": link.recipient,
        "state": link.state(now=now),
        "token_digest": link.token_digest[:16],
        "expires_at": link.expires_at.isoformat(),
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "revoke_reason": link.revoke_reason,
        "download_count": link.download_count,
        "last_downloaded_at": (
            link.last_downloaded_at.isoformat() if link.last_downloaded_at else None
        ),
    }


# --- helpers + gates --------------------------------------------------------------------


_generate = require("report.evidence_pack.generate")  # SYSTEM scope (no path id → SYSTEM override)
_export = require("report.export")


async def _load(session: AsyncSession, caller: AppUser, pack_id: uuid.UUID) -> EvidencePack:
    pack = await packs_repo.get_pack(session, pack_id)
    if pack is None or pack.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Evidence pack not found")
    return pack


# --- endpoints --------------------------------------------------------------------------


@router.post("/evidence-packs", status_code=status.HTTP_201_CREATED)
async def create_pack_endpoint(
    body: PackCreate,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a pack (DRAFT) + compute its preview synchronously (resolve + R28-classify candidates,
    gap/exclusion summaries). The preview is advisory — generate seals it."""
    pack = await create_pack_with_preview(
        session,
        caller,
        title=body.title,
        scope_kind=body.scope_kind,
        scope_selector=body.scope_selector(),
        period_start=body.period_start,
        period_end=body.period_end,
    )
    items = await packs_repo.list_pack_items(session, pack.id)
    return {**_pack(pack), "items": [_pack_item(i) for i in items]}


@router.get("/evidence-packs")
async def list_packs_endpoint(
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
) -> list[dict[str, Any]]:
    packs = await packs_repo.list_packs(session, caller.org_id, limit=min(limit, 100))
    return [_pack(p) for p in packs]


@router.get("/evidence-packs/{pack_id}")
async def get_pack_endpoint(
    pack_id: uuid.UUID,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The pack header + membership + gap/exclusion summaries + **status** (the build poll endpoint:
    DRAFT/BUILDING/SEALED/FAILED). Membership reflects seal-time classification once SEALED."""
    pack = await _load(session, caller, pack_id)
    items = await packs_repo.list_pack_items(session, pack.id)
    return {**_pack(pack), "items": [_pack_item(i) for i in items]}


@router.post("/evidence-packs/{pack_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_pack_endpoint(
    pack_id: uuid.UUID,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue the immutable build/seal (DRAFT/FAILED → BUILDING). Poll ``GET /evidence-packs/{id}``
    for SEALED. 409 if already sealed or a build is in progress."""
    pack = await generate_pack(session, caller, pack_id)
    return _pack(pack)


@router.get("/evidence-packs/{pack_id}/download")
async def download_pack_endpoint(
    pack_id: uuid.UUID,
    caller: AppUser = Depends(_export),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Presign the sealed pack ZIP (gate ``report.export``). 409 until the pack is SEALED."""
    pack = await _load(session, caller, pack_id)
    if pack.status is not PackStatus.SEALED or pack.zip_blob_sha256 is None:
        raise ProblemException(
            status=409, code="conflict", title="Pack is not sealed yet", detail=pack.status.value
        )
    blob = await vault_repo.get_blob(session, pack.zip_blob_sha256)
    if blob is None:  # pragma: no cover - defensive (the seal wrote the blob row)
        raise ProblemException(status=404, code="not_found", title="Pack artifact not found")
    url = await storage.presign_get(blob.object_key, bucket=blob.bucket)
    return {"download_url": url, "sha256": pack.zip_blob_sha256, "content_type": "application/zip"}


# --- external delivery: time-boxed share links (S-pack-2, doc 06 §7.4, UJ-7) ------------
#
# Sharing/revoking rides the SAME ``report.evidence_pack.generate`` authority that produces packs
# (the pack-management owner via the SYSTEM override; the catalog stays CLOSED, no new key). The
# PUBLIC guest landing + download (``api/pack_share.py``) carry NO gate — the signed, time-boxed,
# revocable token IS the authorization. These POSTs are share-link *lifecycle* management; they do
# not mutate the immutable sealed pack content (the route-inventory proof whitelists them).


@router.post("/evidence-packs/{pack_id}/share", status_code=status.HTTP_201_CREATED)
async def share_pack_endpoint(
    pack_id: uuid.UUID,
    body: ShareCreate,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Mint a time-boxed Ed25519 share link for a SEALED pack. Returns the raw token + the share URL
    **once** (only its digest is stored). 409 if the pack is not sealed; 503 if the signing key is
    not provisioned."""
    link, token = await create_share_link(
        session,
        caller,
        pack_id,
        ttl_days=body.ttl_days,
        expires_at=body.expires_at,
        recipient=body.recipient,
    )
    base = get_settings().public_base_url.rstrip("/")
    return {
        **_share_link(link),
        "token": token,
        "share_url": f"{base}/api/v1/evidence-packs/shared?t={token}",
    }


@router.get("/evidence-packs/{pack_id}/share-links")
async def list_share_links_endpoint(
    pack_id: uuid.UUID,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """List a pack's share links (management view, no raw token). 404 if the pack is another org."""
    await _load(session, caller, pack_id)
    links = await packs_repo.list_share_links(session, pack_id)
    return [_share_link(link) for link in links]


@router.post("/evidence-packs/{pack_id}/share-links/{link_id}/revoke")
async def revoke_share_link_endpoint(
    pack_id: uuid.UUID,
    link_id: uuid.UUID,
    body: ShareRevoke,
    caller: AppUser = Depends(_generate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Revoke a share link (immediate — the public endpoint re-checks on every access). 404 if
    missing; 409 if already revoked. The sealed pack is untouched (doc 06 §7.4 frozen snapshot)."""
    link = await revoke_share_link(session, caller, pack_id, link_id, reason=body.reason)
    return _share_link(link)
