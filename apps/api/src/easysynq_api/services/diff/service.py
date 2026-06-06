"""The version-diff orchestration (slice S-dcr-3a; doc 05 §8). I/O around the pure ``domain/diff``.

``build_version_diff`` assembles the doc 05 §8.1 two core dimensions + the provenance header band
for two versions of the SAME document: (1) the metadata diff (pure, over the frozen snapshots);
(2) the text redline (on-demand Tika extraction of each version's source blob → pure line-LCS;
degrades to ``unavailable`` if either text can't be extracted); (3) a per-version provenance
header (the version's immutable columns + its ``signature_event[]`` projected to ``{user_id}``
ONLY — no email/keycloak subject, the pack-dossier PII boundary). The ``dcr_id`` link in the
header is a forward seam (NULL until S-dcr-5 wires ``document_version.dcr_id``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._signature_enums import SignatureMeaning
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.signature_event import SignatureEvent
from ...domain.diff import diff_metadata, redline
from ..vault import repository as vault_repo
from ..vault import storage
from .extractor import get_text_extractor


async def _provenance(session: AsyncSession, version: DocumentVersion) -> dict[str, Any]:
    """The doc 05 §8.1 provenance header for one version: its immutable columns + the version's
    signature events (signer projected to {user_id} only — the PII boundary). dcr_id is a forward
    seam (S-dcr-5)."""
    sigs = (
        (
            await session.execute(
                select(SignatureEvent)
                .where(SignatureEvent.signed_object_id == version.id)
                .order_by(SignatureEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "version_id": str(version.id),
        "version_seq": version.version_seq,
        "revision_label": version.revision_label,
        "version_state": version.version_state.value,
        "change_significance": version.change_significance.value,
        "change_reason": version.change_reason,
        "effective_from": version.effective_from.isoformat() if version.effective_from else None,
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "author_user_id": str(version.author_user_id),
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "signatures": [
            {
                "meaning": s.meaning.value,
                "signer_user_id": str(s.signer_user_id) if s.signer_user_id else None,
                "signed_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sigs
            # release/approval/obsolete signatures are the provenance; reserved Part-11 meanings
            # stay out of v1 (they are never emitted).
            if s.meaning
            in (SignatureMeaning.approval, SignatureMeaning.release, SignatureMeaning.obsolete)
        ],
    }


async def _extract_text(session: AsyncSession, version: DocumentVersion) -> str | None:
    """On-demand text for a version's source blob (None if the blob/bytes/text are unavailable —
    fail-closed: a missing blob object or an extractor outage degrades the diff to ``unavailable``,
    never a 500)."""
    blob = await vault_repo.get_blob(session, version.source_blob_sha256)
    if blob is None:  # pragma: no cover - defensive (the FK guarantees it)
        return None
    try:
        data = await storage.fetch_bytes(blob.object_key, bucket=blob.bucket)
    except Exception:  # noqa: BLE001 — a fetch outage degrades to text-unavailable, not a 500
        return None
    return await get_text_extractor().extract_text(
        data=data, mime_type=blob.mime_type, filename=_filename(version, blob)
    )


def _filename(version: DocumentVersion, blob: Blob) -> str:
    # A best-effort filename for the extractor's strategy choice (ext-sniffing) — identifier +
    # the blob's mime-derived extension is enough; the bytes are authoritative.
    ident = version.metadata_snapshot.get("identifier") or str(version.document_id)
    return f"{ident}-{version.revision_label}"


async def build_version_diff(
    session: AsyncSession,
    from_version: DocumentVersion,
    to_version: DocumentVersion,
) -> dict[str, Any]:
    """The doc 05 §8 diff of ``from_version`` → ``to_version`` (same document; the caller validated
    org + same-document + the gate). Metadata diff + text redline + both provenance headers."""
    metadata_deltas = diff_metadata(from_version.metadata_snapshot, to_version.metadata_snapshot)

    old_text = await _extract_text(session, from_version)
    new_text = await _extract_text(session, to_version)
    if old_text is None or new_text is None:
        text_diff: dict[str, Any] = {
            "status": "unavailable",
            "reason": "source text could not be extracted (Tika unavailable or a non-text format)",
        }
    else:
        text_diff = {
            "status": "ok",
            "hunks": [{"op": h.op, "text": h.text} for h in redline(old_text, new_text)],
        }

    return {
        "document_id": str(to_version.document_id),
        "from": await _provenance(session, from_version),
        "to": await _provenance(session, to_version),
        "metadata_diff": [
            {"field": d.field, "from": d.from_value, "to": d.to_value, "changed": d.changed}
            for d in metadata_deltas
        ],
        "text_diff": text_diff,
    }
