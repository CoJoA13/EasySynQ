"""The public evidence-pack delivery surface (slice S-pack-2, doc 06 §7.4, UJ-7).

``GET /evidence-packs/shared?t=<token>`` (a minimal HTML landing) and
``GET /evidence-packs/shared/download?t=<token>&format=zip|pdf`` are **UNAUTHENTICATED** so an
external auditor (Olsen) can open a time-boxed share-link without an account — the point of UJ-7
delivery (the S7c ``/verify`` precedent: a signed token outside the PEP). Latch-exempt EXACT paths.

The token is the bearer credential; the authoritative, **revocable** state lives in the
``pack_share_link`` row, re-checked on **every** request — so a revoke takes effect at once and the
bytes are streamed **through the API** (never a presigned URL that could outlive a revoke). Every
successful download writes a ``PACK_DOWNLOADED`` audit row (R28 / doc 06 §7.4 "every view logged").
The raw token is never logged (digest only) and responses carry ``Referrer-Policy: no-referrer`` so
the URL doesn't leak via the Referer header. The landing surfaces the gap + exclusion (R28 honesty).
"""

from __future__ import annotations

import logging
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models.evidence_pack import EvidencePack
from ..db.models.pack_share_link import PackShareLink
from ..db.session import get_session
from ..services.packs import record_share_download, resolve_share_token
from ..services.vault import repository as vault_repo
from ..services.vault import storage, watermark

logger = logging.getLogger("easysynq.packs")
router = APIRouter(prefix="/api/v1", tags=["evidence-packs"])

_NO_REFERRER = {"Referrer-Policy": "no-referrer"}

_DENIED_MESSAGE = {
    "INVALID": "This link is invalid.",
    "EXPIRED": "This share link has expired.",
    "REVOKED": "This share link has been revoked.",
    "UNAVAILABLE": "This evidence pack is not available.",
}


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _denied_page(status: str) -> str:
    note = _DENIED_MESSAGE.get(status, "This link could not be verified.")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>EasySynQ — Evidence Pack</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:34rem;margin:3rem auto'>"
        f"<h1 style='color:#a50e0e'>✗ Unavailable</h1><p style='color:#555'>{escape(note)}</p>"
        "<hr><p style='color:#999;font-size:.85rem'>EasySynQ time-boxed evidence-pack delivery</p>"
        "</body></html>"
    )


def _landing_page(pack: EvidencePack, link: PackShareLink, token: str) -> str:
    gap = pack.gap_summary or {}
    excl = pack.exclusion_summary or {}
    zip_url = f"./shared/download?t={escape(token)}&format=zip"
    pdf_url = f"./shared/download?t={escape(token)}&format=pdf"
    rows = "".join(
        f"<tr><td style='color:#666;padding:.15rem 1rem .15rem 0'>{escape(k)}</td>"
        f"<td>{escape(v)}</td></tr>"
        for k, v in [
            ("Title", pack.title),
            ("Scope", f"{pack.scope_kind.value} {pack.scope_selector}"),
            (
                "Period",
                f"{pack.period_start or '—'} .. {pack.period_end or '—'}",
            ),
            ("Generated", pack.generated_at.isoformat() if pack.generated_at else "—"),
            ("Content hash", pack.content_hash or "—"),
            ("Records included", str(pack.item_count)),
            (
                "Excluded",
                f"{excl.get('permission_count', 0)} (permission), "
                f"{excl.get('absence_count', 0)} (absence)",
            ),
            (
                "Gap report",
                f"{gap.get('gap_count', 0)} of {gap.get('in_scope_star_clauses', 0)} "
                "mandatory clauses lacking evidence",
            ),
            ("Access expires", link.expires_at.isoformat()),
        ]
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>EasySynQ — Evidence Pack</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:42rem;margin:3rem auto'>"
        "<h1 style='color:#137333'>Evidence Pack</h1>"
        "<p style='color:#555'>A time-boxed, read-only audit bundle. Items excluded for permission "
        "or absence are listed above (never silently dropped).</p>"
        f"<table style='border-collapse:collapse;margin:1rem 0'>{rows}</table>"
        f"<p><a href='{zip_url}' style='display:inline-block;margin-right:1rem;padding:.5rem 1rem;"
        "background:#137333;color:#fff;border-radius:.4rem;text-decoration:none'>Download ZIP</a>"
        f"<a href='{pdf_url}' style='display:inline-block;padding:.5rem 1rem;background:#1a56b0;"
        "color:#fff;border-radius:.4rem;text-decoration:none'>Download PDF portfolio</a></p>"
        "<hr><p style='color:#999;font-size:.85rem'>EasySynQ time-boxed evidence-pack delivery</p>"
        "</body></html>"
    )


@router.get("/evidence-packs/shared", response_class=HTMLResponse)
async def shared_landing_endpoint(
    t: str = Query(..., description="The signed share token from the delivery link."),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """The guest landing page: verify the token + show the pack summary (incl. the R28 gap/exclusion
    surface) and the download links, or an honest 'expired/revoked/invalid' message. No auth."""
    res = await resolve_share_token(session, t)
    pid = str(res.pack.id) if res.pack else None
    logger.info(
        "packs.share_landing", extra={"extra_fields": {"status": res.status, "pack_id": pid}}
    )
    if res.status != "OK" or res.pack is None or res.link is None:
        return HTMLResponse(_denied_page(res.status), status_code=403, headers=_NO_REFERRER)
    return HTMLResponse(_landing_page(res.pack, res.link, t), headers=_NO_REFERRER)


@router.get("/evidence-packs/shared/download")
async def shared_download_endpoint(
    request: Request,
    t: str = Query(..., description="The signed share token from the delivery link."),
    format: Literal["zip", "pdf"] = Query("zip"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream the sealed pack to the guest (ZIP canonical, or the live-stamped PDF portfolio).
    Verifies the token + the **revocable** DB state on every request, audits PACK_DOWNLOADED, and
    streams the bytes through the API (revoke is immediate; no presigned URL outlives it)."""
    res = await resolve_share_token(session, t)
    if res.status != "OK" or res.pack is None or res.link is None:
        logger.info("packs.share_download_denied", extra={"extra_fields": {"status": res.status}})
        msg = _DENIED_MESSAGE.get(res.status, "denied")
        return Response(msg, status_code=403, headers=_NO_REFERRER)

    pack, link = res.pack, res.link
    portfolio_sha = pack.portfolio_blob_sha256
    zip_sha = pack.zip_blob_sha256
    # 409 BEFORE auditing — a download that can't happen is not a "view".
    if format == "pdf" and portfolio_sha is None:
        return Response(
            "PDF portfolio is not available for this pack; use the ZIP variant.",
            status_code=409,
            headers=_NO_REFERRER,
        )
    if zip_sha is None:  # pragma: no cover - resolve_share_token already asserts SEALED + zip
        return Response("Pack artifact not found", status_code=404, headers=_NO_REFERRER)

    await record_share_download(session, link, pack, fmt=format, client_ip=_client_ip(request))

    if format == "pdf" and portfolio_sha is not None:
        base = await storage.fetch_bytes(portfolio_sha, bucket=get_settings().s3_bucket_renditions)
        recipient = link.recipient or "an external auditor"
        expiry = link.expires_at.date().isoformat()
        stamped = watermark.stamp_per_request_copy(
            base,
            banner=f"EVIDENCE PACK — provided to {recipient} under time-boxed external access",
            footer_note=f"Access expires {expiry} · downloaded via EasySynQ",
        )
        return Response(
            stamped,
            media_type="application/pdf",
            headers={
                **_NO_REFERRER,
                "Content-Disposition": f'attachment; filename="evidence-pack-{pack.id}.pdf"',
            },
        )

    blob = await vault_repo.get_blob(session, zip_sha)
    if blob is None:  # pragma: no cover - the seal wrote the blob row
        return Response("Pack artifact not found", status_code=404, headers=_NO_REFERRER)
    return StreamingResponse(
        storage.stream_object(blob.object_key, bucket=blob.bucket),
        media_type="application/zip",
        headers={
            **_NO_REFERRER,
            "Content-Disposition": f'attachment; filename="evidence-pack-{pack.id}.zip"',
        },
    )
