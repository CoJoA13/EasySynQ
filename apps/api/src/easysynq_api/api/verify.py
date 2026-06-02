"""The public controlled-rendition verify page (slice S7c, doc 05 §6.4).

``GET /verify?t=<token>`` is **UNAUTHENTICATED** so anyone holding a printed/exported controlled
copy (an external auditor, a recipient) can check its currency without an account — the whole point
of the verify token (R11: copies outside the mirror are reachable only this way). The token is
Ed25519-signed (unforgeable), so this only confirms what a valid token encodes — no enumeration.

Minimal disclosure: a status banner (**CURRENT / SUPERSEDED / UNKNOWN**) + the identifier + the
*current* effective revision/date — never document content. Each hit is logged. Identifier/revision
are ``html.escape``'d; the token is never reflected. The signing key is read once + memoized
(``verify_token._read_key``), so a request is at most ~3 indexed PK lookups. Edge/app rate-limiting
for this public route is a **v1 hardening** (the signed token already prevents enumeration).
"""

from __future__ import annotations

import logging
from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._vault_enums import VersionState
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.session import get_session
from ..services.vault import verify_token

logger = logging.getLogger("easysynq.vault")
router = APIRouter(prefix="/api/v1", tags=["verify"])

_BANNER = {
    "CURRENT": ("#137333", "✓ CURRENT", "This is the current effective revision."),
    "SUPERSEDED": (
        "#b06000",
        "⚠ SUPERSEDED",
        "A newer revision now governs — do not rely on this copy.",
    ),
    "UNKNOWN": ("#a50e0e", "✗ UNKNOWN", "This copy could not be verified."),
}


def _page(
    status: str, identifier: str | None = None, rev: str | None = None, eff: str | None = None
) -> str:
    color, title, note = _BANNER[status]
    detail = ""
    if identifier:
        detail = f"<p><b>{escape(identifier)}</b>"
        if status == "SUPERSEDED" and rev:
            detail += f" — current revision is <b>{escape(rev)}</b>"
            if eff:
                detail += f" (effective {escape(eff)})"
        detail += "</p>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>EasySynQ — Verify</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto'>"
        f"<h1 style='color:{color}'>{title}</h1><p style='color:#555'>{note}</p>{detail}"
        "<hr><p style='color:#999;font-size:.85rem'>EasySynQ controlled-document verification</p>"
        "</body></html>"
    )


@router.get("/verify", response_class=HTMLResponse)
async def verify_endpoint(
    t: str = Query(
        ..., description="The signed verify token from the controlled copy's QR/footer."
    ),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    claims = verify_token.verify(t)
    if claims is None:
        logger.info(
            "vault.verify", extra={"extra_fields": {"status": "UNKNOWN", "reason": "token"}}
        )
        return HTMLResponse(_page("UNKNOWN"))

    version = await session.get(DocumentVersion, claims.version_id)
    if (
        version is None
        or version.document_id != claims.document_id
        or version.source_blob_sha256 != claims.content_digest
    ):
        logger.info(
            "vault.verify", extra={"extra_fields": {"status": "UNKNOWN", "reason": "claims"}}
        )
        return HTMLResponse(_page("UNKNOWN"))

    doc = await session.get(DocumentedInformation, claims.document_id)
    if doc is None:  # pragma: no cover - a version's parent always exists
        return HTMLResponse(_page("UNKNOWN"))

    is_current = (
        doc.current_effective_version_id == claims.version_id
        and version.version_state is VersionState.Effective
    )
    status = "CURRENT" if is_current else "SUPERSEDED"

    rev = eff = None
    if doc.current_effective_version_id is not None:
        current = await session.get(DocumentVersion, doc.current_effective_version_id)
        if current is not None:
            rev = current.revision_label
            eff = current.effective_from.date().isoformat() if current.effective_from else None

    logger.info(
        "vault.verify",
        extra={"extra_fields": {"status": status, "identifier": doc.identifier}},
    )
    return HTMLResponse(_page(status, doc.identifier, rev, eff))
