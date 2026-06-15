"""Gather the data for an MR minutes pack from the released version + directory + signatures,
then render it (S-mr-pack). Read-only: no DB writes, no blob writes. The endpoint has already
409'd if the review is unreleased; this layer also fail-closes (409 pack_unavailable) on a
missing minutes key."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...domain.mgmt_review.pack_render import render_minutes_pdf
from ...problems import ProblemException
from . import repository as repo

_MINUTES_KEY = "mgmt_review_minutes"


def minutes_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The frozen minutes dict, or 409 pack_unavailable (a non-MR / legacy version on this path)."""
    minutes = (snapshot or {}).get(_MINUTES_KEY)
    if not isinstance(minutes, dict):
        raise ProblemException(
            status=409, code="pack_unavailable", title="No minutes are available for this review"
        )
    return minutes


def _collect_user_ids(minutes: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for ro in minutes.get("outputs") or []:
        if ro.get("owner_user_id"):
            ids.add(str(ro["owner_user_id"]))
    for a in minutes.get("attendees") or []:
        if a.get("user_id"):
            ids.add(str(a["user_id"]))
    return ids


async def _resolve_names(session: AsyncSession, ids: set[str], org_id: uuid.UUID) -> dict[str, str]:
    # org_id-scoped: a name only resolves for a user in the review's own org (D1 is single-org, so
    # this is defence-in-depth + the every-table tenancy posture — never render a foreign name).
    if not ids:
        return {}
    uuids = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(i))
        except ValueError:  # a non-uuid leaked into the snapshot — skip, render shows "—"
            continue
    if not uuids:
        return {}
    # display_name only (NOT email) — the pack is mgmtReview.read-gated and the directory surface
    # only exposes {id, display_name} to ordinary members; an email fallback would leak admin-roster
    # PII into the PDF. A user with no display_name falls back to the (non-PII) id.
    rows = await session.execute(
        select(AppUser.id, AppUser.display_name).where(
            AppUser.id.in_(uuids), AppUser.org_id == org_id
        )
    )
    return {str(uid): (dn or str(uid)) for uid, dn in rows.all()}


def _signer_label(display_name: str | None, signer_user_id: uuid.UUID | None) -> str | None:
    """The sign-off label for one signature. A human signer (signer_user_id set) always gets a
    non-null label — display_name, else the (non-PII) id — so a human with no display_name is NOT
    misrendered as "system" (and no email PII is exposed). Only a true system signature (null
    signer_user_id) returns None (the render maps that to "system")."""
    if signer_user_id is None:
        return None
    return display_name or str(signer_user_id)


async def build_minutes_pdf(session: AsyncSession, doc: DocumentedInformation) -> bytes:
    """Render the released MR's filed minutes to a PDF (bytes). Assumes
    doc.current_effective_version_id is set (the endpoint 409s otherwise). Renders ONLY frozen /
    immutable facts (the version snapshot + the version's revision/effective fields + its
    append-only signatures + the doc's stable lifecycle state) — never mutable post-filing state
    like close_state, so the pack is a faithful, byte-stable rendering of the filed minutes."""
    version = await session.get(DocumentVersion, doc.current_effective_version_id)
    if (
        version is None
    ):  # pragma: no cover — the pointer is a live FK; endpoint guards the None case
        raise ProblemException(
            status=409, code="pack_unavailable", title="This review has not been released yet"
        )
    snapshot = version.metadata_snapshot or {}
    minutes = minutes_from_snapshot(snapshot)
    name_of = await _resolve_names(session, _collect_user_ids(minutes), doc.org_id)
    signatures = [
        {
            "signer": _signer_label(display_name, signer_user_id),
            "meaning": meaning.value if hasattr(meaning, "value") else str(meaning),
            "when": when.astimezone(datetime.UTC).isoformat() if when is not None else None,
            "method": method.value if hasattr(method, "value") else str(method),
        }
        for (
            display_name,
            signer_user_id,
            meaning,
            when,
            method,
        ) in await repo.list_signoffs_for_version(session, version.id)
    ]
    return render_minutes_pdf(
        identifier=doc.identifier,
        # the FROZEN title (snapshot), not the live doc.title — a metadata PATCH on the released MR
        # must not change the filed pack's bytes (the same class as close_state).
        title=snapshot.get("title") or doc.title,
        current_state=doc.current_state.value,
        revision_label=version.revision_label,
        effective_from=version.effective_from.isoformat() if version.effective_from else None,
        version_id=str(version.id),
        source_digest=version.source_blob_sha256,
        minutes=minutes,
        name_of=name_of,
        signatures=signatures,
    )
