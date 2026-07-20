# S-report-doc-control — Controlled Document Register Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `GET /api/v1/reports/document-control` — the auditor-facing Controlled Document Register (ISO 9001 §7.5.3 master list) with a provenance header + content hash — plus a read-only SPA register page.

**Architecture:** A thin route in `api/reports.py` (the compliance-checklist precedent) delegates to a new `services/reports/document_control.py` service that: queries all org `kind=DOCUMENT` rows matching facet filters, per-row filters by `document.read` (the `list_documents` authz loop), batch-enriches the visible set (effective version, clause refs with ★, process links, approval signatures, owner/approver display, type name), and computes a deterministic content hash. Two-layer gate: `require("report.read")` SYSTEM surface gate + the per-row `document.read` filter. Full register (no pagination) so the content hash covers the complete as-of set. The SPA adds a `features/reports/` page reusing the shipped `RegisterToolbar`, calm states, and `Table.ScrollContainer` a11y patterns.

**Tech Stack:** FastAPI / Python 3.12 / SQLAlchemy async · React/TS + Mantine + TanStack Query + MSW/vitest/jest-axe.

## Global Constraints

- **No migration** — `report.read` is already seeded (`migrations/versions/0004_seed_authz.py`); head stays `0070`. No new permission key, no enum change, no DDL.
- **Read-only endpoint** — emits **no** `audit_event`, no WORM/append-only write, no blob mutation, no lifecycle transition. (Confirm no write path in review.)
- **Deny-by-default, two-layer** — surface gate `require("report.read")` at default SYSTEM scope; row filter `document.read` per row via `gather_grants` + `authorize`. Neither substitutes for the other.
- **Full register, no pagination** — the candidate scan covers ALL matching org DOCUMENT rows (no `_LIST_SCAN_CAP`); enrichment is batched (no N+1).
- **Content hash covers row DATA only**, rows sorted by `identifier`, canonical JSON (`sort_keys=True, separators=(",",":"), default=str`); the provenance block (with a wall-clock `generated_at`) is EXCLUDED from the hash input.
- **Contracts in-PR** — document the new path in `packages/contracts/openapi.yaml` (redocly-lint only, no codegen).
- **Verification** — `/check-api` (ruff + mypy-strict + pytest unit), `/check-web` (eslint + tsc + build + test), `/check-contracts` (redocly). Integration tests are CI-authoritative (4 shards); locally run scoped via `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v` (Docker available on this box).
- **Integration test discipline** — run-scoped / delta assertions only (shared session DB); do not assume a clean OR dirty DB; self-provide every precondition. Any `audit_event` write pins `occurred_at` to a seeded month (2026-06/07/08) — this endpoint writes none, but tests that seed docs via other services must respect it.
- **Web false-PASS discipline** — MSW fixtures pinned via `satisfies` to the real serializer shape; jest-dom tests `import { expect, it } from "vitest"`; distinct `aria-label`s; no `dangerouslySetInnerHTML`; RAG carried by shape/icon/label, never colour alone.

---

## File Structure

**API (create):**
- `apps/api/src/easysynq_api/services/reports/document_control.py` — the register service + pure helpers.
- `apps/api/tests/unit/test_document_control_report.py` — unit tests for the pure helpers.
- `apps/api/tests/integration/test_report_document_control.py` — gate + row-filter + hash integration tests.

**API (modify):**
- `apps/api/src/easysynq_api/api/reports.py` — add the `GET /reports/document-control` route.
- `packages/contracts/openapi.yaml` — document the new path.

**Web (create):**
- `apps/web/src/features/reports/useDocumentControlRegister.ts` — the query hook.
- `apps/web/src/features/reports/ReportsRegisterPage.tsx` — the page.
- `apps/web/src/features/reports/ReportsRegisterPage.test.tsx` — component + a11y tests.
- `apps/web/src/features/reports/useDocumentControlRegister.test.tsx` — hook tests.

**Web (modify):**
- `apps/web/src/lib/types.ts` — the register response types.
- `apps/web/src/App.tsx` — the route.
- `apps/web/src/app/shell/LeftRail.tsx` — the nav entry (gated `report.read`).
- `apps/web/src/lib/routeChrome.ts` — the tab-title mapping.
- `apps/web/src/app/shell/LeftRail.test.tsx` — nav gating assertion.
- `apps/web/src/test/handlers.ts` (or the shared MSW handler file) — a base handler for the new endpoint.

---

## Task 1: Pure content-hash + provenance helpers

**Files:**
- Create: `apps/api/src/easysynq_api/services/reports/document_control.py` (helpers only this task)
- Test: `apps/api/tests/unit/test_document_control_report.py`

**Interfaces:**
- Produces:
  - `register_content_hash(rows: list[dict[str, Any]]) -> str` — returns `"sha256:<hex>"` over the rows sorted by `identifier`, canonical-JSON serialized. Deterministic (input-order-independent), filter-sensitive.
  - `build_provenance(*, generated_by: str, generated_at: datetime, scope: str, app_version: str, filters: dict[str, str], row_count: int, content_hash: str) -> dict[str, Any]` — the provenance block dict (see shape below). `as_of == generated_at.isoformat()`.

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/unit/test_document_control_report.py
from __future__ import annotations

import datetime

from easysynq_api.services.reports.document_control import (
    build_provenance,
    register_content_hash,
)


def _rows() -> list[dict]:
    return [
        {"identifier": "SOP-QA-002", "title": "B", "current_state": "Effective"},
        {"identifier": "SOP-QA-001", "title": "A", "current_state": "Effective"},
    ]


def test_content_hash_is_deterministic_and_order_independent():
    a = register_content_hash(_rows())
    b = register_content_hash(list(reversed(_rows())))
    assert a == b
    assert a.startswith("sha256:")


def test_content_hash_is_filter_sensitive():
    base = register_content_hash(_rows())
    fewer = register_content_hash(_rows()[:1])
    assert base != fewer


def test_content_hash_reacts_to_a_field_change():
    rows = _rows()
    changed = [dict(rows[0], title="CHANGED"), rows[1]]
    assert register_content_hash(rows) != register_content_hash(changed)


def test_build_provenance_shape_excludes_hash_from_its_own_input():
    now = datetime.datetime(2026, 7, 19, 12, 0, tzinfo=datetime.UTC)
    prov = build_provenance(
        generated_by="Mara Quality",
        generated_at=now,
        scope="org:DEFAULT",
        app_version="0.1.0",
        filters={"filter[current_state][eq]": "Effective"},
        row_count=2,
        content_hash="sha256:abc",
    )
    assert prov["report_name"] == "Controlled Document Register"
    assert prov["generated_at"] == now.isoformat()
    assert prov["as_of"] == now.isoformat()
    assert prov["scope"] == "org:DEFAULT"
    assert prov["app_version"] == "0.1.0"
    assert prov["row_count"] == 2
    assert prov["content_hash"] == "sha256:abc"
    assert prov["filters"] == {"filter[current_state][eq]": "Effective"}
    assert prov["generated_by"] == "Mara Quality"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/unit/test_document_control_report.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError: cannot import name 'register_content_hash'`.

- [ ] **Step 3: Write the module + helpers**

```python
# apps/api/src/easysynq_api/services/reports/document_control.py
"""The Controlled Document Register report (ISO 9001 §7.5.3 master list; doc 13 §6.1, doc 15 §8.15).

``GET /reports/document-control`` (api/reports.py) returns the org's master list of controlled
Documents — permission-filtered by ``document.read`` (the ``list_documents`` row-filter), with an
audit-defensible provenance header + a content hash over the full as-of set. Read-only: NO
audit_event, NO WORM write, NO migration. The pure helpers (hash + provenance) are DB-free and
unit-tested; ``compute_document_control_register`` does the query + authz filter + batched enrichment.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

_REPORT_NAME = "Controlled Document Register"


def register_content_hash(rows: list[dict[str, Any]]) -> str:
    """A deterministic sha256 over the register's ROW DATA (not the provenance block, whose
    wall-clock ``generated_at`` would make every hash unique). Rows are sorted by ``identifier`` and
    canonically serialized so the hash is independent of DB return order and reproducible given the
    same filtered set + as-of. Filter-sensitive: a different row set → a different hash."""
    ordered = sorted(rows, key=lambda r: str(r.get("identifier") or ""))
    canonical = json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provenance(
    *,
    generated_by: str,
    generated_at: datetime.datetime,
    scope: str,
    app_version: str,
    filters: dict[str, str],
    row_count: int,
    content_hash: str,
) -> dict[str, Any]:
    """The audit-defensibility header block (doc 13 §6). ``as_of`` mirrors ``generated_at`` (the
    instant the register was materialized). ``filters`` echoes the applied ``filter[...]`` params so
    the content hash is reproducible."""
    stamp = generated_at.isoformat()
    return {
        "report_name": _REPORT_NAME,
        "generated_by": generated_by,
        "generated_at": stamp,
        "as_of": stamp,
        "scope": scope,
        "app_version": app_version,
        "filters": filters,
        "row_count": row_count,
        "content_hash": content_hash,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/test_document_control_report.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + type-check the new module**

Run: `cd apps/api && uv run ruff check src/easysynq_api/services/reports/document_control.py && uv run ruff format --check src/easysynq_api/services/reports/document_control.py && uv run mypy src/easysynq_api/services/reports/document_control.py`
Expected: clean (mypy may warn only about unused imports if any — fix inline).

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/reports/document_control.py apps/api/tests/unit/test_document_control_report.py
git commit -m "feat(reports): pure content-hash + provenance helpers for the document-control register"
```

---

## Task 2: The register service (query + authz filter + batched enrichment)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/reports/document_control.py` (add the service + `RegisterResult`)
- Test: `apps/api/tests/integration/test_report_document_control.py` (the row-filter + hash-covers-full-set tests)

**Interfaces:**
- Consumes: `register_content_hash` (Task 1); `gather_grants` (`services.authz`); `authorize`, `RequestContext`, `ResourceContext` (`domain.authz`); `vault_repo.process_ids_for_docs` (`services.vault.repository`).
- Produces:
  - `@dataclass(frozen=True) class RegisterResult: rows: list[dict[str, Any]]; content_hash: str; row_count: int`
  - `async def compute_document_control_register(session: AsyncSession, caller: AppUser, *, filters: list[ColumnElement[bool]], source_ip: str | None) -> RegisterResult`

**Row shape produced** (each dict in `RegisterResult.rows`):
```
id, identifier, title, document_type_id, document_type, current_state,
owner_user_id, owner_display, effective_revision_label, effective_from,
blob_sha256, clause_refs (list[{clause, starred}]), process_links (list[str]),
approved_by, approved_on, next_review_due, review_state
```

- [ ] **Step 1: Write the failing integration test**

```python
# apps/api/tests/integration/test_report_document_control.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_register_includes_a_new_effective_document_and_hash_changes(
    app_under_test: object,
) -> None:
    """The full register is complete (not paginated): a newly-Effective doc appears, and the content
    hash reacts to the larger set. Run-scoped: we assert OUR doc is present + the hash differs before
    vs after, never an absolute count on the shared DB."""
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.reports.document_control import (
        compute_document_control_register,
    )
    # Helpers below are the existing integration fixtures for seeding an org + an Effective document.
    from tests.integration._vault_helpers import (  # adjust import to the repo's actual helper module
        seed_admin_user,
        seed_effective_document,
    )

    sm = get_sessionmaker()
    async with sm() as session:
        caller = await seed_admin_user(session)  # a SYSTEM document.read holder
        before = await compute_document_control_register(
            session, caller, filters=[], source_ip=None
        )
        doc = await seed_effective_document(session, org_id=caller.org_id, identifier="SOP-REG-777")
        await session.commit()

    async with sm() as session:
        caller = await seed_admin_user(session)
        after = await compute_document_control_register(
            session, caller, filters=[], source_ip=None
        )

    ids = {r["identifier"] for r in after.rows}
    assert "SOP-REG-777" in ids
    assert after.row_count == len(after.rows)
    assert after.content_hash != before.content_hash
    row = next(r for r in after.rows if r["identifier"] == "SOP-REG-777")
    assert row["current_state"] == "Effective"
    assert row["effective_revision_label"]  # a released doc has a revision label
    assert isinstance(row["clause_refs"], list)
    assert isinstance(row["process_links"], list)
```

> **Note to implementer:** the exact seeding-helper import path depends on what the integration suite already provides. Grep `tests/integration/` for an existing helper that creates an Effective document (e.g. the vault/release tests). If none is reusable as a bare function, inline the minimal seed using the vault service the release tests use, following the run-scoped discipline. Do NOT create a second `organization` (the REVOKE-DELETE / `test_restore` `scalar_one` trap) — reuse the default org or a throwaway with rollback.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v -m integration`
Expected: FAIL — `ImportError: cannot import name 'compute_document_control_register'`.

- [ ] **Step 3: Implement the service**

Append to `apps/api/src/easysynq_api/services/reports/document_control.py`:

```python
# --- add these imports at the top of the module ---
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ...db.models._signature_enums import SignatureMeaning, SignedObjectType
from ...db.models._vault_enums import DocumentKind
from ...db.models.app_user import AppUser
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.signature_event import SignatureEvent
from ...domain.authz import RequestContext, ResourceContext, authorize
from ..authz import gather_grants
from ..vault import repository as vault_repo
from ..vault.review import review_state, today_org
```

```python
@dataclass(frozen=True)
class RegisterResult:
    rows: list[dict[str, Any]]
    content_hash: str
    row_count: int


def _display(user: AppUser | None) -> str | None:
    if user is None:
        return None
    return user.display_name or user.email or str(user.id)


async def compute_document_control_register(
    session: AsyncSession,
    caller: AppUser,
    *,
    filters: list[ColumnElement[bool]],
    source_ip: str | None,
) -> RegisterResult:
    """The permission-filtered master list. Scans ALL org DOCUMENT rows matching ``filters`` (no
    cap — the register is complete), row-filters by ``document.read`` (the ``list_documents`` loop),
    then batch-enriches the visible set. No N+1; no audit_event."""
    docs = (
        (
            await session.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.org_id == caller.org_id,
                    DocumentedInformation.kind == DocumentKind.DOCUMENT,
                    *filters,
                )
                # deterministic candidate order; the final rows re-sort by identifier in the hash.
                .order_by(DocumentedInformation.identifier)
            )
        )
        .scalars()
        .all()
    )

    # document_level per doc-type (needed for the document.read ResourceContext) — the list_documents
    # ``levels`` map.
    type_ids = {d.document_type_id for d in docs if d.document_type_id}
    type_level: dict[uuid.UUID, str] = {}
    type_name: dict[uuid.UUID, str] = {}
    if type_ids:
        for dt in (
            (await session.execute(select(DocumentType).where(DocumentType.id.in_(type_ids))))
            .scalars()
            .all()
        ):
            type_level[dt.id] = dt.document_level.value
            type_name[dt.id] = dt.name

    process_ids_by_doc = await vault_repo.process_ids_for_docs(session, [d.id for d in docs])

    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC), source_ip=source_ip)
    visible: list[DocumentedInformation] = []
    for d in docs:
        resource = ResourceContext(
            artifact_id=str(d.id),
            folder_path=d.folder_path,
            document_level=type_level.get(d.document_type_id) if d.document_type_id else None,
            process_ids=process_ids_by_doc.get(d.id, frozenset()),
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            visible.append(d)

    # --- batched enrichment over the visible set only ---
    eff_ids = [d.current_effective_version_id for d in visible if d.current_effective_version_id]
    versions: dict[uuid.UUID, DocumentVersion] = {}
    if eff_ids:
        for v in (
            (await session.execute(select(DocumentVersion).where(DocumentVersion.id.in_(eff_ids))))
            .scalars()
            .all()
        ):
            versions[v.id] = v

    # clause refs WITH the ★ mandatory flag (clause.is_mandatory_star) — the register's own loader
    # (vault_repo.clause_numbers_for_docs returns numbers only, no star).
    clause_by_doc: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if visible:
        for doc_id, number, starred in (
            await session.execute(
                select(
                    ClauseMapping.documented_information_id,
                    Clause.number,
                    Clause.is_mandatory_star,
                )
                .join(Clause, ClauseMapping.clause_id == Clause.id)
                .where(ClauseMapping.documented_information_id.in_([d.id for d in visible]))
                .order_by(Clause.number)
            )
        ).all():
            clause_by_doc.setdefault(doc_id, []).append(
                {"clause": number, "starred": bool(starred)}
            )

    # approval/release signature on the effective version → approver + date (latest wins).
    approval_by_version: dict[uuid.UUID, SignatureEvent] = {}
    if eff_ids:
        for sig in (
            (
                await session.execute(
                    select(SignatureEvent)
                    .where(
                        SignatureEvent.signed_object_type == SignedObjectType.document_version,
                        SignatureEvent.signed_object_id.in_(eff_ids),
                        SignatureEvent.meaning.in_(
                            [SignatureMeaning.approval, SignatureMeaning.release]
                        ),
                    )
                    .order_by(SignatureEvent.created_at)
                )
            )
            .scalars()
            .all()
        ):
            approval_by_version[sig.signed_object_id] = sig  # last (latest) wins

    # display names for owners ∪ signers.
    user_ids: set[uuid.UUID] = {d.owner_user_id for d in visible}
    user_ids |= {s.signer_user_id for s in approval_by_version.values() if s.signer_user_id}
    users: dict[uuid.UUID, AppUser] = {}
    if user_ids:
        for u in (
            (await session.execute(select(AppUser).where(AppUser.id.in_(user_ids))))
            .scalars()
            .all()
        ):
            users[u.id] = u

    today = today_org()
    rows: list[dict[str, Any]] = []
    for d in visible:
        ev = versions.get(d.current_effective_version_id) if d.current_effective_version_id else None
        sig = (
            approval_by_version.get(d.current_effective_version_id)
            if d.current_effective_version_id
            else None
        )
        rows.append(
            {
                "id": str(d.id),
                "identifier": d.identifier,
                "title": d.title,
                "document_type_id": str(d.document_type_id) if d.document_type_id else None,
                "document_type": type_name.get(d.document_type_id) if d.document_type_id else None,
                "current_state": d.current_state.value,
                "owner_user_id": str(d.owner_user_id),
                "owner_display": _display(users.get(d.owner_user_id)),
                "effective_revision_label": ev.revision_label if ev else None,
                "effective_from": ev.effective_from.isoformat() if ev and ev.effective_from else None,
                "blob_sha256": ev.source_blob_sha256 if ev else None,
                "clause_refs": clause_by_doc.get(d.id, []),
                "process_links": sorted(process_ids_by_doc.get(d.id, frozenset())),
                "approved_by": _display(users.get(sig.signer_user_id)) if sig and sig.signer_user_id else None,
                "approved_on": sig.created_at.isoformat() if sig else None,
                "next_review_due": d.next_review_due.isoformat() if d.next_review_due else None,
                "review_state": review_state(d.next_review_due, today),
            }
        )

    content_hash = register_content_hash(rows)
    return RegisterResult(rows=rows, content_hash=content_hash, row_count=len(rows))
```

> **Implementer checks:** confirm `review_state`/`today_org` import from `..vault.review` matches the callable signature used in `api/documents.py:97` (`review_state(next_review_due, today_org())`). Confirm `AppUser` has `email` (it does: `app_user.py:54`). If `mypy` flags `d.current_effective_version_id` nullability on the dict key lookups, keep the `if ... else None` guards shown.

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v -m integration`
Expected: PASS. (If the seeding-helper import needed adjusting, fix it until green.)

- [ ] **Step 5: Lint + type-check**

Run: `cd apps/api && uv run ruff check src/easysynq_api/services/reports/document_control.py && uv run ruff format --check src/easysynq_api/services/reports/document_control.py && uv run mypy src/easysynq_api/services/reports/document_control.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/reports/document_control.py apps/api/tests/integration/test_report_document_control.py
git commit -m "feat(reports): document-control register service — authz-filtered master list + batched enrichment"
```

---

## Task 3: The route + two-layer gate + contracts

**Files:**
- Modify: `apps/api/src/easysynq_api/api/reports.py` (add the route)
- Modify: `packages/contracts/openapi.yaml` (document the path)
- Test: `apps/api/tests/integration/test_report_document_control.py` (add the HTTP gate tests)

**Interfaces:**
- Consumes: `compute_document_control_register`, `build_provenance` (Tasks 1–2); `require` (`services.authz`); `_parse_document_filters` (`api.documents`); `get_settings` (`config`).
- Produces: `GET /api/v1/reports/document-control` → `{ "provenance": {...}, "rows": [...] }`.

- [ ] **Step 1: Write the failing HTTP tests**

```python
# append to apps/api/tests/integration/test_report_document_control.py

async def test_endpoint_403s_without_report_read(client_as_employee) -> None:
    """The surface gate: a caller with no SYSTEM report.read grant is refused before any query.
    ``client_as_employee`` is an authenticated client whose principal holds document.read but NOT
    report.read (seed accordingly — an Employee-role user)."""
    resp = await client_as_employee.get("/api/v1/reports/document-control")
    assert resp.status_code == 403


async def test_endpoint_returns_register_with_provenance(client_as_report_reader) -> None:
    """A SYSTEM report.read holder gets the register + a provenance header whose content_hash matches
    a recompute over the returned rows."""
    from easysynq_api.services.reports.document_control import register_content_hash

    resp = await client_as_report_reader.get("/api/v1/reports/document-control")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"provenance", "rows"}
    prov = body["provenance"]
    assert prov["report_name"] == "Controlled Document Register"
    assert prov["row_count"] == len(body["rows"])
    assert prov["content_hash"] == register_content_hash(body["rows"])
    assert prov["scope"].startswith("org:")
    assert prov["app_version"]
```

> **Implementer note:** reuse the integration suite's existing authenticated-client fixtures. Grep `tests/integration/conftest.py` for how other gated endpoints build a client for a given role/permission set (e.g. an admin client with SYSTEM overrides vs an employee client). If a `report.read` SYSTEM holder fixture doesn't exist, build one by granting the SYSTEM `report.read` override to the test user (the grant-override precedent used across the integration suite). Match the existing fixture naming; the names above are illustrative.

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v -m integration`
Expected: the two new tests FAIL (404 — route not mounted yet).

- [ ] **Step 3: Add the route**

Modify `apps/api/src/easysynq_api/api/reports.py` — add imports + the endpoint:

```python
# --- add to the imports block ---
from fastapi import Request

from ..config import get_settings
from ..services.common.org_clock import current_org_tz
from ..services.reports.document_control import (
    build_provenance,
    compute_document_control_register,
)
from .documents import _parse_document_filters

# --- add near _checklist_read ---
# report.read is PROCESS-finest but the register is an org-level surface → gate at the default SYSTEM
# scope (the checklist require(...) precedent). Rows are then filtered per-row by document.read inside
# the service (doc 13 §6.1 "all Documents the requester may see"). A guest (ARTIFACT report.read) or
# an Employee (no report.read) is refused here.
_report_read = require("report.read")


@router.get("/reports/document-control")
async def document_control_register_endpoint(
    request: Request,
    caller: AppUser = Depends(_report_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The Controlled Document Register (ISO 9001 §7.5.3 master list) — a provenance-stamped, content-
    hashed master list of every controlled Document the caller may read. Full set (no pagination);
    facet filters via the shared ``filter[field][op]`` grammar. Read-only (no audit_event)."""
    filters = _parse_document_filters(request)
    source_ip = request.client.host if request.client else None
    result = await compute_document_control_register(
        session, caller, filters=filters, source_ip=source_ip
    )
    # echo the applied filter[...] params (for hash reproducibility) — only the filter[...] keys.
    applied = {k: v for k, v in request.query_params.items() if k.startswith("filter[")}
    org = await session.get(type(caller).__mro__[0], caller.org_id)  # replaced below — see note
    generated_at = datetime.datetime.now(current_org_tz())
    provenance = build_provenance(
        generated_by=(caller.display_name or caller.email or str(caller.id)),
        generated_at=generated_at,
        scope=f"org:{await _org_short_code(session, caller.org_id)}",
        app_version=get_settings().version,
        filters=applied,
        row_count=result.row_count,
        content_hash=result.content_hash,
    )
    return {"provenance": provenance, "rows": result.rows}
```

> The `org = await session.get(...)` line above is a placeholder — **delete it**. Instead add a tiny helper in `api/reports.py` to resolve the org short_code:

```python
from ..db.models.organization import Organization  # add to imports


async def _org_short_code(session: AsyncSession, org_id: uuid.UUID) -> str:
    org = await session.get(Organization, org_id)
    return org.short_code if org else str(org_id)
```

Also add `import datetime` and `import uuid` to `api/reports.py` if not present.

> **Filter-echo consistency:** the `applied` dict is built from `request.query_params.items()` (only `filter[...]` keys). `_parse_document_filters` will 400 on an unknown `filter[...]` before the echo runs, so the echo only ever reflects accepted filters.

- [ ] **Step 4: Run the HTTP tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v -m integration`
Expected: all PASS.

- [ ] **Step 5: Document the endpoint in the OpenAPI contract**

Add to `packages/contracts/openapi.yaml` under the existing `reports` tag a `GET /reports/document-control` path. Follow the shape of the existing `/reports/compliance-checklist` path entry (grep it for the exact style). Include: summary ("Controlled Document Register — the ISO 9001 §7.5.3 master list. Needs report.read (SYSTEM); rows filtered per-row by document.read."), the `filter[field][op]` query param note, and a 200 response schema with `provenance` (object: report_name, generated_by, generated_at, as_of, scope, app_version, filters, row_count, content_hash) + `rows` (array of the register row object). 403 for a caller lacking report.read.

- [ ] **Step 6: Run the API + contracts checks**

Run: `cd apps/api && uv run ruff check src/easysynq_api/api/reports.py && uv run mypy src/easysynq_api/api/reports.py`
Run: from repo root, `npx --prefix . redocly lint packages/contracts/openapi.yaml` (or invoke the `/check-contracts` skill).
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/api/reports.py packages/contracts/openapi.yaml apps/api/tests/integration/test_report_document_control.py
git commit -m "feat(reports): GET /reports/document-control route (report.read gate) + openapi"
```

---

## Task 4: Web — types + query hook + MSW handler

**Files:**
- Modify: `apps/web/src/lib/types.ts` (add the register types)
- Create: `apps/web/src/features/reports/useDocumentControlRegister.ts`
- Create: `apps/web/src/features/reports/useDocumentControlRegister.test.tsx`
- Modify: the shared MSW handler file (grep `apps/web/src/test/` for where base handlers live; add a base handler for the new endpoint).

**Interfaces:**
- Produces:
  - `types.ts`: `DocumentControlRegister`, `RegisterProvenance`, `RegisterRow` (a `clause_refs` element type `ClauseRef`).
  - `useDocumentControlRegister(): UseQueryResult<DocumentControlRegister> & { forbidden: boolean }`.

- [ ] **Step 1: Add the types**

Add to `apps/web/src/lib/types.ts`:

```ts
export interface ClauseRef {
  clause: string;
  starred: boolean;
}

export interface RegisterRow {
  id: string;
  identifier: string;
  title: string;
  document_type_id: string | null;
  document_type: string | null;
  current_state: string;
  owner_user_id: string;
  owner_display: string | null;
  effective_revision_label: string | null;
  effective_from: string | null;
  blob_sha256: string | null;
  clause_refs: ClauseRef[];
  process_links: string[];
  approved_by: string | null;
  approved_on: string | null;
  next_review_due: string | null;
  review_state: string | null;
}

export interface RegisterProvenance {
  report_name: string;
  generated_by: string;
  generated_at: string;
  as_of: string;
  scope: string;
  app_version: string;
  filters: Record<string, string>;
  row_count: number;
  content_hash: string;
}

export interface DocumentControlRegister {
  provenance: RegisterProvenance;
  rows: RegisterRow[];
}
```

- [ ] **Step 2: Write the failing hook test**

```tsx
// apps/web/src/features/reports/useDocumentControlRegister.test.tsx
import { expect, it, describe } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { server } from "../../test/server";
import { withProviders } from "../../test/withProviders"; // adjust to the repo's query-client test wrapper
import { useDocumentControlRegister } from "./useDocumentControlRegister";
import type { DocumentControlRegister } from "../../lib/types";

const SAMPLE: DocumentControlRegister = {
  provenance: {
    report_name: "Controlled Document Register",
    generated_by: "Mara",
    generated_at: "2026-07-19T12:00:00+00:00",
    as_of: "2026-07-19T12:00:00+00:00",
    scope: "org:DEFAULT",
    app_version: "0.1.0",
    filters: {},
    row_count: 1,
    content_hash: "sha256:abc",
  },
  rows: [
    {
      id: "1", identifier: "SOP-QA-001", title: "Doc Control", document_type_id: null,
      document_type: "SOP", current_state: "Effective", owner_user_id: "u1", owner_display: "Priya",
      effective_revision_label: "Rev A", effective_from: "2026-06-01T00:00:00+00:00",
      blob_sha256: "deadbeef", clause_refs: [{ clause: "7.5.3", starred: true }],
      process_links: [], approved_by: "Ken", approved_on: "2026-06-01T00:00:00+00:00",
      next_review_due: null, review_state: null,
    },
  ],
} satisfies DocumentControlRegister;

describe("useDocumentControlRegister", () => {
  it("returns the register on 200", async () => {
    server.use(
      http.get("*/api/v1/reports/document-control", () => HttpResponse.json(SAMPLE)),
    );
    const { result } = renderHook(() => useDocumentControlRegister(), { wrapper: withProviders });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.rows[0].identifier).toBe("SOP-QA-001");
    expect(result.current.forbidden).toBe(false);
  });

  it("sets forbidden on 403", async () => {
    server.use(
      http.get("*/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
    );
    const { result } = renderHook(() => useDocumentControlRegister(), { wrapper: withProviders });
    await waitFor(() => expect(result.current.forbidden).toBe(true));
  });
});
```

> **Implementer note:** match the repo's existing hook-test harness (grep `useComplianceChecklist.test.tsx` for the exact `server`/wrapper imports — reuse them verbatim; the `withProviders`/`server` paths above are illustrative).

- [ ] **Step 3: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/reports/useDocumentControlRegister.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the hook**

```ts
// apps/web/src/features/reports/useDocumentControlRegister.ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DocumentControlRegister } from "../../lib/types";

// GET /reports/document-control is hard-gated (report.read SYSTEM). A 403 is a first-class non-error
// outcome (the caller may lack the key) → surface `forbidden` for a calm no-access panel. retry:false
// (don't hammer a permission denial). The mirror of useComplianceChecklist.
export function useDocumentControlRegister() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["document-control-register"],
    queryFn: () =>
      api.get<DocumentControlRegister>("/api/v1/reports/document-control"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

- [ ] **Step 5: Add a base MSW handler**

Add to the shared base handlers file (the one that registers the other `/reports/*` and `/documents` base handlers under `onUnhandledRequest: "error"`) a default handler returning an empty register, so any page mounting the hook doesn't error:

```ts
http.get("*/api/v1/reports/document-control", () =>
  HttpResponse.json({
    provenance: {
      report_name: "Controlled Document Register", generated_by: "test",
      generated_at: "2026-07-19T12:00:00+00:00", as_of: "2026-07-19T12:00:00+00:00",
      scope: "org:DEFAULT", app_version: "0.1.0", filters: {}, row_count: 0, content_hash: "sha256:0",
    },
    rows: [],
  }),
),
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/reports/useDocumentControlRegister.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/reports/useDocumentControlRegister.ts apps/web/src/features/reports/useDocumentControlRegister.test.tsx apps/web/src/test/
git commit -m "feat(web): document-control register types + query hook + MSW base handler"
```

---

## Task 5: Web — the ReportsRegisterPage (provenance banner + register table)

**Files:**
- Create: `apps/web/src/features/reports/ReportsRegisterPage.tsx`
- Create: `apps/web/src/features/reports/ReportsRegisterPage.test.tsx`

**Interfaces:**
- Consumes: `useDocumentControlRegister` (Task 4); `RegisterToolbar`/`SortableTh` (`lib/RegisterToolbar`); `LoadingState`/`ErrorState`/`NoAccessState`/`EmptyState` (`lib/states`); `useDebouncedSearch`/`useTableSort`/`sortRows` (`lib/registerControls`); `AsOf` (`lib/AsOf`).
- Produces: `export function ReportsRegisterPage()`.

- [ ] **Step 1: Write the failing component tests**

```tsx
// apps/web/src/features/reports/ReportsRegisterPage.test.tsx
import { expect, it, describe } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { server } from "../../test/server";
import { withProviders } from "../../test/withProviders"; // reuse the repo's page-test wrapper (router+query)
import { ReportsRegisterPage } from "./ReportsRegisterPage";
import type { DocumentControlRegister } from "../../lib/types";

const REG: DocumentControlRegister = {
  provenance: {
    report_name: "Controlled Document Register", generated_by: "Mara",
    generated_at: "2026-07-19T12:00:00+00:00", as_of: "2026-07-19T12:00:00+00:00",
    scope: "org:DEFAULT", app_version: "0.1.0", filters: {}, row_count: 1,
    content_hash: "sha256:abc123",
  },
  rows: [
    {
      id: "1", identifier: "SOP-QA-001", title: "Document Control", document_type_id: null,
      document_type: "SOP", current_state: "Effective", owner_user_id: "u1", owner_display: "Priya",
      effective_revision_label: "Rev A", effective_from: "2026-06-01T00:00:00+00:00",
      blob_sha256: "deadbeefcafef00d", clause_refs: [{ clause: "7.5.3", starred: true }],
      process_links: [], approved_by: "Ken", approved_on: "2026-06-01T00:00:00+00:00",
      next_review_due: "2027-06-01", review_state: "OK",
    },
  ],
} satisfies DocumentControlRegister;

describe("ReportsRegisterPage", () => {
  it("renders the provenance banner + a register row", async () => {
    server.use(http.get("*/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    render(<ReportsRegisterPage />, { wrapper: withProviders });
    expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();
    expect(screen.getByText(/Controlled Document Register/)).toBeInTheDocument();
    expect(screen.getByText(/sha256:abc123/)).toBeInTheDocument();
    expect(screen.getByText("Rev A")).toBeInTheDocument();
    expect(screen.getByText("7.5.3")).toBeInTheDocument();
  });

  it("shows a calm no-access panel on 403", async () => {
    server.use(
      http.get("*/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
    );
    render(<ReportsRegisterPage />, { wrapper: withProviders });
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    server.use(http.get("*/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    const { container } = render(<ReportsRegisterPage />, { wrapper: withProviders });
    await screen.findByText("SOP-QA-001");
    expect(await axe(container)).toHaveNoViolations();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/reports/ReportsRegisterPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the page**

```tsx
// apps/web/src/features/reports/ReportsRegisterPage.tsx
import { Card, Container, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { useDocumentControlRegister } from "./useDocumentControlRegister";
import type { RegisterRow } from "../../lib/types";
import { ErrorState, LoadingState, NoAccessState, EmptyState } from "../../lib/states";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { sortRows, useDebouncedSearch, useTableSort } from "../../lib/registerControls";
import { StateBadge } from "../document/StateBadge";

const SORT_KEYS = ["identifier", "title", "type", "state", "review"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function sortValue(r: RegisterRow, key: SortKey): string | number | null {
  switch (key) {
    case "identifier":
      return r.identifier;
    case "title":
      return r.title;
    case "type":
      return r.document_type ?? "";
    case "state":
      return r.current_state;
    case "review":
      return r.next_review_due;
  }
}

// The Controlled Document Register report (ISO 9001 §7.5.3 master list). Read-only, auditor-facing:
// a provenance banner (defensibility header + content hash) over a filterable/sortable master list.
// Reuses the shared register primitives (RegisterToolbar/SortableTh/registerControls) + the calm
// states. RAG next-review is carried by label + StateBadge shape, never colour alone.
export function ReportsRegisterPage() {
  const { data, isLoading, isError, forbidden, refetch } = useDocumentControlRegister();
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const matched = query
      ? all.filter((r) =>
          [r.identifier, r.title, r.document_type ?? ""].some((v) =>
            v.toLowerCase().includes(query),
          ),
        )
      : all;
    return sortRows(matched, sort, dir, (r) => sortValue(r, sort));
  }, [data, query, sort, dir]);

  return (
    <Container size="xl" py="md">
      <Stack gap="md">
        <Title order={1}>Controlled Document Register</Title>
        {forbidden ? (
          <NoAccessState message="You need the report.read permission to view the Controlled Document Register." />
        ) : isLoading ? (
          <LoadingState label="Loading the register" />
        ) : isError ? (
          <ErrorState title="Couldn't load the register" onRetry={() => refetch()} />
        ) : (
          <>
            {data && <ProvenanceBanner provenance={data.provenance} />}
            <RegisterToolbar
              q={q}
              onQ={setQ}
              placeholder="Search identifier / title / type…"
              count={rows.length}
              countNoun="documents"
            />
            {rows.length === 0 ? (
              <EmptyState message="No controlled documents match." />
            ) : (
              <Table.ScrollContainer minWidth={900}>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <SortableTh label="Identifier" sortKey="identifier" sort={sort} dir={dir} onSort={toggleSort} scope="col" />
                      <SortableTh label="Title" sortKey="title" sort={sort} dir={dir} onSort={toggleSort} scope="col" />
                      <SortableTh label="Type" sortKey="type" sort={sort} dir={dir} onSort={toggleSort} scope="col" />
                      <Table.Th scope="col">Rev</Table.Th>
                      <SortableTh label="State" sortKey="state" sort={sort} dir={dir} onSort={toggleSort} scope="col" />
                      <Table.Th scope="col">Owner</Table.Th>
                      <Table.Th scope="col">Clauses</Table.Th>
                      <SortableTh label="Next review" sortKey="review" sort={sort} dir={dir} onSort={toggleSort} scope="col" />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.map((r) => (
                      <Table.Tr key={r.id}>
                        <Table.Td>{r.identifier}</Table.Td>
                        <Table.Td>{r.title}</Table.Td>
                        <Table.Td>{r.document_type ?? "—"}</Table.Td>
                        <Table.Td>{r.effective_revision_label ?? "—"}</Table.Td>
                        <Table.Td>
                          <StateBadge state={r.current_state} />
                        </Table.Td>
                        <Table.Td>{r.owner_display ?? "—"}</Table.Td>
                        <Table.Td>
                          <Group gap={4}>
                            {r.clause_refs.length === 0
                              ? "—"
                              : r.clause_refs.map((c) => (
                                  <Text key={c.clause} size="sm">
                                    {c.starred ? "★ " : ""}
                                    {c.clause}
                                  </Text>
                                ))}
                          </Group>
                        </Table.Td>
                        <Table.Td>{r.next_review_due ?? "—"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </>
        )}
      </Stack>
    </Container>
  );
}

function ProvenanceBanner({ provenance }: { provenance: import("../../lib/types").RegisterProvenance }) {
  const p = provenance;
  return (
    <Card withBorder padding="sm">
      <Stack gap={4}>
        <Text fw={600}>{p.report_name}</Text>
        <Text size="sm" c="dimmed">
          Generated by {p.generated_by} · {new Date(p.generated_at).toLocaleString()} · {p.scope} ·
          EasySynQ {p.app_version} · {p.row_count} documents
        </Text>
        <Text size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
          {p.content_hash}
        </Text>
      </Stack>
    </Card>
  );
}
```

> **Implementer checks:** confirm `StateBadge`'s prop name/shape by reading `apps/web/src/features/document/StateBadge.tsx` (the register page reuses it — it carries lifecycle by shape+label). Confirm `sortRows`' exact signature from `lib/registerControls.ts:118` and adapt the call if it differs (e.g. it may take `(rows, sort, dir, valueFn)` or a comparator — match it). Confirm `SortableTh` forwards `scope="col"` to `Table.Th` (it spreads `...thProps` — yes). If `Table.ScrollContainer`'s prop is `minWidth`, keep it; verify against the #17 usage in `LibraryPage`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/reports/ReportsRegisterPage.test.tsx`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/reports/ReportsRegisterPage.tsx apps/web/src/features/reports/ReportsRegisterPage.test.tsx
git commit -m "feat(web): Controlled Document Register page — provenance banner + register table"
```

---

## Task 6: Web — wire the route, nav entry, and tab title

**Files:**
- Modify: `apps/web/src/App.tsx` (import + route)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (nav entry, gated `report.read`)
- Modify: `apps/web/src/lib/routeChrome.ts` (tab title)
- Modify: `apps/web/src/app/shell/LeftRail.test.tsx` (gating assertion)

**Interfaces:**
- Consumes: `ReportsRegisterPage` (Task 5).

- [ ] **Step 1: Add the route to App.tsx**

Add the import near the other feature-page imports (after the `ObjectivesRegisterPage` import at `App.tsx:33`):

```tsx
import { ReportsRegisterPage } from "./features/reports/ReportsRegisterPage";
```

Add the route inside the operational `<Route path="/" element={<AppShell />}>` block (e.g. right after the `compliance` route at `App.tsx:144`):

```tsx
<Route path="reports/document-control" element={<ReportsRegisterPage />} />
```

- [ ] **Step 2: Add the nav entry (gated report.read)**

In `apps/web/src/app/shell/LeftRail.tsx`, add to the `CHECK` phase array (alongside Compliance / Audit / MR / Drift):

```tsx
{
  to: "/reports/document-control",
  label: "Document register",
  prefix: "/reports/document-control",
  gate: "report.read",
},
```

> Rationale: the register is a CHECK-phase auditor read; the nav `can()` is SYSTEM-scoped, which exactly matches the endpoint's SYSTEM `report.read` surface gate — a user sees the nav iff they'd pass the gate. Hidden entirely without the key (the `/drift`/`/objectives` gated-entry precedent).

- [ ] **Step 3: Add the tab title**

In `apps/web/src/lib/routeChrome.ts`, add to the `TITLES` array:

```ts
["/reports/document-control", "Document register"],
```

(Longest-prefix-wins sort already handles precedence; no other `/reports` route exists.)

- [ ] **Step 4: Add a LeftRail gating test**

In `apps/web/src/app/shell/LeftRail.test.tsx`, follow the existing pattern for a gated entry (e.g. the `/drift` or `/objectives` assertions) to add:
- with `report.read` granted → a link to `/reports/document-control` is present;
- without it → absent.

Grep the existing test for the exact `can`-mock/render harness and mirror it (do not invent a new harness).

- [ ] **Step 5: Run the web checks**

Run: `cd apps/web && npx vitest run src/app/shell/LeftRail.test.tsx src/features/reports/`
Expected: PASS.

Run the full web gate: invoke the `/check-web` skill (eslint + strict tsc + build + full vitest).
Expected: clean. (Strict `noUncheckedIndexedAccess` + cross-file drift only surface here.)

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/lib/routeChrome.ts apps/web/src/app/shell/LeftRail.test.tsx
git commit -m "feat(web): route + gated nav entry + tab title for the document register"
```

---

## Final verification (before PR)

- [ ] `/check-api` — ruff + format-check + mypy-strict + pytest unit (green; unit count +4).
- [ ] `cd apps/api && uv run pytest tests/integration/test_report_document_control.py -v -m integration` — all green (CI-authoritative across the 4 shards).
- [ ] `/check-web` — eslint + tsc + build + full vitest (green; web count +~6).
- [ ] `/check-contracts` — redocly lint clean.
- [ ] Run the `diff-critic` agent on the branch diff. Fold only CONFIRMED findings. Focus areas: (a) the two-layer gate — does the surface `require("report.read")` truly 403 an Employee AND does the per-row `document.read` filter run for every row (no leak for a PROCESS-scoped holder)? (b) content hash — is it computed over row data only, order-independent, and does the provenance echo NOT feed the hash? (c) no `audit_event` / WORM / blob write anywhere in the new paths; (d) run-scoped integration assertions (no absolute count on the shared DB, no 2nd `organization`).
- [ ] Run the `web-test-trap-reviewer` agent on the web diff. Focus: `satisfies`-pinned MSW fixtures, jest-dom `import { expect } from "vitest"`, distinct `aria-label`s, no `dangerouslySetInnerHTML`, the calm-403 path.
- [ ] Pre-merge live smoke (the `/live-smoke` skill, owner does the Keycloak login): grant a SYSTEM `report.read` override, hit `/reports/document-control` in the SPA, confirm the register renders with a provenance banner + content hash, and that an Employee (no report.read) sees the nav hidden + a 403/no-access on direct navigation.

## Self-Review (author)

- **Spec coverage:** §1 goal → all tasks; §2 two-layer gate → Task 3 (surface) + Task 2 (row filter); §3 full register → Task 2 (no cap) + Task 2 hash-covers-full-set test; §4 response shape → Tasks 2 (rows) + 3 (provenance); §5 service split → Tasks 1–2; §6 content hash → Task 1; §7 SPA → Tasks 4–6; §8 contracts → Task 3 step 5; §9 tests → each task's TDD + Final verification; §10 non-goals → not built (export/other reports/org_role/retention); §11 invariants → Global Constraints + diff-critic focus.
- **Placeholder scan:** the one intentional throwaway line in Task 3 step 3 (`org = await session.get(type(caller)...)`) is explicitly flagged for deletion and replaced with the `_org_short_code` helper — no silent placeholder.
- **Type consistency:** `register_content_hash`/`build_provenance` (Task 1) consumed verbatim in Tasks 2–3; `compute_document_control_register(session, caller, *, filters, source_ip)` + `RegisterResult{rows,content_hash,row_count}` (Task 2) consumed in Task 3; `DocumentControlRegister`/`RegisterRow`/`RegisterProvenance`/`ClauseRef` (Task 4) consumed in Tasks 5–6; `useDocumentControlRegister` (Task 4) consumed in Task 5; `ReportsRegisterPage` (Task 5) consumed in Task 6.
