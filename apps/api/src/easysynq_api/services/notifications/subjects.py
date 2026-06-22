"""Polymorphic subject resolution for notifications (spec §5, refute L5-2).

The engine hook has only (subject_type, subject_id) — this module loads the human
identifier/title and builds the deep link, covering all 7 slice-1 subject types with
a /tasks fallback so a new/unknown kind never renders a broken link.

Live-schema notes (verified against ORM 2026-06-21):
- Dcr: has ``identifier`` but NO ``title`` — we use reason_text as the title surrogate.
- ManagementReview: has NO ``identifier`` or ``title`` — only ``period_label``.
  We use period_label as the identifier surrogate (e.g. "Q1 2025"); id-as-str fallback.
- All other backed models (DocumentedInformation, ImprovementInitiative) have both fields.

SPA route corrections vs. brief:
- DCR: /dcr/{id}  →  /dcrs?dcr={id}  (drawer-opened via query param, per SpawnDcrModal)
- MGMT_REVIEW: /management-review/{id}  →  /management-reviews/{id}  (App.tsx route)
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SubjectInfo:
    identifier: str
    title: str
    kind: str
    deep_link: str


# ---------------------------------------------------------------------------
# Route map
# ---------------------------------------------------------------------------

# subject_type → the SPA route fragment.
# DOC_ACK / PERIODIC_REVIEW / LEADERSHIP_AUTHORIZATION resolve against the underlying
# document (subject_id is the document id).  Everything unmapped falls back to /tasks.
_ROUTES: dict[str, str] = {
    "DOCUMENT": "/documents/{id}",
    "DOC_ACK": "/documents/{id}",
    "PERIODIC_REVIEW": "/documents/{id}",
    "LEADERSHIP_AUTHORIZATION": "/documents/{id}",
    "DCR": "/dcrs?dcr={id}",  # drawer-opened via query param
    "CAPA": "/capa?capa={id}",
    "IMPROVEMENT_INITIATIVE": "/improvement?initiative={id}",
    "MGMT_REVIEW": "/management-reviews/{id}",  # plural — App.tsx route
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def deep_link_for(subject_type: str, subject_id: uuid.UUID) -> str:
    """Return the absolute SPA deep link for *subject_type*/*subject_id*.

    Unmapped kinds fall back to /tasks so a future subject type never produces a
    broken link in an email (spec §5 / refute L5-2).
    """
    base = get_settings().app_base_url.rstrip("/")
    frag = _ROUTES.get(subject_type)
    if frag is None:
        return f"{base}/tasks"
    return f"{base}{frag.format(id=subject_id)}"


def prefs_link() -> str:
    """The SPA notification-preferences route (the {{prefs_link}} template var)."""
    return f"{get_settings().app_base_url.rstrip('/')}/settings/notifications"


# ---------------------------------------------------------------------------
# Async resolver
# ---------------------------------------------------------------------------


async def resolve_subject(
    session: AsyncSession,
    subject_type: str,
    subject_id: uuid.UUID,
) -> SubjectInfo:
    """Load the subject's identifier + title from the DB.

    Defensive: a missing or unknown row degrades to identifier=str(id), title=""
    rather than raising — enqueue is best-effort (spec §4).
    """
    identifier = str(subject_id)
    title = ""

    if subject_type in (
        "DOCUMENT",
        "DOC_ACK",
        "PERIODIC_REVIEW",
        "LEADERSHIP_AUTHORIZATION",
        "CAPA",
    ):
        # DocumentedInformation backs all document-related subjects, and CAPA is a
        # DocumentedInformation subtype whose base row carries identifier + title.
        from ...db.models.documented_information import DocumentedInformation

        row = await session.get(DocumentedInformation, subject_id)
        if row is not None:
            identifier = row.identifier
            title = getattr(row, "title", "") or ""

    elif subject_type == "DCR":
        from ...db.models.dcr import Dcr

        dcr = await session.get(Dcr, subject_id)
        if dcr is not None:
            identifier = dcr.identifier
            # Dcr has no .title — use reason_text as a human-readable surrogate.
            title = getattr(dcr, "reason_text", "") or ""

    elif subject_type == "IMPROVEMENT_INITIATIVE":
        from ...db.models.improvement_initiative import ImprovementInitiative

        init = await session.get(ImprovementInitiative, subject_id)
        if init is not None:
            identifier = init.identifier
            title = init.title or ""

    elif subject_type == "MGMT_REVIEW":
        from ...db.models.management_review import ManagementReview

        mr = await session.get(ManagementReview, subject_id)
        if mr is not None:
            # ManagementReview has no identifier/title — period_label is the closest
            # human-readable label (e.g. "Q1 2025"); fall back to id-as-str if null.
            identifier = getattr(mr, "period_label", None) or str(subject_id)
            title = ""

    return SubjectInfo(
        identifier=identifier,
        title=title,
        kind=subject_type,
        deep_link=deep_link_for(subject_type, subject_id),
    )
