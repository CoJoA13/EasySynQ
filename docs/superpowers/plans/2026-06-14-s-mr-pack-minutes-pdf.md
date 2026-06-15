# Management Review minutes pack (PDF) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a downloadable, table-styled PDF of a released Management Review's *filed* minutes — rendered on demand from the released version's frozen snapshot + its e-signatures — exposed at `GET /management-reviews/{id}/pack` and surfaced as a button on the MR detail page.

**Architecture:** A pure reportlab **Platypus** render leaf (`domain/mgmt_review/pack_render.py`) takes plain data (the frozen minutes dict + a resolved name map + sign-off rows + version meta) and returns deterministic PDF bytes. A thin service (`services/mgmt_review/pack.py`) gathers that data from the released version snapshot, the `app_user` directory, and `signature_event`, then calls the leaf. A sync endpoint streams `application/pdf`. No Celery, no cache, no blob, no migration, no new key. The FE fetches the authed bytes via `getBlob` → objectURL → download.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy (async) · reportlab (Platypus) · pypdf (test extraction) · React/TS + Mantine · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-14-s-mr-pack-minutes-pdf-design.md` (owner-approved).

---

## Conventions (read once)

- **Windows box reality:** API **integration tests + the full unit suite are CI-only** (ProactorEventLoop / native crash). Locally verify backend via `ruff` + `mypy --strict` + **targeted** unit tests (`uv run pytest tests/unit/<file> -v`). Write the integration test failing-first *by reasoning* and let CI verify it.
- **Branch:** already on `feat/s-mr-pack-minutes-pdf` (the spec is committed there).
- **Commit trailer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Web test traps:** `import { expect, it } from "vitest"` in any test using jest-dom matchers; pin MSW fixtures with `satisfies <Type>`; add a jest-axe smoke if you touch page structure.

## File structure (decomposition)

| File | Responsibility | Task |
|------|----------------|------|
| `apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py` | **Create** — pure: frozen-minutes data → PDF bytes (Platypus tables, deterministic). No DB/IO/Problem. | 1 |
| `apps/api/tests/unit/test_mr_pack_render.py` | **Create** — unit tests for the render leaf. | 1 |
| `apps/api/src/easysynq_api/services/mgmt_review/repository.py` | **Modify** — add `list_signoffs_for_version`. | 2 |
| `apps/api/src/easysynq_api/services/mgmt_review/pack.py` | **Create** — `minutes_from_snapshot` (pure, 409 on missing key) + `build_minutes_pdf` (gather + render). | 2 |
| `apps/api/src/easysynq_api/services/mgmt_review/__init__.py` | **Modify** — export `build_minutes_pdf`. | 2 |
| `apps/api/tests/unit/test_mr_pack_service.py` | **Create** — unit test for `minutes_from_snapshot`. | 2 |
| `apps/api/src/easysynq_api/api/mgmt_review.py` | **Modify** — add `GET /management-reviews/{review_id}/pack`. | 3 |
| `packages/contracts/openapi.yaml` | **Modify** — document the new endpoint (binary response). | 4 |
| `apps/api/tests/integration/test_mgmt_review_pack.py` | **Create** — end-to-end (CI-only). | 5 |
| `apps/web/src/features/management-review/ManagementReviewDetailPage.tsx` | **Modify** — the download button + handler. | 6 |
| `apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx` | **Modify** — button visibility + download + 409. | 6 |

---

## Task 1: The pure render leaf

**Files:**
- Create: `apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py`
- Test: `apps/api/tests/unit/test_mr_pack_render.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/unit/test_mr_pack_render.py
from __future__ import annotations

from pypdf import PdfReader
import io

from easysynq_api.domain.mgmt_review.pack_render import render_minutes_pdf


def _args(**over):
    base = dict(
        identifier="MR-GEN-003",
        title="Annual Management Review 2026",
        current_state="Effective",
        close_state="ActionsTracked",
        revision_label="1.0",
        effective_from="2026-06-14T00:00:00+00:00",
        version_id="11111111-1111-1111-1111-111111111111",
        source_digest="abc123def456",
        minutes={
            "period_label": "FY2026",
            "review_date": "2026-06-10",
            "attendees": [{"name": "Mara QM", "role": "Quality Manager", "user_id": "u-mara"}],
            "inputs": [
                {"input_type": "OBJECTIVES_STATUS", "available": True,
                 "source_ref": {"on_track": 4, "at_risk": 1}, "position": 1},
                {"input_type": "AUDIT_RESULTS", "available": False, "source_ref": None, "position": 2},
            ],
            "outputs": [
                {"output_type": "ACTION", "description": "Re-baseline the supplier KPI.",
                 "owner_user_id": "u-diego", "due_date": "2026-09-30"},
                {"output_type": "DECISION", "description": "Approve the 2027 objectives.",
                 "owner_user_id": None, "due_date": None},
            ],
            "compiled_at": "2026-06-10T09:00:00+00:00",
        },
        name_of={"u-diego": "Diego PO", "u-mara": "Mara QM"},
        signatures=[
            {"signer": "Ken Approver", "meaning": "approval", "when": "2026-06-12T10:00:00+00:00", "method": "SESSION"},
            {"signer": None, "meaning": "release", "when": "2026-06-14T00:00:00+00:00", "method": "SESSION"},
        ],
    )
    base.update(over)
    return base


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def test_render_produces_pdf_with_minutes_content():
    pdf = render_minutes_pdf(**_args())
    assert pdf[:4] == b"%PDF"
    text = _text(pdf)
    assert "MR-GEN-003" in text
    assert "Annual Management Review 2026" in text
    assert "Mara QM" in text                 # attendee
    assert "Re-baseline the supplier KPI." in text   # output description
    assert "Diego PO" in text                # resolved owner name (not the raw uuid)
    assert "Ken Approver" in text            # signer
    assert "approval" in text and "release" in text  # sign-off meanings
    assert "abc123def456" in text            # footer source digest


def test_render_is_byte_deterministic():
    assert render_minutes_pdf(**_args()) == render_minutes_pdf(**_args())


def test_render_null_signer_shows_system():
    pdf = render_minutes_pdf(**_args())
    assert "system" in _text(pdf)            # the release signature had signer=None


def test_render_handles_empty_sections():
    args = _args()
    args["minutes"] = {**args["minutes"], "attendees": [], "inputs": [], "outputs": []}
    args["signatures"] = []
    pdf = render_minutes_pdf(**args)
    assert pdf[:4] == b"%PDF"
    assert "none recorded" in _text(pdf).lower()


def test_render_tolerates_odd_source_ref_shapes():
    args = _args()
    args["minutes"] = {
        **args["minutes"],
        "inputs": [
            {"input_type": "X", "available": True, "source_ref": "a bare string", "position": 1},
            {"input_type": "Y", "available": True, "source_ref": ["a", "list"], "position": 2},
            {"input_type": "Z", "available": True, "source_ref": None, "position": 3},
        ],
    }
    pdf = render_minutes_pdf(**args)   # must not raise
    assert pdf[:4] == b"%PDF"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/unit/test_mr_pack_render.py -v` (from `apps/api`)
Expected: FAIL — `ModuleNotFoundError: easysynq_api.domain.mgmt_review.pack_render`.

- [ ] **Step 3: Write the implementation**

```python
# apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py
"""Pure reportlab (Platypus) render of a Management Review's filed minutes → a table-styled PDF.

S-mr-pack. NO DB / NO IO / NO ProblemException — the caller
(services/mgmt_review/pack.build_minutes_pdf) gathers the frozen minutes snapshot + resolved user
names + the version's sign-off signatures and passes plain data in. Deterministic via an invariant
canvasmaker (the PDF /ID + metadata are fixed) so a given released MR renders byte-identical — no
Date.now(), only stored values. Defensive on the free-form source_ref / attendee shapes (never raises
on an unexpected JSON shape)."""

from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_MARGIN = 54.0  # aligns with the evidence-pack portfolio
_GRID = colors.HexColor("#d0d5dd")
_HEADER_BG = colors.HexColor("#f2f4f7")


def _invariant_canvas(*args: Any, **kw: Any) -> Canvas:
    """Force a deterministic PDF (/ID + metadata). Setting the key (rather than passing as a partial)
    avoids a duplicate-kwarg TypeError if this reportlab forwards its own ``invariant`` to the maker."""
    kw["invariant"] = 1
    return Canvas(*args, **kw)


def _summary(source_ref: Any) -> str:
    """A generic, never-raising one-cell summary of the free-form 9.3.2 source_ref."""
    if isinstance(source_ref, dict):
        return "; ".join(f"{k}: {source_ref[k]}" for k in sorted(source_ref)) or "—"
    if source_ref in (None, "", [], {}):
        return "—"
    return str(source_ref)


def _attendee_name(a: dict[str, Any], name_of: dict[str, str]) -> str:
    nm = a.get("name")
    if nm:
        return str(nm)
    uid = a.get("user_id")
    return name_of.get(str(uid), "—") if uid else "—"


def render_minutes_pdf(
    *,
    identifier: str,
    title: str,
    current_state: str,
    close_state: str | None,
    revision_label: str,
    effective_from: str | None,
    version_id: str,
    source_digest: str,
    minutes: dict[str, Any],
    name_of: dict[str, str],
    signatures: list[dict[str, Any]],
) -> bytes:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("mr_body", parent=styles["BodyText"], fontSize=8.5, leading=11)
    h2 = ParagraphStyle("mr_h2", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=4)
    title_style = ParagraphStyle("mr_title", parent=styles["Title"], fontSize=15, spaceAfter=2)

    def p(text: str) -> Paragraph:
        return Paragraph(escape(str(text)), body)

    def grid(rows: list[list[Any]], widths_in: list[float]) -> Table:
        t = Table(rows, colWidths=[w * inch for w in widths_in], hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return t

    def none_row() -> Table:
        return grid([[p("— none recorded —")]], [6.4])

    flow: list[Any] = [
        Paragraph("Management Review — minutes (controlled record)", title_style),
        Spacer(1, 6),
    ]

    # 1. metadata (2-col key/value)
    flow.append(
        grid(
            [
                ["Identifier", p(identifier)],
                ["Title", p(title)],
                ["Period", p(minutes.get("period_label") or "—")],
                ["Review date", p(minutes.get("review_date") or "—")],
                ["State", p(current_state + (f" · {close_state}" if close_state else ""))],
                [
                    "Revision",
                    p(revision_label + (f" · effective {effective_from}" if effective_from else "")),
                ],
            ],
            [1.4, 5.0],
        )
    )

    # 2. attendees
    flow.append(Paragraph("Attendees", h2))
    attendees = minutes.get("attendees") or []
    if attendees:
        rows = [["Name", "Role"]] + [
            [p(_attendee_name(a, name_of)), p(a.get("role") or "—")] for a in attendees
        ]
        flow.append(grid(rows, [3.2, 3.2]))
    else:
        flow.append(none_row())

    # 3. 9.3.2 inputs
    flow.append(Paragraph("Review inputs (9.3.2)", h2))
    inputs = sorted(minutes.get("inputs") or [], key=lambda r: r.get("position", 0))
    if inputs:
        rows = [["Input", "Available", "Summary"]] + [
            [
                p(ri.get("input_type") or "—"),
                p("Yes" if ri.get("available") else "No"),
                p(_summary(ri.get("source_ref"))),
            ]
            for ri in inputs
        ]
        flow.append(grid(rows, [1.7, 0.9, 3.8]))
    else:
        flow.append(none_row())

    # 4. 9.3.3 outputs / decisions
    flow.append(Paragraph("Review outputs / decisions (9.3.3)", h2))
    outputs = minutes.get("outputs") or []
    if outputs:
        rows = [["Type", "Decision / action", "Owner", "Due"]] + [
            [
                p(ro.get("output_type") or "—"),
                p(ro.get("description") or "—"),
                p(name_of.get(str(ro.get("owner_user_id")), "—") if ro.get("owner_user_id") else "—"),
                p(ro.get("due_date") or "—"),
            ]
            for ro in outputs
        ]
        flow.append(grid(rows, [1.3, 3.1, 1.4, 0.6]))
    else:
        flow.append(none_row())

    # 5. sign-off
    flow.append(Paragraph("Sign-off", h2))
    if signatures:
        rows = [["Signer", "Meaning", "When (UTC)", "Method"]] + [
            [
                p(s.get("signer") or "system"),
                p(s.get("meaning") or "—"),
                p(s.get("when") or "—"),
                p(s.get("method") or "—"),
            ]
            for s in signatures
        ]
        flow.append(grid(rows, [2.1, 1.3, 2.2, 0.8]))
    else:
        flow.append(none_row())

    def _footer(canvas: Canvas, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(
            _MARGIN,
            36,
            f"Derived printable view of the filed minutes — canonical record: Management Review "
            f"version {version_id} (Rev {revision_label}).",
        )
        canvas.drawString(
            _MARGIN,
            26,
            f"Minutes source digest: {source_digest} — re-hash the application/json source blob "
            f"(RFC 8785 JCS) to verify.",
        )
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=f"{identifier} — minutes",
    )
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer, canvasmaker=_invariant_canvas)
    return buf.getvalue()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/unit/test_mr_pack_render.py -v`
Expected: PASS (all 5).
> If `test_render_is_byte_deterministic` fails, the installed reportlab embeds a wall-clock/ID despite the invariant canvasmaker — investigate (do NOT weaken the assert): confirm `_invariant_canvas` is actually used, and as a last resort wrap the build with `reportlab.rl_config.invariant` set/restored around `doc.build`. Determinism is a real requirement here.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py && uv run ruff format --check apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py && uv run mypy --strict apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/domain/mgmt_review/pack_render.py apps/api/tests/unit/test_mr_pack_render.py
git commit -m "feat(s-mr-pack): pure reportlab render of MR minutes → table-styled PDF

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: The gather service + signoff query

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/repository.py`
- Create: `apps/api/src/easysynq_api/services/mgmt_review/pack.py`
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/__init__.py`
- Test: `apps/api/tests/unit/test_mr_pack_service.py`

- [ ] **Step 1: Write the failing unit test** (the pure `minutes_from_snapshot` helper — DB-free)

```python
# apps/api/tests/unit/test_mr_pack_service.py
from __future__ import annotations

import pytest

from easysynq_api.problems import ProblemException
from easysynq_api.services.mgmt_review.pack import minutes_from_snapshot


def test_minutes_from_snapshot_returns_dict():
    snap = {"mgmt_review_minutes": {"period_label": "FY2026", "inputs": [], "outputs": []}}
    assert minutes_from_snapshot(snap)["period_label"] == "FY2026"


def test_minutes_from_snapshot_409_when_key_absent():
    with pytest.raises(ProblemException) as ei:
        minutes_from_snapshot({"distribution": {}})
    assert ei.value.status == 409
    assert ei.value.code == "pack_unavailable"


def test_minutes_from_snapshot_409_when_not_a_dict():
    with pytest.raises(ProblemException) as ei:
        minutes_from_snapshot({"mgmt_review_minutes": "oops"})
    assert ei.value.status == 409
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/unit/test_mr_pack_service.py -v`
Expected: FAIL — `ModuleNotFoundError: ...services.mgmt_review.pack`.

- [ ] **Step 3a: Add the signoff query** to `repository.py`

Add these imports at the top of `repository.py` (alongside the existing ones):

```python
from ...db.models._signature_enums import SignatureMeaning, SignedObjectType
from ...db.models.app_user import AppUser
from ...db.models.signature_event import SignatureEvent
```

Append this function:

```python
# (signer_display_name | None, meaning, created_at, method) — the MR pack sign-off rows.
SignoffRow = tuple[Any, Any, Any, Any]


async def list_signoffs_for_version(
    session: AsyncSession, version_id: uuid.UUID
) -> list[SignoffRow]:
    """The approval + release signatures on an MR's released version, oldest first, with the signer's
    display name (OUTER JOIN — a null signer is a Beat-activated future-dated release → name None).
    The MR rides document.approve/release, so the signatures carry signed_object_type=document_version
    + signed_object_id = the version id."""
    rows = await session.execute(
        select(
            AppUser.display_name,
            SignatureEvent.meaning,
            SignatureEvent.created_at,
            SignatureEvent.method,
        )
        .outerjoin(AppUser, AppUser.id == SignatureEvent.signer_user_id)
        .where(
            SignatureEvent.signed_object_type == SignedObjectType.document_version,
            SignatureEvent.signed_object_id == version_id,
            SignatureEvent.meaning.in_([SignatureMeaning.approval, SignatureMeaning.release]),
        )
        .order_by(SignatureEvent.created_at)
    )
    return [tuple(r) for r in rows.all()]
```

- [ ] **Step 3b: Create `pack.py`** (the service)

```python
# apps/api/src/easysynq_api/services/mgmt_review/pack.py
"""Gather the data for an MR minutes pack from the released version + directory + signatures, then
render it (S-mr-pack). Read-only: no DB writes, no blob writes. The endpoint has already 409'd if the
review is unreleased; this layer also fail-closes (409 pack_unavailable) on a missing minutes key."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.management_review import ManagementReview
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


async def _resolve_names(session: AsyncSession, ids: set[str]) -> dict[str, str]:
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
    rows = await session.execute(
        select(AppUser.id, AppUser.display_name, AppUser.email).where(AppUser.id.in_(uuids))
    )
    return {str(uid): (dn or em or str(uid)) for uid, dn, em in rows.all()}


async def build_minutes_pdf(
    session: AsyncSession, mr: ManagementReview, doc: DocumentedInformation
) -> bytes:
    """Render the released MR's filed minutes to a PDF (bytes). Assumes doc.current_effective_version_id
    is set (the endpoint 409s otherwise)."""
    version = await session.get(DocumentVersion, doc.current_effective_version_id)
    if version is None:  # pragma: no cover — the pointer is a live FK; endpoint guards the None case
        raise ProblemException(
            status=409, code="pack_unavailable", title="This review has not been released yet"
        )
    minutes = minutes_from_snapshot(version.metadata_snapshot)
    name_of = await _resolve_names(session, _collect_user_ids(minutes))
    signatures = [
        {
            "signer": signer,
            "meaning": meaning.value if hasattr(meaning, "value") else str(meaning),
            "when": when.astimezone(datetime.UTC).isoformat() if when is not None else None,
            "method": method.value if hasattr(method, "value") else str(method),
        }
        for (signer, meaning, when, method) in await repo.list_signoffs_for_version(
            session, version.id
        )
    ]
    return render_minutes_pdf(
        identifier=doc.identifier,
        title=doc.title,
        current_state=doc.current_state.value,
        close_state=mr.close_state.value if mr.close_state is not None else None,
        revision_label=version.revision_label,
        effective_from=version.effective_from.isoformat() if version.effective_from else None,
        version_id=str(version.id),
        source_digest=version.source_blob_sha256,
        minutes=minutes,
        name_of=name_of,
        signatures=signatures,
    )
```

- [ ] **Step 3c: Export from `__init__.py`**

In `apps/api/src/easysynq_api/services/mgmt_review/__init__.py`, add `build_minutes_pdf` to the imports + `__all__` (mirror the existing export style, e.g. alongside `close_review`/`compile_inputs`):

```python
from .pack import build_minutes_pdf
```
and add `"build_minutes_pdf",` to `__all__`.

- [ ] **Step 4: Run the unit test + lint/type**

Run: `uv run pytest apps/api/tests/unit/test_mr_pack_service.py -v`
Expected: PASS (3).
Run: `uv run ruff check apps/api/src/easysynq_api/services/mgmt_review/ && uv run mypy --strict apps/api/src/easysynq_api/services/mgmt_review/pack.py apps/api/src/easysynq_api/services/mgmt_review/repository.py`
Expected: clean. (If mypy flags the `SignoffRow = tuple[Any, ...]` / `.value` access, the `hasattr` guards keep it honest; adjust annotations as mypy directs without loosening behavior.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/ apps/api/tests/unit/test_mr_pack_service.py
git commit -m "feat(s-mr-pack): gather service + version sign-off query for the minutes pack

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: The endpoint

**Files:**
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py`

> No standalone unit test (it needs a DB) — covered by the Task 5 integration test. Keep the handler thin.

- [ ] **Step 1: Add `build_minutes_pdf` to the service import block**

In `api/mgmt_review.py`, add `build_minutes_pdf` to the `from ..services.mgmt_review import (...)` list (keep alphabetical-ish with the neighbors).

- [ ] **Step 2: Add the endpoint** — place it **immediately before** the `@router.get("/management-reviews/{review_id}")` handler (the sub-paths-before-`/{review_id}` file convention; `Response` is already imported):

```python
@router.get("/management-reviews/{review_id}/pack")
async def get_review_pack_endpoint(
    review_id: uuid.UUID,
    caller: AppUser = Depends(_mr_read),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream the released MR's filed-minutes pack as application/pdf (gate mgmtReview.read — the PDF
    shows only data the reader already sees). 409 ``pack_unavailable`` before release; 404 cross-org.
    Rendered on demand from the released version's frozen snapshot — no cache, no blob, no seal."""
    mr, doc = await _load_review(session, caller, review_id)
    if doc.current_effective_version_id is None:
        raise ProblemException(
            status=409, code="pack_unavailable", title="This review has not been released yet"
        )
    pdf = await build_minutes_pdf(session, mr, doc)
    filename = f"{doc.identifier}-minutes.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 3: Lint + typecheck + a routing smoke**

Run: `uv run ruff check apps/api/src/easysynq_api/api/mgmt_review.py && uv run mypy --strict apps/api/src/easysynq_api/api/mgmt_review.py`
Expected: clean.
Run: `uv run python -c "from easysynq_api.main import app; print([r.path for r in app.router.routes if getattr(r,'path','').endswith('/pack')])"` (from `apps/api`)
Expected: prints `['/api/v1/management-reviews/{review_id}/pack']` (the route is registered).

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/api/mgmt_review.py
git commit -m "feat(s-mr-pack): GET /management-reviews/{id}/pack — stream the minutes PDF

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: The OpenAPI contract

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Add the path entry** — insert immediately **before** `  /management-reviews/{review_id}:` (so it sits with the other sub-paths). Use this exact block (2-space indent under `paths:`):

```yaml
  /management-reviews/{review_id}/pack:
    get:
      tags: [management-reviews]
      operationId: getManagementReviewPack
      summary: "Download the filed minutes as a table-styled PDF (clause 9.3). Gated mgmtReview.read; released-only."
      description: >-
        Renders the released version's frozen minutes snapshot (attendees + 9.3.2 inputs + 9.3.3
        outputs + an e-signature sign-off block) on demand to a single application/pdf. No cache, no
        blob, no seal — a derived printable view of the canonical (already WORM-sealed) MR version.
        409 pack_unavailable before the review is released.
      parameters:
        - { name: review_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: The minutes pack (application/pdf).
          content:
            application/pdf:
              schema: { type: string, format: binary }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
        "409":
          description: "The review has not been released yet (pack_unavailable)."
          content:
            application/problem+json:
              schema: { $ref: "#/components/schemas/Problem" }
```

- [ ] **Step 2: Lint the contract**

Run (repo root): `npx --prefix . redocly lint packages/contracts/openapi.yaml` — or use the project skill `/check-contracts`.
Expected: no errors (warnings consistent with the rest of the file are fine).

- [ ] **Step 3: Commit**

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(contracts): document GET /management-reviews/{id}/pack

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: API integration test (CI-only on this box)

**Files:**
- Create: `apps/api/tests/integration/test_mgmt_review_pack.py`

> ⚠ This will NOT run locally on the Windows box (ProactorEventLoop). Write it failing-first **by reasoning** against the code paths above; CI verifies it. Model it on the existing MR integration tests — reuse their helpers for driving an MR create → compile → submit → approve → release (look at `apps/api/tests/integration/test_mgmt_review*.py` for the exact fixtures, the SoD-2 approver/releaser setup, and the `candidate_pool`-based decide helper). **Carry the S-dcr-ui-4 flake lesson:** the releaser needs `document.release` (grant `grant_lifecycle`, not only the `Approver` role) or the release 403s.

- [ ] **Step 1: Write the integration test**

```python
# apps/api/tests/integration/test_mgmt_review_pack.py
import pytest

pytestmark = pytest.mark.integration

# NOTE: reuse the MR end-to-end driver from the sibling MR integration tests (create → compile-inputs
# → add ACTION/DECISION outputs → submit-review → approve via /tasks decision → release). Name the
# helper to match whatever the existing files expose; the asserts below are the new coverage.


async def test_pack_streams_pdf_for_released_review(app_under_test, ...):
    review_id, identifier = await _drive_mr_to_effective(...)  # existing-style helper
    resp = await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(reader))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content[:4] == b"%PDF"
    assert f'{identifier}-minutes.pdf' in resp.headers["content-disposition"]


async def test_pack_409_before_release(app_under_test, ...):
    review_id = await _create_draft_mr(...)
    resp = await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(reader))
    assert resp.status_code == 409
    assert resp.json()["code"] == "pack_unavailable"


async def test_pack_404_cross_org(app_under_test, ...):
    review_id = await _drive_mr_to_effective(...)        # org A
    resp = await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(org_b_user))
    assert resp.status_code == 404


async def test_pack_403_without_read(app_under_test, ...):
    review_id = await _drive_mr_to_effective(...)
    resp = await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(no_read_user))
    assert resp.status_code == 403


async def test_pack_reflects_frozen_snapshot_not_live_rows(app_under_test, ...):
    """The pack reads the version snapshot, not the mutable review_input/review_output rows."""
    review_id, identifier = await _drive_mr_to_effective(..., with_output_desc="ORIGINAL ACTION")
    before = (await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(reader))).content
    # Mutate a live output row directly (bypassing the FSM) — the frozen snapshot must be unaffected.
    await _force_update_output_description(session, review_id, "TAMPERED")
    after = (await client.get(f"/api/v1/management-reviews/{review_id}/pack", headers=auth(reader))).content
    assert before == after            # determinism + frozen-source proof
    text = _pdf_text(after)           # pypdf extract helper (see Task 1)
    assert "ORIGINAL ACTION" in text and "TAMPERED" not in text
```

- [ ] **Step 2: Reason about failure** — without the endpoint/service these 404/AttributeError. With them, they should pass on CI. Fill in the `...` fixtures by copying the established MR integration harness (do not invent a new one).

- [ ] **Step 3: Lint**

Run: `uv run ruff check apps/api/tests/integration/test_mgmt_review_pack.py`
Expected: clean. (Cannot run `-m integration` locally — CI executes it.)

- [ ] **Step 4: Commit**

```bash
git add apps/api/tests/integration/test_mgmt_review_pack.py
git commit -m "test(s-mr-pack): integration — pack 200/409/404/403 + frozen-snapshot proof (CI-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: The frontend download affordance

**Files:**
- Modify: `apps/web/src/features/management-review/ManagementReviewDetailPage.tsx`
- Test: `apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx`

- [ ] **Step 1: Write the failing tests** — append to `ManagementReviewDetailPage.test.tsx` (mirror the existing render harness in that file: the same MSW server, the `renderWithProviders`/router wrapper, and a `satisfies MgmtReviewDetail` fixture). Stub objectURL like the `VisualDiffViewer.test.tsx` precedent.

```tsx
import { expect, it, describe, vi, beforeEach } from "vitest";
// ... reuse the file's existing imports/harness ...

describe("MR minutes pack download", () => {
  beforeEach(() => {
    // jsdom lacks these — mirror VisualDiffViewer.test.tsx
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("shows the download button when the review is released (Effective)", async () => {
    // fixture: current_state: "Effective"  (satisfies MgmtReviewDetail)
    renderDetail({ current_state: "Effective" });
    expect(await screen.findByRole("button", { name: /download minutes pack/i })).toBeInTheDocument();
  });

  it("hides the download button when not released", async () => {
    renderDetail({ current_state: "Draft" });
    await screen.findByText(/period/i); // page settled
    expect(screen.queryByRole("button", { name: /download minutes pack/i })).toBeNull();
  });

  it("downloads the pack on click", async () => {
    // MSW: GET /api/v1/management-reviews/:id/pack → 200 application/pdf blob
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderDetail({ current_state: "Effective" });
    const btn = await screen.findByRole("button", { name: /download minutes pack/i });
    await userEvent.click(btn);
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("shows a calm message on 409", async () => {
    // MSW: GET .../pack → 409 { code: "pack_unavailable", title: "..." }
    renderDetail({ current_state: "Effective" });
    await userEvent.click(await screen.findByRole("button", { name: /download minutes pack/i }));
    expect(await screen.findByText(/once the review is released/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `apps/web`): `npx vitest run src/features/management-review/ManagementReviewDetailPage.test.tsx`
Expected: FAIL — no such button.

- [ ] **Step 3: Implement** in `ManagementReviewDetailPage.tsx`

3a. Update the import to add `useApi`:
```tsx
import { ApiError, useApi } from "../../lib/api";
```

3b. Inside the component, after `const { can } = usePermissions();`, add:
```tsx
  const api = useApi();
  const [packLoading, setPackLoading] = useState(false);
  const [packError, setPackError] = useState<string | null>(null);
```

3c. After the `run(...)` helper, add the download handler (`mr` is in scope and non-null here):
```tsx
  async function downloadPack() {
    setPackError(null);
    setPackLoading(true);
    try {
      const blob = await api.getBlob(`/api/v1/management-reviews/${mr.id}/pack`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${mr.identifier}-minutes.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setPackError(
        e instanceof ApiError && e.status === 409
          ? "Available once the review is released."
          : "Couldn't generate the pack. Please retry.",
      );
    } finally {
      setPackLoading(false);
    }
  }
  const isReleased = mr.current_state === "Effective";
```

3d. Replace the header `<div>` (the block holding the identifier Group, Title, and the period Text) with a `Group justify="space-between"` so the button sits top-right:
```tsx
        <Group justify="space-between" align="flex-start">
          <div>
            <Group gap="xs" mb={4}>
              <Text c="dimmed" size="sm" fw={500}>
                {mr.identifier}
              </Text>
              <StateBadge state={mr.current_state} />
            </Group>
            <Title order={2}>{mr.title}</Title>
            <Text size="sm" c="dimmed">
              {mr.period_label ?? "—"}
              {mr.review_date ? ` · ${mr.review_date}` : ""}
              {mr.attendees?.length ? ` · ${mr.attendees.map((a) => a.name).join(", ")}` : ""}
            </Text>
          </div>
          {isReleased && (
            <Button
              variant="default"
              size="xs"
              loading={packLoading}
              onClick={() => void downloadPack()}
            >
              Download minutes pack (PDF)
            </Button>
          )}
        </Group>
        {packError && (
          <Alert color="red" withCloseButton onClose={() => setPackError(null)}>
            {packError}
          </Alert>
        )}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/features/management-review/ManagementReviewDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/management-review/ManagementReviewDetailPage.tsx apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx
git commit -m "feat(s-mr-pack): download-minutes-pack button on the MR detail page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (run before the PR)

- [ ] **API:** `/check-api` (ruff + format-check + mypy-strict + unit). Integration is CI-only here.
- [ ] **Contracts:** `/check-contracts` (redocly lint).
- [ ] **Web:** `/check-web` (eslint + strict tsc + build + the full vitest suite — the strict `tsc` catches the jest-dom×vitest `expect` import + array-index nits a per-file run misses).
- [ ] **No `/check-migrations`** — this slice adds no migration (head stays `0051`).
- [ ] **diff-critic** on the branch diff (`Agent`, `subagent_type: diff-critic`). **No migration-reviewer** (no migration).
- [ ] **web-test-trap-reviewer** on the `apps/web` diff.
- [ ] **Live smoke** via Chrome MCP (the owner logs in): the MR detail page is a **full route** (drivable, unlike the `/dcrs` drawer). Drive a released MR (reuse the S-mr-3 smoke artifact `MR-GEN-003` if present, or build one via service heredocs), click **Download minutes pack (PDF)**, confirm a PDF downloads with the five tables + the sign-off + the footer digest. Also confirm a Draft MR shows no button. Grant `mgmtReview.read` to the live `demo` app_user if needed (`scripts/grant-overrides.py`; revert before the PR).

## Self-review notes (already applied)

- **Spec coverage:** shape (sync printable) → Tasks 1–3,6; frozen-snapshot source → Task 2 + the Task 5 frozen-vs-live proof; direct stream → Task 3; released-only gate → Task 3 (409); `mgmtReview.read` → Task 3 (`_mr_read`); sign-off block → Tasks 1–2; plain footer + digest → Task 1; contract → Task 4; FE authed-binary button → Task 6; testing → Tasks 1,2,5,6.
- **Type consistency:** `render_minutes_pdf(**kwargs)` keys match between Task 1 (definition), the Task 1 test, and Task 2 (call site); `minutes_from_snapshot` signature matches Task 2 test + service; `list_signoffs_for_version` 4-tuple matches the Task 2 unpack.
- **No placeholders** except the deliberately-marked `...` fixtures in the CI-only integration test (Task 5), which must be filled from the existing MR integration harness (a real file the implementer reads), not invented.
