# S-web-7d — Audits & findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the internal-audit module (programmes/plans/audits + the 7-state FSM, findings with NC→auto-CAPA, the R39 block-until-corrected close gate) in the SPA, over the shipped S-aud-1/2 backend plus one thin read-enrichment.

**Architecture:** New `features/audits/` module: `/audits` tab layout (Audits · Programme) + a full `/audits/:id` detail page. One backend change — serializer field-adds (`_audit` gains identifier/title/created_at, `_finding` gains title) mirroring the 7a `_capa` enrichment; no migration, no key, no endpoint. Spec: `docs/superpowers/specs/2026-06-09-web-track-s-web-7d-audits-findings-design.md` (approved).

**Tech Stack:** React/TS + Mantine + React Query + react-router (SPA) · vitest + MSW + jest-axe · FastAPI serializers (enrichment only) · openapi.yaml contract.

**Working rules (every task):**
- Branch is `feat/s-web-7d-audits-findings`; commit per task with the message given.
- Web tests run from `apps/web`: `npm test -- --pool=forks --poolOptions.forks.singleFork=true <file>` (the full parallel run can flake "document is not defined" — use forks for a clean signal).
- Fixtures are pinned to the REAL serializers (`apps/api/src/easysynq_api/api/audits.py` `_program:94 _plan:106 _audit:118 _finding:130`), never the mockup.
- All user text renders as React text nodes / Mantine `Text` — never `dangerouslySetInnerHTML`.
- `GET /processes` returns a **bare array**; all audit-family lists return `{"data":[...]}`.

**File map (created → C, modified → M):**

| File | Task |
|------|------|
| M `apps/api/src/easysynq_api/services/audits/repository.py` (list_audits join · get_audit_header · FindingRow+title) | 1 |
| M `apps/api/src/easysynq_api/api/audits.py` (`_audit`/`_audit_full`/`_finding` enrichment) | 1 |
| M `packages/contracts/openapi.yaml` (Audit/Finding field-adds) | 1 |
| M `apps/api/tests/integration/test_audits.py` (2 enrichment tests) | 1 |
| M `apps/web/src/lib/types.ts` (audit-family types) | 2 |
| C `apps/web/src/features/audits/labels.ts` | 2 |
| M `apps/web/src/test/msw/handlers.ts` (audit fixtures + handlers) | 3 |
| C `apps/web/src/features/audits/hooks.ts` (+ `hooks.test.tsx`) | 4 |
| C `apps/web/src/features/audits/mutations.ts` (+ `mutations.test.tsx`) | 5 |
| C `apps/web/src/features/audits/badges.tsx` (+ `badges.test.tsx`) | 6 |
| C `apps/web/src/features/audits/AuditsLayout.tsx` (+ test) · M `App.tsx` · M `LeftRail.tsx` (+ test) | 7 |
| C `apps/web/src/features/audits/AuditsListPage.tsx` (+ test) | 8 |
| C `apps/web/src/features/audits/NewAuditModal.tsx` (+ test) | 9 |
| C `apps/web/src/features/audits/ProgrammePage.tsx` + `ProgramForm.tsx` (+ tests) | 10 |
| C `apps/web/src/features/audits/PlanForm.tsx` (+ plans-table additions + tests) | 11 |
| C `apps/web/src/features/audits/AuditDetailPage.tsx` (+ test) | 12 |
| C `apps/web/src/features/audits/AuditLifecyclePanel.tsx` (+ test) | 13 |
| C `apps/web/src/features/audits/FindingsCard.tsx` + `FindingPanel.tsx` (+ tests) | 14 |
| C `apps/web/src/features/audits/LogFindingModal.tsx` + `CorrectFindingModal.tsx` (+ tests) | 15 |
| (verification only — full `/check-web`, axe sweep, contracts) | 16 |

---

### Task 1: Backend thin read-enrichment (`_audit` + `_finding`)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/audits/repository.py`
- Modify: `apps/api/src/easysynq_api/api/audits.py`
- Modify: `packages/contracts/openapi.yaml` (Audit schema ~line 6012; Finding schema ~line 6041)
- Test: `apps/api/tests/integration/test_audits.py` (append; CI-gated — the api test suites are Linux-CI-only on this box)

**Context:** an audit/finding is a `kind=RECORD` shared-PK subtype; identifier/title/created_at live on the `documented_information` base row. Mirror the 7a CAPA enrichment exactly: `services/capa/repository.py:140` (`list_capas` 4-tuple join) and `:157` (`get_capa_header`), `api/capa.py:170` (`_capa_full`).

- [ ] **Step 1: Write the two failing integration tests** (append to `apps/api/tests/integration/test_audits.py`, using the file's existing `_grant`/`_auth`/`_subject` helpers and key tuples):

```python
async def test_audit_list_detail_and_writes_carry_record_header(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-web-7d enrichment: _audit carries identifier/title/created_at on list + detail + writes."""
    subject = _subject("aud-enrich")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "Enrich P"})
    assert r.status_code == 201, r.text
    r = await app_client.post(
        f"/api/v1/audit-programs/{r.json()['id']}/plans", headers=h, json={}
    )
    assert r.status_code == 201, r.text
    r = await app_client.post(
        "/api/v1/audits",
        headers=h,
        json={"plan_id": r.json()["id"], "title": "Audit of Purchasing (enrich)"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    audit_id = created["id"]
    # The CREATE response already carries the record header (the _capa_full precedent).
    assert created["identifier"].startswith("REC-")
    assert created["title"] == "Audit of Purchasing (enrich)"
    assert created["created_at"] is not None

    r = await app_client.get("/api/v1/audits", headers=h)
    row = next(a for a in r.json()["data"] if a["id"] == audit_id)
    assert row["identifier"].startswith("REC-")
    assert row["title"] == "Audit of Purchasing (enrich)"
    assert row["created_at"] is not None

    r = await app_client.get(f"/api/v1/audits/{audit_id}", headers=h)
    assert r.json()["title"] == "Audit of Purchasing (enrich)"

    # A transition response carries the header too (no null-flash after a write).
    r = await app_client.post(f"/api/v1/audits/{audit_id}/plan", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["identifier"].startswith("REC-")
    assert r.json()["title"] == "Audit of Purchasing (enrich)"


async def test_finding_serializer_carries_title(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-web-7d enrichment: _finding carries title (the logged summary / correction reason)."""
    subject = _subject("fnd-enrich")
    await _grant(
        subject, _AUDIT_KEYS + ("finding.create", "finding.read")
    )
    h = _auth(token_factory, subject)

    r = await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "Enrich F"})
    r = await app_client.post(
        f"/api/v1/audit-programs/{r.json()['id']}/plans", headers=h, json={}
    )
    r = await app_client.post("/api/v1/audits", headers=h, json={"plan_id": r.json()["id"]})
    audit_id = r.json()["id"]

    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings",
        headers=h,
        json={"finding_type": "OBSERVATION", "summary": "Vendor index outside the library"},
    )
    assert r.status_code == 201, r.text
    finding = r.json()
    assert finding["title"] == "Vendor index outside the library"

    r = await app_client.get(f"/api/v1/audits/{audit_id}/findings", headers=h)
    row = next(f for f in r.json()["data"] if f["id"] == finding["id"])
    assert row["title"] == "Vendor index outside the library"

    r = await app_client.post(
        f"/api/v1/findings/{finding['id']}/correction",
        headers=h,
        json={"finding_type": "OFI", "reason": "Reclassified as an improvement"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Reclassified as an improvement"
    assert r.json()["correction_of"] == finding["id"]
```

- [ ] **Step 2: Run the static gates to confirm the current state compiles** (the integration tests themselves are Linux-CI-only here):

Run from repo root: `cd apps/api; ~/.local/bin/uv run ruff check .; ~/.local/bin/uv run mypy src`
Expected: clean BEFORE the impl change (the tests fail only in CI; locally we gate on static checks).

- [ ] **Step 3: Repository — enrich `list_audits` + add `get_audit_header` + thread `title` through `FindingRow`** in `apps/api/src/easysynq_api/services/audits/repository.py`:

Add to the imports: `from datetime import datetime`.

Replace `list_audits` (line 75) with the `list_capas` 4-tuple shape:

```python
async def list_audits(
    session: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[Audit, str | None, str | None, datetime | None]]:
    """(audit, identifier, title, created_at) — the record header lives on the base row
    (the list_capas precedent; same-PK join, zero extra queries)."""
    rows = await session.execute(
        select(
            Audit,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.created_at,
        )
        .join(DocumentedInformation, DocumentedInformation.id == Audit.id)
        .where(Audit.org_id == org_id)
    )
    return [(a, ident, title, created) for a, ident, title, created in rows.all()]


async def get_audit_header(
    session: AsyncSession, audit_id: uuid.UUID
) -> tuple[str | None, str | None, datetime | None] | None:
    """(identifier, title, created_at) for an audit's record — the get_capa_header mirror."""
    row = (
        await session.execute(
            select(
                DocumentedInformation.identifier,
                DocumentedInformation.title,
                DocumentedInformation.created_at,
            ).where(DocumentedInformation.id == audit_id)
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None
```

Change `FindingRow` + `_finding_select` + the two readers to carry `title` (the join already exists — one added column):

```python
# A finding read row: (finding, identifier, title, correction_of, superseded_by_correction).
FindingRow = tuple[AuditFinding, str, str | None, uuid.UUID | None, uuid.UUID | None]


def _finding_select() -> Select[Any]:
    return (
        select(
            AuditFinding,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            Record.correction_of,
            Record.superseded_by_correction,
        )
        .join(DocumentedInformation, DocumentedInformation.id == AuditFinding.id)
        .join(Record, Record.id == AuditFinding.id)
    )
```

`get_finding_row` returns `(row[0], row[1], row[2], row[3], row[4])`; `list_findings` returns
`[(f, ident, title, co, sbc) for f, ident, title, co, sbc in rows.all()]`.

- [ ] **Step 4: Serializers + endpoints** in `apps/api/src/easysynq_api/api/audits.py`:

`_audit` takes the header as args; `_audit_full` is the single-audit response builder (the `_capa_full` precedent) used by create + get + ALL SIX transition endpoints:

```python
def _audit(
    a: Audit,
    identifier: str | None = None,
    title: str | None = None,
    created_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "identifier": identifier,
        "title": title,
        "plan_id": str(a.plan_id),
        "lead_auditor_user_id": str(a.lead_auditor_user_id) if a.lead_auditor_user_id else None,
        "state": a.state.value,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "result_summary": a.result_summary,
        "created_at": created_at.isoformat() if created_at else None,
    }


async def _audit_full(session: AsyncSession, a: Audit) -> dict[str, Any]:
    """Serialize an audit with its record header populated — used by every single-audit response
    (create + detail + each FSM transition), so a write never returns identifier/title as null."""
    header = await audits_repo.get_audit_header(session, a.id)
    identifier, title, created_at = header if header else (None, None, None)
    return _audit(a, identifier, title, created_at)
```

- `list_audits_endpoint` → `return {"data": [_audit(a, ident, title, created) for a, ident, title, created in rows]}`.
- `get_audit_endpoint`, `create_audit_endpoint`, and the 6 transition endpoints → `return await _audit_full(session, audit)` (transitions: `return await _audit_full(session, await advance_audit(session, caller, audit_id, AuditState.X))`).

`_finding` gains `title` as the 3rd POSITIONAL arg with the chain args forced keyword (so the old 4-positional list call can't silently shift — update every call site):

```python
def _finding(
    f: AuditFinding,
    identifier: str | None,
    title: str | None = None,
    *,
    correction_of: uuid.UUID | None = None,
    superseded_by_correction: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "identifier": identifier,
        "title": title,
        "audit_id": str(f.audit_id),
        "finding_type": f.finding_type.value,
        "severity": f.severity.value if f.severity else None,
        "clause_ref": f.clause_ref,
        "process_ref": f.process_ref,
        "auto_capa_id": str(f.auto_capa_id) if f.auto_capa_id else None,
        "correction_of": str(correction_of) if correction_of else None,
        "superseded_by_correction": (
            str(superseded_by_correction) if superseded_by_correction else None
        ),
    }
```

Call sites:
- `list_findings_endpoint`: `{"data": [_finding(f, ident, title, correction_of=co, superseded_by_correction=sbc) for f, ident, title, co, sbc in rows]}`
- `get_finding_endpoint`: unpack the 5-tuple `finding, ident, title, co, sbc = row` and pass all.
- `create_finding_endpoint` + `correct_finding_endpoint`: replace the `get_identifier` call with a row read so title/chain come from the DB truth (the successor's `correction_of` is already set by `capture_record`):

```python
    row = await audits_repo.get_finding_row(session, finding.id)
    assert row is not None  # just created in this txn
    f, ident, title, co, sbc = row
    return _finding(f, ident, title, correction_of=co, superseded_by_correction=sbc)
```

(same for `successor.id` in the correction endpoint — and drop the now-unused trailing comment about `_correction_of`).

After this refactor `audits_repo.get_identifier` has NO remaining callers (both endpoint uses are
replaced by `get_finding_row`; the service layer never used it) — **delete it** from
`services/audits/repository.py` (verify with a grep for `get_identifier` scoped to the audits
package first; the capa repository has its own same-named helper, leave that one alone).

- [ ] **Step 5: Contract field-adds** in `packages/contracts/openapi.yaml`:

To the `Audit` schema properties (line ~6016):

```yaml
        identifier: { type: [string, "null"], description: The audit record's human identifier (REC-…) — from the documented_information base row. }
        title: { type: [string, "null"] }
        created_at: { type: [string, "null"], format: date-time }
```

To the `Finding` schema properties (line ~6045):

```yaml
        title: { type: [string, "null"], description: The logged summary (or correction reason) — the record title. }
```

- [ ] **Step 6: Run the local gates**

Run: `/check-api` (ruff + format + mypy-strict; unit) and `/check-contracts` (redocly).
Expected: all clean. (`pytest -m integration` is Linux-CI-only on this box — the Step-1 tests prove it in CI.)

- [ ] **Step 7: Commit**

```bash
git add apps/api packages/contracts/openapi.yaml
git commit -m "feat(s-web-7d): thin read-enrichment — _audit carries identifier/title/created_at, _finding carries title"
```

---

### Task 2: Web types + the transition/label maps

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append to the S-web-7 block)
- Create: `apps/web/src/features/audits/labels.ts`

No test of its own (consumed + asserted by every later task); strict `tsc` is the gate.

- [ ] **Step 1: Append the audit-family types** to `apps/web/src/lib/types.ts` (after the 7c NCR block). These pin the §3+§4 serializer shapes:

```ts
// ---- S-web-7d audits & findings (pinned to api/audits.py _program/_plan/_audit/_finding) ----
export type AuditState =
  | "Scheduled" | "Planned" | "InProgress" | "FindingsDraft"
  | "Reported" | "Closing" | "Closed";
export type FindingType = "NC" | "OBSERVATION" | "OFI";

export interface AuditProgram {
  id: string;
  identifier: string;            // AUDPROG-NNN
  title: string;
  period: string | null;
  coverage: Record<string, unknown> | null;
  archived: boolean;
  created_at: string;
}
export interface AuditPlan {
  id: string;
  program_id: string;
  auditee_process_id: string | null;
  lead_auditor_user_id: string | null;
  scheduled_date: string | null;  // date (YYYY-MM-DD)
  checklist_ref: string | null;
  created_at: string;
}
export interface Audit {
  id: string;
  identifier: string | null;      // S-web-7d enrichment (REC-…)
  title: string | null;           // S-web-7d enrichment
  plan_id: string;
  lead_auditor_user_id: string | null;
  state: AuditState;
  started_at: string | null;      // date
  completed_at: string | null;    // date
  result_summary: string | null;  // never written in v1 — not rendered
  created_at: string | null;      // S-web-7d enrichment
}
export interface Finding {
  id: string;
  identifier: string | null;
  title: string | null;           // S-web-7d enrichment (the logged summary / correction reason)
  audit_id: string;
  finding_type: FindingType;
  severity: NcSeverity | null;
  clause_ref: string | null;
  process_ref: string | null;
  auto_capa_id: string | null;
  correction_of: string | null;
  superseded_by_correction: string | null;
}
export interface AuditProgramList { data: AuditProgram[]; }
export interface AuditPlanList { data: AuditPlan[]; }
export interface AuditList { data: Audit[]; }
export interface FindingList { data: Finding[]; }

// request bodies
export interface AuditProgramCreateBody { title: string; period?: string; }
export interface AuditProgramUpdateBody { title?: string; period?: string; archived?: boolean; }
export interface AuditPlanCreateBody {
  auditee_process_id?: string;
  lead_auditor_user_id?: string;
  scheduled_date?: string;
  checklist_ref?: string;
}
export interface AuditCreateBody { plan_id: string; title?: string; lead_auditor_user_id?: string; }
export interface FindingCreateBody {
  finding_type: FindingType;
  severity?: NcSeverity;
  clause_ref?: string;
  process_ref?: string;
  summary?: string;
}
export interface FindingCorrectionBody {
  finding_type: FindingType;
  severity?: NcSeverity;
  clause_ref?: string;
  process_ref?: string;
  reason?: string;
}

// GET /processes (bare array; _process in api/processes.py) — the SPA reads id + name only,
// but extra fields arrive (structural subset typing).
export interface ProcessRow { id: string; name: string; }
```

- [ ] **Step 2: Create `apps/web/src/features/audits/labels.ts`** — the FSM order, labels, and the one-legal-next-transition map (the spec §6.3 labels):

```ts
import type { AuditState, FindingType } from "../../lib/types";

export const AUDIT_STATE_ORDER: AuditState[] = [
  "Scheduled", "Planned", "InProgress", "FindingsDraft", "Reported", "Closing", "Closed",
];

export const AUDIT_STATE_LABEL: Record<AuditState, string> = {
  Scheduled: "Scheduled",
  Planned: "Planned",
  InProgress: "In progress",
  FindingsDraft: "Findings draft",
  Reported: "Reported",
  Closing: "Closing",
  Closed: "Closed",
};

// The single legal next transition per state (the backend FSM is a linear forward chain).
// path = the POST sub-resource; gate = the permission key that endpoint requires.
export const NEXT_TRANSITION: Record<
  AuditState,
  { path: string; label: string; gate: "audit.conduct" | "audit.close" } | null
> = {
  Scheduled: { path: "plan", label: "Finalize plan", gate: "audit.conduct" },
  Planned: { path: "conduct", label: "Begin fieldwork", gate: "audit.conduct" },
  InProgress: { path: "draft-findings", label: "Draft findings", gate: "audit.conduct" },
  FindingsDraft: { path: "report", label: "Issue report", gate: "audit.conduct" },
  Reported: { path: "begin-closing", label: "Begin closing", gate: "audit.close" },
  Closing: { path: "close", label: "Close audit", gate: "audit.close" },
  Closed: null,
};

export const FINDING_TYPE_LABEL: Record<FindingType, string> = {
  NC: "NC",
  OBSERVATION: "Observation",
  OFI: "OFI",
};
```

- [ ] **Step 3: Typecheck**

Run from `apps/web`: `npx tsc --noEmit`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/audits/labels.ts
git commit -m "feat(s-web-7d): audit-family types + FSM label/transition maps"
```

---

### Task 3: MSW fixtures + default handlers

**Files:**
- Modify: `apps/web/src/test/msw/handlers.ts` (append fixtures + handlers; export everything)

No test of its own (every later test consumes these); `tsc` + the existing suite staying green is the gate. The `satisfies` clauses make strict tsc enforce the §3+§4 shapes.

> ⚠ These fixtures presuppose Task 1's enrichment (identifier/title/created_at on `_audit`, title on
> `_finding`) — Task 1 precedes this task and its CI integration tests + the pre-merge live smoke are
> the cross-checks that the REAL serializer matches what's mocked here (the recurring false-PASS class).

- [ ] **Step 1: Append the audit-family fixtures** to `apps/web/src/test/msw/handlers.ts` (after the S-ing-4b block, before `export const handlers`). Add `AuditList, AuditPlanList, AuditProgramList, Finding, FindingList` to the existing `import type { … } from "../../lib/types"`:

```ts
// ---- S-web-7d audit fixtures (pinned to api/audits.py _program/_plan/_audit/_finding + the
// S-web-7d read-enrichment: _audit carries identifier/title/created_at, _finding carries title) ----
export const auditProgramsFixture = {
  data: [
    { id: "ap000001-0001-0001-0001-000000000001", identifier: "AUDPROG-000001", title: "2026 Internal Audit Programme", period: "2026", coverage: null, archived: false, created_at: "2026-01-05T09:00:00+00:00" },
    { id: "ap000002-0002-0002-0002-000000000002", identifier: "AUDPROG-000002", title: "2025 Programme", period: "2025", coverage: null, archived: true, created_at: "2025-01-06T09:00:00+00:00" },
  ],
} satisfies AuditProgramList;

export const auditPlansFixture = {
  data: [
    { id: "pl000001-0001-0001-0001-000000000001", program_id: "ap000001-0001-0001-0001-000000000001", auditee_process_id: "pr000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", scheduled_date: "2026-05-28", checklist_ref: "FRM-AUD-002", created_at: "2026-01-10T09:00:00+00:00" },
    { id: "pl000002-0002-0002-0002-000000000002", program_id: "ap000001-0001-0001-0001-000000000001", auditee_process_id: null, lead_auditor_user_id: null, scheduled_date: "2026-09-01", checklist_ref: null, created_at: "2026-01-11T09:00:00+00:00" },
  ],
} satisfies AuditPlanList;

export const auditListFixture = {
  data: [
    { id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061", title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", state: "InProgress", started_at: "2026-05-28", completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00" },
    { id: "au000002-0002-0002-0002-000000000002", identifier: "REC-000055", title: "Document Control audit", plan_id: "pl000002-0002-0002-0002-000000000002", lead_auditor_user_id: null, state: "Closed", started_at: "2026-04-01", completed_at: "2026-04-30", result_summary: null, created_at: "2026-03-25T09:00:00+00:00" },
    { id: "au000003-0003-0003-0003-000000000003", identifier: "REC-000066", title: "Competence & Training audit", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", state: "Closing", started_at: "2026-05-01", completed_at: null, result_summary: null, created_at: "2026-04-25T09:00:00+00:00" },
  ],
} satisfies AuditList;

// Findings of au000001: a live Major NC (its CAPA ca000001 is at RootCause → BLOCKS close), an OFI,
// and a corrected pair (fd000003 NC superseded by fd000004 OBSERVATION → does NOT block).
export const findingsFixture = {
  data: [
    { id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062", title: "Supplier re-evaluation overdue for 2 vendors", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major", clause_ref: "8.4", process_ref: "Purchasing", auto_capa_id: "ca000001-0001-0001-0001-000000000001", correction_of: null, superseded_by_correction: null },
    { id: "fd000002-0002-0002-0002-000000000002", identifier: "REC-000063", title: "Consider automating the supplier scorecard", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "OFI", severity: null, clause_ref: "8.4", process_ref: null, auto_capa_id: null, correction_of: null, superseded_by_correction: null },
    { id: "fd000003-0003-0003-0003-000000000003", identifier: "REC-000064", title: "Mis-typed as an NC at first triage", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Minor", clause_ref: null, process_ref: null, auto_capa_id: "ca000006-0006-0006-0006-000000000006", correction_of: null, superseded_by_correction: "fd000004-0004-0004-0004-000000000004" },
    { id: "fd000004-0004-0004-0004-000000000004", identifier: "REC-000065", title: "Vendor file index stored outside the library", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "OBSERVATION", severity: null, clause_ref: null, process_ref: null, auto_capa_id: null, correction_of: "fd000003-0003-0003-0003-000000000003", superseded_by_correction: null },
  ],
} satisfies FindingList;

// A created NC finding (the POST /audits/{id}/findings default response) — auto_capa_id SET.
export const createdNcFindingFixture = {
  id: "fd-new-00-0000-0000-0000-000000000000",
  identifier: "REC-000070",
  title: "New NC finding",
  audit_id: "au000001-0001-0001-0001-000000000001",
  finding_type: "NC",
  severity: "Major",
  clause_ref: null,
  process_ref: null,
  auto_capa_id: "ca-auto-00-0000-0000-0000-000000000000",
  correction_of: null,
  superseded_by_correction: null,
} satisfies Finding;

// GET /processes returns a BARE ARRAY (api/processes.py list_processes_endpoint) — pin the full
// _process row shape (the SPA reads id+name only, but the fixture mirrors the serializer).
export const processesFixture = [
  { id: "pr000001-0001-0001-0001-000000000001", org_id: "or000001-0001-0001-0001-000000000001", name: "Purchasing", parent_id: null, owner_org_role_id: null, pdca_phase: "DO", criteria: null, state: "ACTIVE", excluded: false, is_outsourced: false, outsourced_supplier_id: null, created_at: "2026-01-01T09:00:00+00:00" },
  { id: "pr000002-0002-0002-0002-000000000002", org_id: "or000001-0001-0001-0001-000000000001", name: "Production", parent_id: null, owner_org_role_id: null, pdca_phase: "DO", criteria: null, state: "ACTIVE", excluded: false, is_outsourced: false, outsourced_supplier_id: null, created_at: "2026-01-01T09:00:00+00:00" },
];
```

- [ ] **Step 2: Append the default handlers** inside the `export const handlers = [` array (after the S-web-7c block). Defaults are happy-path; per-test overrides drive 403/409/422/empty:

```ts
  // ---- S-web-7d audits & findings (default happy-path; per-test overrides for 403/409/422) ----
  http.get("/api/v1/audit-programs", () => HttpResponse.json(auditProgramsFixture)),
  http.get("/api/v1/audit-programs/:id/plans", () => HttpResponse.json(auditPlansFixture)),
  http.get("/api/v1/audit-plans/:id", ({ params }) =>
    HttpResponse.json(
      auditPlansFixture.data.find((p) => p.id === params.id) ?? auditPlansFixture.data[0]!,
    ),
  ),
  http.post("/api/v1/audit-programs", () =>
    HttpResponse.json(
      { ...auditProgramsFixture.data[0]!, id: "ap-new-00-0000-0000-0000-000000000000", identifier: "AUDPROG-000003" },
      { status: 201 },
    ),
  ),
  http.patch("/api/v1/audit-programs/:id", ({ params }) =>
    HttpResponse.json({ ...auditProgramsFixture.data[0]!, id: String(params.id) }),
  ),
  http.post("/api/v1/audit-programs/:id/plans", () =>
    HttpResponse.json(
      { ...auditPlansFixture.data[0]!, id: "pl-new-00-0000-0000-0000-000000000000" },
      { status: 201 },
    ),
  ),
  http.get("/api/v1/audits", () => HttpResponse.json(auditListFixture)),
  http.get("/api/v1/audits/:id", ({ params }) => {
    const audit = auditListFixture.data.find((a) => a.id === params.id);
    return audit
      ? HttpResponse.json(audit)
      : HttpResponse.json({ code: "not_found", title: "Audit not found" }, { status: 404 });
  }),
  http.post("/api/v1/audits", () =>
    HttpResponse.json(
      { ...auditListFixture.data[0]!, id: "au-new-00-0000-0000-0000-000000000000", identifier: "REC-000069", state: "Scheduled", started_at: null },
      { status: 201 },
    ),
  ),
  // The 6 FSM transitions — each returns the audit advanced to its target state.
  http.post("/api/v1/audits/:id/plan", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Planned" })),
  http.post("/api/v1/audits/:id/conduct", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "InProgress" })),
  http.post("/api/v1/audits/:id/draft-findings", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "FindingsDraft" })),
  http.post("/api/v1/audits/:id/report", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Reported" })),
  http.post("/api/v1/audits/:id/begin-closing", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Closing" })),
  http.post("/api/v1/audits/:id/close", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Closed", completed_at: "2026-06-09" })),
  http.get("/api/v1/audits/:id/findings", () => HttpResponse.json(findingsFixture)),
  http.post("/api/v1/audits/:id/findings", () => HttpResponse.json(createdNcFindingFixture, { status: 201 })),
  http.post("/api/v1/findings/:id/correction", ({ params }) =>
    HttpResponse.json(
      { ...findingsFixture.data[1]!, id: "fd-corr-0-0000-0000-0000-000000000000", correction_of: String(params.id) },
      { status: 201 },
    ),
  ),
  http.get("/api/v1/processes", () => HttpResponse.json(processesFixture)),
```

- [ ] **Step 3: Run the existing full suite to prove no drift**

Run from `apps/web`: `npx tsc --noEmit && npm test -- --pool=forks --poolOptions.forks.singleFork=true`
Expected: clean tsc; all 429 existing tests still pass (new handlers are additive).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/test/msw/handlers.ts
git commit -m "test(s-web-7d): MSW audit-family fixtures + default handlers (pinned to the real serializers)"
```

---

### Task 4: Read hooks (`features/audits/hooks.ts`)

**Files:**
- Create: `apps/web/src/features/audits/hooks.ts`
- Test: `apps/web/src/features/audits/hooks.test.tsx`

The `features/capa/hooks.ts` idiom exactly: `useQuery` + `retry:false` + a `forbidden` flag from `ApiError.status===403`.

- [ ] **Step 1: Write the failing tests** (`hooks.test.tsx`). Test hooks through a probe component (the capa `hooks.test.tsx` pattern — render a tiny consumer with `renderWithProviders`):

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useAudit, useAuditPrograms, useAudits, useFindings, useProcesses } from "./hooks";

function AuditsProbe() {
  const { data, forbidden } = useAudits();
  if (forbidden) return <div>forbidden</div>;
  return <div>{(data ?? []).map((a) => a.identifier).join(",")}</div>;
}

test("useAudits unwraps {data} and surfaces rows", async () => {
  renderWithProviders(<AuditsProbe />);
  expect(await screen.findByText(/REC-000061/)).toBeInTheDocument();
});

test("useAudits surfaces a forbidden flag on 403", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<AuditsProbe />);
  expect(await screen.findByText("forbidden")).toBeInTheDocument();
});

function DetailProbe({ id }: { id: string | null }) {
  const { data } = useAudit(id);
  return <div>{data?.title ?? "none"}</div>;
}

test("useAudit fetches the detail; disabled while id is null", async () => {
  renderWithProviders(<DetailProbe id="au000001-0001-0001-0001-000000000001" />);
  expect(await screen.findByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  renderWithProviders(<DetailProbe id={null} />);
  expect(screen.getByText("none")).toBeInTheDocument();
});

function ProgramsProbe() {
  const { data } = useAuditPrograms();
  return <div>{(data ?? []).map((p) => p.identifier).join(",")}</div>;
}

test("useAuditPrograms unwraps {data}", async () => {
  renderWithProviders(<ProgramsProbe />);
  expect(await screen.findByText(/AUDPROG-000001/)).toBeInTheDocument();
});

function FindingsProbe() {
  const { data, forbidden } = useFindings("au000001-0001-0001-0001-000000000001");
  if (forbidden) return <div>findings-forbidden</div>;
  return <div>{(data ?? []).map((f) => f.identifier).join(",")}</div>;
}

test("useFindings unwraps {data}; forbidden flag on 403", async () => {
  renderWithProviders(<FindingsProbe />);
  expect(await screen.findByText(/REC-000062/)).toBeInTheDocument();
  server.use(
    http.get("/api/v1/audits/:id/findings", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<FindingsProbe />);
  expect(await screen.findByText("findings-forbidden")).toBeInTheDocument();
});

function ProcessesProbe() {
  const { data, forbidden } = useProcesses();
  if (forbidden) return <div>proc-forbidden</div>;
  return <div>{(data ?? []).map((p) => p.name).join(",")}</div>;
}

test("useProcesses reads the bare array; degrades on 403", async () => {
  renderWithProviders(<ProcessesProbe />);
  expect(await screen.findByText(/Purchasing/)).toBeInTheDocument();
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProcessesProbe />);
  expect(await screen.findByText("proc-forbidden")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run from `apps/web`: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/hooks.test.tsx`
Expected: FAIL — `./hooks` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/hooks.ts`**:

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  Audit, AuditList, AuditPlan, AuditPlanList, AuditProgramList, FindingList, ProcessRow,
} from "../../lib/types";

// Every audit-family read is gated (audit.read / finding.read) and the demo admin holds none —
// the S-web-6 calm-403 case. retry:false + a `forbidden` flag (the capa hooks idiom).
function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useAuditPrograms() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit-programs"],
    queryFn: async () => (await api.get<AuditProgramList>("/api/v1/audit-programs")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAuditPlans(programId: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit-plans", programId],
    queryFn: async () =>
      (await api.get<AuditPlanList>(`/api/v1/audit-programs/${programId!}/plans`)).data,
    enabled: programId !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAuditPlan(planId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["audit-plan", planId],
    queryFn: () => api.get<AuditPlan>(`/api/v1/audit-plans/${planId!}`),
    enabled: planId !== null,
    retry: false,
  });
}

export function useAudits() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audits"],
    queryFn: async () => (await api.get<AuditList>("/api/v1/audits")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAudit(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api.get<Audit>(`/api/v1/audits/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useFindings(auditId: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["findings", auditId],
    queryFn: async () => (await api.get<FindingList>(`/api/v1/audits/${auditId!}/findings`)).data,
    enabled: auditId !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /processes returns a BARE array (not {data}). Auxiliary read for the plan form's process
// picker + name resolution — degrade gracefully (omit the picker) when process.read is missing.
export function useProcesses() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["processes"],
    queryFn: () => api.get<ProcessRow[]>("/api/v1/processes"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/hooks.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/audits/hooks.ts apps/web/src/features/audits/hooks.test.tsx
git commit -m "feat(s-web-7d): audit-family read hooks (forbidden-flag idiom)"
```

---

### Task 5: Write mutations (`features/audits/mutations.ts`)

**Files:**
- Create: `apps/web/src/features/audits/mutations.ts`
- Test: `apps/web/src/features/audits/mutations.test.tsx`

The `features/capa/mutations.ts` idiom: `useMutation` + invalidate-and-refetch, never optimistic. **No `Idempotency-Key`** — the audits endpoints have no server replay latch; double-submit is guarded by disabled-while-pending in the forms.

- [ ] **Step 1: Write the failing tests** (`mutations.test.tsx` — probe components, the capa `mutations.test.tsx` pattern):

```tsx
import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useAdvanceAudit, useCreateAudit, useCreateFinding, useCreateProgram } from "./mutations";

let advance: ReturnType<typeof useAdvanceAudit>;
function AdvanceProbe({ auditId }: { auditId: string }) {
  advance = useAdvanceAudit(auditId);
  return <div>{advance.isError ? "error" : (advance.data?.state ?? "idle")}</div>;
}

test("useAdvanceAudit POSTs the transition sub-resource", async () => {
  let hit = "";
  server.use(
    http.post("/api/v1/audits/:id/begin-closing", ({ params }) => {
      hit = String(params.id);
      return HttpResponse.json({ state: "Closing" });
    }),
  );
  renderWithProviders(<AdvanceProbe auditId="au000001-0001-0001-0001-000000000001" />);
  act(() => advance.mutate("begin-closing"));
  await waitFor(() => expect(hit).toBe("au000001-0001-0001-0001-000000000001"));
});

test("a 409 (audit_close_blocked) surfaces as an error — and still refetches (onSettled)", async () => {
  server.use(
    http.post("/api/v1/audits/:id/close", () =>
      HttpResponse.json(
        { code: "audit_close_blocked", title: "Cannot close: 1 live NC finding(s) without a Closed CAPA" },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<AdvanceProbe auditId="au000003-0003-0003-0003-000000000003" />);
  act(() => advance.mutate("close"));
  expect(await screen.findByText("error")).toBeInTheDocument();
});

let createFinding: ReturnType<typeof useCreateFinding>;
function FindingProbe() {
  createFinding = useCreateFinding("au000001-0001-0001-0001-000000000001");
  return <div>{createFinding.data?.auto_capa_id ?? "idle"}</div>;
}

test("useCreateFinding POSTs and returns the created finding (auto_capa_id on NC)", async () => {
  renderWithProviders(<FindingProbe />);
  act(() => createFinding.mutate({ finding_type: "NC", severity: "Major", summary: "x" }));
  expect(await screen.findByText("ca-auto-00-0000-0000-0000-000000000000")).toBeInTheDocument();
});

let createAudit: ReturnType<typeof useCreateAudit>;
function CreateAuditProbe() {
  createAudit = useCreateAudit();
  return <div>{createAudit.data?.id ?? "idle"}</div>;
}

test("useCreateAudit POSTs /audits", async () => {
  renderWithProviders(<CreateAuditProbe />);
  act(() => createAudit.mutate({ plan_id: "pl000001-0001-0001-0001-000000000001" }));
  expect(await screen.findByText("au-new-00-0000-0000-0000-000000000000")).toBeInTheDocument();
});

let createProgram: ReturnType<typeof useCreateProgram>;
function CreateProgramProbe() {
  createProgram = useCreateProgram();
  return <div>{createProgram.data?.identifier ?? "idle"}</div>;
}

test("useCreateProgram POSTs /audit-programs", async () => {
  renderWithProviders(<CreateProgramProbe />);
  act(() => createProgram.mutate({ title: "New programme" }));
  expect(await screen.findByText("AUDPROG-000003")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/mutations.test.tsx`
Expected: FAIL — `./mutations` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/mutations.ts`**:

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Audit, AuditCreateBody, AuditPlan, AuditPlanCreateBody, AuditProgram,
  AuditProgramCreateBody, AuditProgramUpdateBody, Finding, FindingCorrectionBody,
  FindingCreateBody,
} from "../../lib/types";

// Invalidate + refetch, never optimistic — the FSM, the close gate, and the NC→auto-CAPA are
// server truths. No Idempotency-Key: the audits endpoints have no replay latch (forms guard
// double-submit via disabled-while-pending).

export function useCreateProgram() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditProgramCreateBody) =>
      api.send<AuditProgram>("POST", "/api/v1/audit-programs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-programs"] }),
  });
}

export function useUpdateProgram(programId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditProgramUpdateBody) =>
      api.send<AuditProgram>("PATCH", `/api/v1/audit-programs/${programId}`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-programs"] }),
  });
}

export function useCreatePlan(programId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditPlanCreateBody) =>
      api.send<AuditPlan>("POST", `/api/v1/audit-programs/${programId}/plans`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-plans", programId] }),
  });
}

export function useCreateAudit() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditCreateBody) => api.send<Audit>("POST", "/api/v1/audits", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audits"] }),
  });
}

// One mutation for all six transitions — the variable IS the sub-resource path ("plan" |
// "conduct" | "draft-findings" | "report" | "begin-closing" | "close"). Invalidate on SETTLE:
// a 409 (invalid_audit_transition from a stale tab, or audit_close_blocked) means our cached
// state may be stale — refetch behind the calm error (the 7c disposition-race precedent).
export function useAdvanceAudit(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => api.send<Audit>("POST", `/api/v1/audits/${auditId}/${path}`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["audit", auditId] });
      void qc.invalidateQueries({ queryKey: ["audits"] });
    },
  });
}

// An NC response carries auto_capa_id → the CAPA board must see the new CAPA (["capas"]).
export function useCreateFinding(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FindingCreateBody) =>
      api.send<Finding>("POST", `/api/v1/audits/${auditId}/findings`, body),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ["findings", auditId] });
      if (created.auto_capa_id) void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}

// Correction: settle-invalidate (a 409 finding_already_corrected race means the list is stale —
// refetch flips the original to its superseded render behind the calm error).
export function useCorrectFinding(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ findingId, body }: { findingId: string; body: FindingCorrectionBody }) =>
      api.send<Finding>("POST", `/api/v1/findings/${findingId}/correction`, body),
    onSettled: (created) => {
      void qc.invalidateQueries({ queryKey: ["findings", auditId] });
      if (created?.auto_capa_id) void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/mutations.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/audits/mutations.ts apps/web/src/features/audits/mutations.test.tsx
git commit -m "feat(s-web-7d): audit-family mutations (invalidate-on-settle for the 409 races)"
```

---

### Task 6: DP-7 badges (`AuditStateBadge` + `FindingTypeBadge`)

**Files:**
- Create: `apps/web/src/features/audits/badges.tsx`
- Test: `apps/web/src/features/audits/badges.test.tsx`

DP-7: non-color glyph + text label (never color-only). NO `aria-label` attribute on the badges —
the text content IS the accessible name, and looped findings would otherwise duplicate labels
(the S-web-6/7b trap; queries scope `within(row)`).

- [ ] **Step 1: Write the failing tests** (`badges.test.tsx`):

```tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { AuditStateBadge, FindingTypeBadge } from "./badges";

function r(ui: React.ReactElement) {
  return render(<MantineProvider theme={theme}>{ui}</MantineProvider>);
}

test("AuditStateBadge renders glyph + label per state (non-color)", () => {
  r(<AuditStateBadge state="InProgress" />);
  expect(screen.getByText(/● In progress/)).toBeInTheDocument();
});

test("AuditStateBadge renders the closed checkmark", () => {
  r(<AuditStateBadge state="Closed" />);
  expect(screen.getByText(/✓ Closed/)).toBeInTheDocument();
});

test("FindingTypeBadge renders severity + NC for an NC", () => {
  r(<FindingTypeBadge type="NC" severity="Major" />);
  expect(screen.getByText(/⚑ Major NC/)).toBeInTheDocument();
});

test("FindingTypeBadge renders Observation / OFI without severity", () => {
  r(<FindingTypeBadge type="OBSERVATION" severity={null} />);
  expect(screen.getByText(/◆ Observation/)).toBeInTheDocument();
  r(<FindingTypeBadge type="OFI" severity={null} />);
  expect(screen.getByText(/➚ OFI/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/badges.test.tsx`
Expected: FAIL — `./badges` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/badges.tsx`** (reuse `SEVERITY_COLOR`/`SEVERITY_LABEL` from `../capa/columns`):

```tsx
import { Badge } from "@mantine/core";
import type { AuditState, FindingType, NcSeverity } from "../../lib/types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "../capa/columns";
import { AUDIT_STATE_LABEL } from "./labels";

// DP-7: glyph + label, never color-only. Text content is the accessible name (no aria-label —
// looped rows would duplicate it; tests scope within(row)).
const STATE_GLYPH: Record<AuditState, string> = {
  Scheduled: "◷",
  Planned: "◷",
  InProgress: "●",
  FindingsDraft: "✎",
  Reported: "▤",
  Closing: "◔",
  Closed: "✓",
};

const STATE_COLOR: Record<AuditState, string> = {
  Scheduled: "gray",
  Planned: "gray",
  InProgress: "blue",
  FindingsDraft: "yellow",
  Reported: "violet",
  Closing: "orange",
  Closed: "green",
};

export function AuditStateBadge({ state }: { state: AuditState }) {
  return (
    <Badge variant="light" color={STATE_COLOR[state]}>
      {STATE_GLYPH[state]} {AUDIT_STATE_LABEL[state]}
    </Badge>
  );
}

export function FindingTypeBadge({
  type,
  severity,
}: {
  type: FindingType;
  severity: NcSeverity | null;
}) {
  if (type === "NC") {
    const sev = severity ? `${SEVERITY_LABEL[severity]} ` : "";
    return (
      <Badge variant="light" color={severity ? SEVERITY_COLOR[severity] : "red"}>
        ⚑ {sev}NC
      </Badge>
    );
  }
  if (type === "OBSERVATION") {
    return (
      <Badge variant="light" color="gray">
        ◆ Observation
      </Badge>
    );
  }
  return (
    <Badge variant="light" color="blue">
      ➚ OFI
    </Badge>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/badges.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/audits/badges.tsx apps/web/src/features/audits/badges.test.tsx
git commit -m "feat(s-web-7d): DP-7 audit-state + finding-type badges"
```

---

### Task 7: Routes + nav (`AuditsLayout`, `App.tsx`, `LeftRail`)

**Files:**
- Create: `apps/web/src/features/audits/AuditsLayout.tsx`
- Test: `apps/web/src/features/audits/AuditsLayout.test.tsx`
- Modify: `apps/web/src/App.tsx` (routes)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (+ its existing `LeftRail.test.tsx`)

NOTE: `AuditsListPage` / `ProgrammePage` / `AuditDetailPage` don't exist yet — `App.tsx` is wired in THIS task with placeholder-free imports by creating minimal page stubs? **No.** Wire `App.tsx` in Task 12 Step 6 instead, once all three pages exist. This task creates the layout + nav only.

- [ ] **Step 1: Write the failing layout test** (`AuditsLayout.test.tsx` — the `CapaLayout.test.tsx` shape):

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { AuditsLayout } from "./AuditsLayout";

function harness(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="audits" element={<AuditsLayout />}>
        <Route index element={<div>AUDITS-FACE</div>} />
        <Route path="programme" element={<div>PROGRAMME-FACE</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

test("renders the two tabs with the index face active at /audits", async () => {
  harness("/audits");
  expect(await screen.findByText("AUDITS-FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Audits" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Programme" })).toHaveAttribute("aria-selected", "false");
});

test("deep-link /audits/programme selects the Programme tab", async () => {
  harness("/audits/programme");
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Programme" })).toHaveAttribute("aria-selected", "true");
});

test("clicking a tab navigates the outlet", async () => {
  const u = userEvent.setup();
  harness("/audits");
  await u.click(screen.getByRole("tab", { name: "Programme" }));
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditsLayout.test.tsx`
Expected: FAIL — `./AuditsLayout` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/AuditsLayout.tsx`** (the `CapaLayout` shape — no `<Title>`, each face keeps its own):

```tsx
import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// The Internal-Audit front door's secondary nav (S-web-7d): Audits (index) · Programme.
// The /audits/:id detail page sits OUTSIDE this layout (it is a destination, not a tab).
const TABS = [
  { value: "audits", label: "Audits", path: "/audits" },
  { value: "programme", label: "Programme", path: "/audits/programme" },
] as const;

function activeTab(pathname: string): string {
  return pathname.startsWith("/audits/programme") ? "programme" : "audits";
}

export function AuditsLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Container size="xl" pt="md" pb={0}>
        <Tabs
          value={activeTab(pathname)}
          onChange={(v) => {
            const tab = TABS.find((t) => t.value === v);
            if (tab) navigate(tab.path);
          }}
        >
          <Tabs.List>
            {TABS.map((t) => (
              <Tabs.Tab key={t.value} value={t.value}>
                {t.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
      </Container>
      <Outlet />
    </>
  );
}
```

- [ ] **Step 4: Add the unconditional nav entry** in `apps/web/src/app/shell/LeftRail.tsx`, directly after the "Nonconformity & CAPA" NavLink:

```tsx
      <NavLink
        component={Link}
        to="/audits"
        label="Internal Audit"
        active={pathname.startsWith("/audits")}
      />
```

And extend the existing `apps/web/src/app/shell/LeftRail.test.tsx` with:

```tsx
test("Internal Audit entry is unconditional (the CAPA precedent — calm-403 lives on the page)", async () => {
  renderWithProviders(<LeftRail />);
  expect(await screen.findByRole("link", { name: "Internal Audit" })).toHaveAttribute(
    "href",
    "/audits",
  );
});
```

(match the file's existing render helper/imports — it already renders `LeftRail` with providers).

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditsLayout.test.tsx src/app/shell/LeftRail.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/AuditsLayout.tsx apps/web/src/features/audits/AuditsLayout.test.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx
git commit -m "feat(s-web-7d): /audits tab layout + unconditional Internal Audit nav entry"
```

---

### Task 8: `AuditsListPage` (tiles · filter · table · calm-403)

**Files:**
- Create: `apps/web/src/features/audits/AuditsListPage.tsx`
- Test: `apps/web/src/features/audits/AuditsListPage.test.tsx`

The New-audit button + modal land together in Task 9 — this task builds the page WITHOUT them
(tiles, filter, table, calm states only), so it is green standalone with no placeholder imports.

- [ ] **Step 1: Write the failing tests** (`AuditsListPage.test.tsx`):

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuditsListPage } from "./AuditsListPage";

test("renders honest tiles (Total / Active / Closed) from the list", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  // 3 fixture audits: InProgress + Closing (active) and Closed.
  // Tile labels are "… audits" so they never collide with the segmented control's All/Active/Closed.
  const total = await screen.findByText("Total audits");
  expect(within(total.closest("[data-tile]") as HTMLElement).getByText("3")).toBeInTheDocument();
  const active = screen.getByText("Active audits");
  expect(within(active.closest("[data-tile]") as HTMLElement).getByText("2")).toBeInTheDocument();
  const closed = screen.getByText("Closed audits");
  expect(within(closed.closest("[data-tile]") as HTMLElement).getByText("1")).toBeInTheDocument();
});

test("table renders identifier/title/lead/state/date, newest-first; identifier links to the detail", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  const rows = await screen.findAllByRole("row");
  // rows[0] is the header; newest created_at first → REC-000061 (2026-05-20) before REC-000066 (04-25) before REC-000055 (03-25).
  expect(within(rows[1]!).getByText("REC-000061")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("Mara Quality")).toBeInTheDocument(); // directory resolution
  expect(within(rows[1]!).getByText(/● In progress/)).toBeInTheDocument();
  expect(within(rows[2]!).getByText("REC-000066")).toBeInTheDocument();
  expect(within(rows[3]!).getByText("REC-000055")).toBeInTheDocument();
  // a lead the directory can't resolve degrades to a short id ("—" when null).
  expect(within(rows[3]!).getByText("—")).toBeInTheDocument();
  expect(within(rows[1]!).getByRole("link", { name: "REC-000061" })).toHaveAttribute(
    "href",
    "/audits/au000001-0001-0001-0001-000000000001",
  );
});

test("the Active/Closed segmented filter slices client-side", async () => {
  const u = userEvent.setup();
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  await u.click(screen.getByRole("radio", { name: "Closed" }));
  expect(screen.queryByText("REC-000061")).toBeNull();
  expect(screen.getByText("REC-000055")).toBeInTheDocument();
  await u.click(screen.getByRole("radio", { name: "Active" }));
  expect(screen.getByText("REC-000061")).toBeInTheDocument();
  expect(screen.queryByText("REC-000055")).toBeNull();
});

test("renders a calm no-access panel on a 403 (audit.read)", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  expect(await screen.findByText(/don't have access to internal audits/)).toBeInTheDocument();
});

test("an audit title containing markup renders as literal text (XSS-safe)", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({
        data: [{ id: "au-xss-00-0000-0000-0000-000000000000", identifier: "REC-000099", title: "<script>alert(1)</script>", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: null, state: "Scheduled", started_at: null, completed_at: null, result_summary: null, created_at: "2026-06-01T09:00:00+00:00" }],
      }),
    ),
  );
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  expect(await screen.findByText("<script>alert(1)</script>")).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditsListPage.test.tsx`
Expected: FAIL — `./AuditsListPage` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/AuditsListPage.tsx`**:

```tsx
import {
  Alert, Anchor, Container, Group, Loader, Paper, SegmentedControl, SimpleGrid, Table, Text, Title,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { Audit, DirectoryUser } from "../../lib/types";
import { AuditStateBadge } from "./badges";
import { useAudits } from "./hooks";

function leadLabel(userId: string | null, directory: DirectoryUser[]): string {
  if (!userId) return "—";
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <Paper withBorder p="md" data-tile>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
    </Paper>
  );
}

export function AuditsListPage() {
  const { data, isLoading, isError, forbidden } = useAudits();
  const { data: directory } = useUserDirectory();
  const [filter, setFilter] = useState<"all" | "active" | "closed">("all");

  if (forbidden) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to internal audits. They're available to roles holding{" "}
          <code>audit.read</code> (QMS Owner, Process Owner, Internal Auditor).
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="xl" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <Alert color="red" title="Couldn't load audits">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const all = data ?? [];
  // Active = state ≠ Closed (the spec definition). Sort newest-first by created_at (no server order).
  const isActive = (a: Audit) => a.state !== "Closed";
  const sorted = [...all].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const rows =
    filter === "active" ? sorted.filter(isActive)
    : filter === "closed" ? sorted.filter((a) => !isActive(a))
    : sorted;

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Internal Audit</Title>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }} mb="md">
        {/* "… audits" labels: distinct from the segmented control's All/Active/Closed radio names. */}
        <Tile label="Total audits" value={all.length} />
        <Tile label="Active audits" value={all.filter(isActive).length} />
        <Tile label="Closed audits" value={all.filter((a) => !isActive(a)).length} />
      </SimpleGrid>
      <SegmentedControl
        mb="md"
        value={filter}
        onChange={(v) => setFilter(v as typeof filter)}
        data={[
          { value: "all", label: "All" },
          { value: "active", label: "Active" },
          { value: "closed", label: "Closed" },
        ]}
      />
      {rows.length === 0 ? (
        <Text c="dimmed">No audits yet.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Audit</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Lead auditor</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Started</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/audits/${a.id}`}>
                    {a.identifier ?? a.id.slice(0, 8)}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{a.title ?? "—"}</Text>
                </Table.Td>
                <Table.Td>{leadLabel(a.lead_auditor_user_id, directory ?? [])}</Table.Td>
                <Table.Td>
                  <AuditStateBadge state={a.state} />
                </Table.Td>
                <Table.Td>{a.started_at ?? (a.created_at ? a.created_at.slice(0, 10) : "—")}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Container>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditsListPage.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/audits/AuditsListPage.tsx apps/web/src/features/audits/AuditsListPage.test.tsx
git commit -m "feat(s-web-7d): audits list page — honest tiles, segmented filter, calm-403"
```

---

### Task 9: `NewAuditModal` (programme → plan cascade)

**Files:**
- Create: `apps/web/src/features/audits/NewAuditModal.tsx`
- Modify: `apps/web/src/features/audits/AuditsListPage.tsx` (add the gated button + modal)
- Test: `apps/web/src/features/audits/NewAuditModal.test.tsx` (+ extend `AuditsListPage.test.tsx` gating)

- [ ] **Step 1: Write the failing tests** (`NewAuditModal.test.tsx`). The modal navigates on success — assert via a probe route:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NewAuditModal } from "./NewAuditModal";

function harness() {
  return renderWithProviders(
    <Routes>
      <Route path="/audits" element={<NewAuditModal opened onClose={() => {}} />} />
      <Route path="/audits/:id" element={<div>DETAIL-PAGE</div>} />
    </Routes>,
    { route: "/audits" },
  );
}

test("cascade: picking a programme loads its plans; submit POSTs plan_id and navigates", async () => {
  let body: { plan_id?: string; title?: string } | null = null;
  server.use(
    http.post("/api/v1/audits", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "au-new-00-0000-0000-0000-000000000000", identifier: "REC-000069", title: null, plan_id: body!.plan_id!, lead_auditor_user_id: null, state: "Scheduled", started_at: null, completed_at: null, result_summary: null, created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  harness();
  const dialog = await screen.findByRole("dialog");
  // Submit is disabled until a plan is picked.
  expect(within(dialog).getByRole("button", { name: /Create audit/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Programme/));
  await u.click(await screen.findByRole("option", { name: /2026 Internal Audit Programme/ }));
  await u.click(within(dialog).getByLabelText(/^Plan/));
  await u.click(await screen.findByRole("option", { name: /2026-05-28/ }));
  await u.type(within(dialog).getByLabelText(/Title/), "Purchasing audit Q3");
  await u.click(within(dialog).getByRole("button", { name: /Create audit/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.plan_id).toBe("pl000001-0001-0001-0001-000000000001");
  expect(body!.title).toBe("Purchasing audit Q3");
  expect(await screen.findByText("DETAIL-PAGE")).toBeInTheDocument();
});

test("calm empty-state guidance when no programmes exist", async () => {
  server.use(http.get("/api/v1/audit-programs", () => HttpResponse.json({ data: [] })));
  harness();
  expect(await screen.findByText(/No audit plans yet/)).toBeInTheDocument();
  expect(screen.getByText(/Programme tab/)).toBeInTheDocument();
});
```

And in `AuditsListPage.test.tsx` add the gating pair (the `grant` helper from `ComplaintsPage.test.tsx` — copy it into this file):

```tsx
test("New audit hidden without audit.create; shown + opens with the key", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  expect(screen.queryByRole("button", { name: /New audit/ })).toBeNull();

  grant(["audit.create"]);
  const u = userEvent.setup();
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await u.click(await screen.findByRole("button", { name: /New audit/ }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/NewAuditModal.test.tsx`
Expected: FAIL — `./NewAuditModal` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/NewAuditModal.tsx`**:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { useAuditPlans, useAuditPrograms } from "./hooks";
import { useCreateAudit } from "./mutations";

// POST /audits needs a plan_id — the cascade picks programme → that programme's plans. The lead
// auditor defaults server-side to the plan's lead; the optional picker rides the user directory
// (degrades to absent when the directory is empty/denied).
export function NewAuditModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const programs = useAuditPrograms();
  const [programId, setProgramId] = useState<string | null>(null);
  const plans = useAuditPlans(programId);
  const { data: directory } = useUserDirectory();
  const [planId, setPlanId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [leadId, setLeadId] = useState<string | null>(null);
  const create = useCreateAudit();

  const programRows = programs.data ?? [];
  const planRows = plans.data ?? [];
  const directoryRows = directory ?? [];

  function submit() {
    if (!planId) return;
    create.mutate(
      {
        plan_id: planId,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(leadId ? { lead_auditor_user_id: leadId } : {}),
      },
      { onSuccess: (audit) => navigate(`/audits/${audit.id}`) },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New audit">
      {programRows.length === 0 ? (
        <Text c="dimmed">
          No audit plans yet — create a programme and add a plan on the Programme tab first.
        </Text>
      ) : (
        <Stack gap="sm">
          <Select
            label="Programme"
            placeholder="Pick a programme"
            data={programRows.map((p) => ({ value: p.id, label: `${p.identifier} — ${p.title}` }))}
            value={programId}
            onChange={(v) => {
              setProgramId(v);
              setPlanId(null);
            }}
          />
          <Select
            label="Plan"
            placeholder={programId ? "Pick a plan" : "Pick a programme first"}
            disabled={!programId}
            data={planRows.map((p) => ({
              value: p.id,
              label: [p.scheduled_date ?? "unscheduled", p.checklist_ref].filter(Boolean).join(" · "),
            }))}
            value={planId}
            onChange={setPlanId}
          />
          <TextInput
            label="Title (optional)"
            value={title}
            onChange={(e) => setTitle(e.currentTarget.value)}
          />
          {directoryRows.length > 0 && (
            <Select
              label="Lead auditor (optional — defaults to the plan's)"
              data={directoryRows.map((u) => ({ value: u.id, label: u.display_name }))}
              value={leadId}
              onChange={setLeadId}
              clearable
            />
          )}
          {create.isError && (
            <Alert color="red" title="Couldn't create the audit">
              {create.error instanceof ApiError ? create.error.message : "Please try again."}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!planId} loading={create.isPending}>
              Create audit
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
```

- [ ] **Step 4: Wire the gated button into `AuditsListPage.tsx`** — add imports (`Button`, `usePermissions`, `NewAuditModal`, `useState` already present), state + button + mount:

```tsx
  const { can } = usePermissions();
  const [newOpen, setNewOpen] = useState(false);
```

In the header `Group` (next to the `Title`):

```tsx
        {can("audit.create") && <Button onClick={() => setNewOpen(true)}>＋ New audit</Button>}
```

Before the closing `</Container>`:

```tsx
      <NewAuditModal opened={newOpen} onClose={() => setNewOpen(false)} />
```

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/NewAuditModal.test.tsx src/features/audits/AuditsListPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/NewAuditModal.tsx apps/web/src/features/audits/NewAuditModal.test.tsx apps/web/src/features/audits/AuditsListPage.tsx apps/web/src/features/audits/AuditsListPage.test.tsx
git commit -m "feat(s-web-7d): New-audit modal (programme→plan cascade, audit.create-gated)"
```

---

### Task 10: `ProgrammePage` (programmes table) + `ProgramForm`

**Files:**
- Create: `apps/web/src/features/audits/ProgrammePage.tsx`
- Create: `apps/web/src/features/audits/ProgramForm.tsx`
- Test: `apps/web/src/features/audits/ProgrammePage.test.tsx`

This task ships the programmes half; Task 11 adds the per-programme plans table + `PlanForm` into
the same page. `coverage` is NOT exposed (free-form dict — no honest form for it, spec §5).

- [ ] **Step 1: Write the failing tests** (`ProgrammePage.test.tsx`; copy the `grant` helper from `ComplaintsPage.test.tsx`):

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ProgrammePage } from "./ProgrammePage";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

test("lists programmes with the archived badge; write affordances hidden without audit.plan", async () => {
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  expect(await screen.findByText("AUDPROG-000001")).toBeInTheDocument();
  const archived = screen.getByRole("row", { name: /AUDPROG-000002/ });
  expect(within(archived).getByText(/Archived/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /New programme/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
});

test("creating a programme POSTs title + period", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; period?: string } | null = null;
  server.use(
    http.post("/api/v1/audit-programs", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "ap-new-00-0000-0000-0000-000000000000", identifier: "AUDPROG-000003", title: body!.title!, period: body!.period ?? null, coverage: null, archived: false, created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /New programme/ }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/Title/), "2027 Programme");
  await u.type(within(dialog).getByLabelText(/Period/), "2027");
  await u.click(within(dialog).getByRole("button", { name: /Save programme/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.title).toBe("2027 Programme");
  expect(body!.period).toBe("2027");
});

test("editing pre-fills and PATCHes; the archive toggle rides the same form", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; archived?: boolean } | null = null;
  server.use(
    http.patch("/api/v1/audit-programs/:id", async ({ request, params }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({ id: String(params.id), identifier: "AUDPROG-000001", title: "2026 Internal Audit Programme", period: "2026", coverage: null, archived: true, created_at: "2026-01-05T09:00:00+00:00" });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const row = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  await u.click(within(row).getByRole("button", { name: /Edit/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Title/)).toHaveValue("2026 Internal Audit Programme");
  await u.click(within(dialog).getByLabelText(/Archived/));
  await u.click(within(dialog).getByRole("button", { name: /Save programme/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.archived).toBe(true);
});

test("renders a calm no-access panel on a 403 (audit.read)", async () => {
  server.use(
    http.get("/api/v1/audit-programs", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  expect(await screen.findByText(/don't have access to the audit programme/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await screen.findByText("AUDPROG-000001");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/ProgrammePage.test.tsx`
Expected: FAIL — `./ProgrammePage` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/ProgramForm.tsx`** (one modal for create + edit; edit shows the Archived switch):

```tsx
import { Alert, Button, Group, Modal, Stack, Switch, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { AuditProgram } from "../../lib/types";
import { useCreateProgram, useUpdateProgram } from "./mutations";

// Create (program == null) or edit (pre-filled + the Archived toggle). `coverage` is not exposed
// (free-form dict — no honest form, spec §5).
export function ProgramForm({
  program,
  opened,
  onClose,
}: {
  program: AuditProgram | null;
  opened: boolean;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(program?.title ?? "");
  const [period, setPeriod] = useState(program?.period ?? "");
  const [archived, setArchived] = useState(program?.archived ?? false);
  const create = useCreateProgram();
  const update = useUpdateProgram(program?.id ?? "");
  const active = program ? update : create;

  function submit() {
    if (!title.trim()) return;
    const onSuccess = () => onClose();
    if (program) {
      update.mutate(
        { title: title.trim(), ...(period.trim() ? { period: period.trim() } : {}), archived },
        { onSuccess },
      );
    } else {
      create.mutate(
        { title: title.trim(), ...(period.trim() ? { period: period.trim() } : {}) },
        { onSuccess },
      );
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={program ? "Edit programme" : "New programme"}>
      <Stack gap="sm">
        <TextInput
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <TextInput
          label="Period"
          placeholder="e.g. 2026"
          value={period}
          onChange={(e) => setPeriod(e.currentTarget.value)}
        />
        {program && (
          <Switch
            label="Archived"
            checked={archived}
            onChange={(e) => setArchived(e.currentTarget.checked)}
          />
        )}
        {active.isError && (
          <Alert color="red" title="Couldn't save the programme">
            {active.error instanceof ApiError ? active.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!title.trim()} loading={active.isPending}>
            Save programme
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement `apps/web/src/features/audits/ProgrammePage.tsx`** (programmes half; Task 11 appends the plans section where marked):

```tsx
import { Alert, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import type { AuditProgram } from "../../lib/types";
import { useAuditPrograms } from "./hooks";
import { ProgramForm } from "./ProgramForm";

export function ProgrammePage() {
  const { data, isLoading, isError, forbidden } = useAuditPrograms();
  const { can } = usePermissions();
  // null = closed; "new" = create; a programme = edit. Keyed remount resets the form state.
  const [editing, setEditing] = useState<AuditProgram | "new" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (forbidden) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Audit Programme
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the audit programme. It's available to roles holding{" "}
          <code>audit.read</code>.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="xl" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Audit Programme
        </Title>
        <Alert color="red" title="Couldn't load programmes">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const rows = data ?? [];
  const selected = rows.find((p) => p.id === selectedId) ?? rows[0] ?? null;

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Audit Programme</Title>
        {can("audit.plan") && (
          <Button onClick={() => setEditing("new")}>＋ New programme</Button>
        )}
      </Group>
      {rows.length === 0 ? (
        <Text c="dimmed">No programmes yet.</Text>
      ) : (
        <Table striped highlightOnHover mb="lg">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Period</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((p) => (
              <Table.Tr
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                style={{ cursor: "pointer" }}
                data-selected={selected?.id === p.id || undefined}
              >
                <Table.Td>{p.identifier}</Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{p.title}</Text>
                </Table.Td>
                <Table.Td>{p.period ?? "—"}</Table.Td>
                <Table.Td>
                  {p.archived ? (
                    <Badge variant="light" color="gray">
                      ▣ Archived
                    </Badge>
                  ) : (
                    <Badge variant="light" color="green">
                      ▶ Active
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  {can("audit.plan") && (
                    <Button
                      size="xs"
                      variant="subtle"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditing(p);
                      }}
                    >
                      Edit
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {/* Task 11 mounts the selected programme's plans section here (uses `selected`). */}
      {editing !== null && (
        <ProgramForm
          key={editing === "new" ? "new" : editing.id}
          program={editing === "new" ? null : editing}
          opened
          onClose={() => setEditing(null)}
        />
      )}
    </Container>
  );
}
```

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/ProgrammePage.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/ProgrammePage.tsx apps/web/src/features/audits/ProgramForm.tsx apps/web/src/features/audits/ProgrammePage.test.tsx
git commit -m "feat(s-web-7d): Programme tab — programmes table + create/edit/archive (audit.plan-gated)"
```

---

### Task 11: Plans table + `PlanForm` (on `ProgrammePage`)

**Files:**
- Create: `apps/web/src/features/audits/PlanForm.tsx`
- Modify: `apps/web/src/features/audits/ProgrammePage.tsx` (the plans section)
- Test: extend `apps/web/src/features/audits/ProgrammePage.test.tsx`

Date entry uses a plain `<TextInput type="date">` — NO `@mantine/dates` dependency (the track has
never added it; the backend takes a bare `YYYY-MM-DD`).

- [ ] **Step 1: Write the failing tests** (append to `ProgrammePage.test.tsx`):

```tsx
test("shows the selected programme's plans (process + lead resolved, degrade-friendly)", async () => {
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  // Newest programme (AUDPROG-000001) is selected by default → its plans render.
  expect(await screen.findByText("Plans — AUDPROG-000001")).toBeInTheDocument();
  const planRows = await screen.findAllByRole("row", { name: /2026-/ });
  expect(within(planRows[0]!).getByText("2026-05-28")).toBeInTheDocument();
  expect(within(planRows[0]!).getByText("Purchasing")).toBeInTheDocument(); // process name
  expect(within(planRows[0]!).getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  expect(within(planRows[0]!).getByText("FRM-AUD-002")).toBeInTheDocument();
});

test("Add plan POSTs to the selected programme (date + process + checklist ref)", async () => {
  grant(["audit.plan"]);
  let body: Record<string, unknown> | null = null;
  let target = "";
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", async ({ request, params }) => {
      target = String(params.id);
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "pl-new-00-0000-0000-0000-000000000000", program_id: target, auditee_process_id: null, lead_auditor_user_id: null, scheduled_date: "2026-11-01", checklist_ref: "FRM-AUD-002", created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/Scheduled date/), "2026-11-01");
  await u.click(within(dialog).getByLabelText(/Auditee process/));
  await u.click(await screen.findByRole("option", { name: "Purchasing" }));
  await u.type(within(dialog).getByLabelText(/Checklist ref/), "FRM-AUD-002");
  await u.click(within(dialog).getByRole("button", { name: /Save plan/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(target).toBe("ap000001-0001-0001-0001-000000000001");
  expect(body!["scheduled_date"]).toBe("2026-11-01");
  expect(body!["auditee_process_id"]).toBe("pr000001-0001-0001-0001-000000000001");
  expect(body!["checklist_ref"]).toBe("FRM-AUD-002");
});

test("an archived selected programme hides Add plan; a racing 409 surfaces calmly", async () => {
  grant(["audit.plan"]);
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  // Select the archived programme → no Add plan.
  await u.click(await screen.findByText("AUDPROG-000002"));
  expect(screen.queryByRole("button", { name: /Add plan/ })).toBeNull();
  // Back on the active one, a server 409 (race: archived elsewhere) renders calmly in the modal.
  await u.click(screen.getByText("AUDPROG-000001"));
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", () =>
      HttpResponse.json(
        { code: "program_archived", title: "Cannot add a plan to an archived programme" },
        { status: 409 },
      ),
    ),
  );
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Save plan/ }));
  expect(
    await within(dialog).findByText(/Cannot add a plan to an archived programme/),
  ).toBeInTheDocument();
});

test("the process picker is omitted when GET /processes 403s (degrade)", async () => {
  grant(["audit.plan"]);
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Scheduled date/)).toBeInTheDocument();
  expect(within(dialog).queryByLabelText(/Auditee process/)).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/ProgrammePage.test.tsx`
Expected: the 4 new tests FAIL (no plans section / no `PlanForm`).

- [ ] **Step 3: Implement `apps/web/src/features/audits/PlanForm.tsx`**:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { useProcesses } from "./hooks";
import { useCreatePlan } from "./mutations";

// Add a plan to a programme. The process picker rides process.read (omitted on 403 — degrade);
// the lead picker rides the user directory (omitted when empty). Date = plain YYYY-MM-DD input.
export function PlanForm({
  programId,
  opened,
  onClose,
}: {
  programId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [date, setDate] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);
  const [leadId, setLeadId] = useState<string | null>(null);
  const [checklistRef, setChecklistRef] = useState("");
  const processes = useProcesses();
  const { data: directory } = useUserDirectory();
  const create = useCreatePlan(programId);

  function submit() {
    create.mutate(
      {
        ...(date ? { scheduled_date: date } : {}),
        ...(processId ? { auditee_process_id: processId } : {}),
        ...(leadId ? { lead_auditor_user_id: leadId } : {}),
        ...(checklistRef.trim() ? { checklist_ref: checklistRef.trim() } : {}),
      },
      { onSuccess: onClose },
    );
  }

  const processRows = processes.forbidden ? [] : (processes.data ?? []);
  const directoryRows = directory ?? [];

  return (
    <Modal opened={opened} onClose={onClose} title="Add plan">
      <Stack gap="sm">
        <TextInput
          label="Scheduled date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.currentTarget.value)}
        />
        {processRows.length > 0 && (
          <Select
            label="Auditee process"
            data={processRows.map((p) => ({ value: p.id, label: p.name }))}
            value={processId}
            onChange={setProcessId}
            clearable
          />
        )}
        {directoryRows.length > 0 && (
          <Select
            label="Lead auditor"
            data={directoryRows.map((u) => ({ value: u.id, label: u.display_name }))}
            value={leadId}
            onChange={setLeadId}
            clearable
          />
        )}
        <TextInput
          label="Checklist ref"
          placeholder="e.g. FRM-AUD-002"
          value={checklistRef}
          onChange={(e) => setChecklistRef(e.currentTarget.value)}
        />
        {create.isError && (
          <Alert color="red" title="Couldn't save the plan">
            {create.error instanceof ApiError ? create.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={create.isPending}>
            Save plan
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Mount the plans section in `ProgrammePage.tsx`** (replacing the Task-10 placeholder comment). Add imports: `useAuditPlans`, `useProcesses`, `useUserDirectory`, `PlanForm`, types; add state `const [planFormOpen, setPlanFormOpen] = useState(false);` and the hooks `const plans = useAuditPlans(selected?.id ?? null); const processes = useProcesses(); const { data: directory } = useUserDirectory();`. Section JSX:

```tsx
      {selected && (
        <>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Plans — {selected.identifier}</Title>
            {can("audit.plan") && !selected.archived && (
              <Button variant="light" onClick={() => setPlanFormOpen(true)}>
                ＋ Add plan
              </Button>
            )}
          </Group>
          {(plans.data ?? []).length === 0 ? (
            <Text c="dimmed">No plans in this programme yet.</Text>
          ) : (
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Scheduled</Table.Th>
                  <Table.Th>Auditee process</Table.Th>
                  <Table.Th>Lead auditor</Table.Th>
                  <Table.Th>Checklist ref</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(plans.data ?? []).map((p) => (
                  <Table.Tr key={p.id}>
                    <Table.Td>{p.scheduled_date ?? "—"}</Table.Td>
                    <Table.Td>
                      {p.auditee_process_id
                        ? ((processes.data ?? []).find((x) => x.id === p.auditee_process_id)?.name ??
                          `${p.auditee_process_id.slice(0, 8)}…`)
                        : "—"}
                    </Table.Td>
                    <Table.Td>
                      {p.lead_auditor_user_id
                        ? ((directory ?? []).find((u) => u.id === p.lead_auditor_user_id)
                            ?.display_name ?? `${p.lead_auditor_user_id.slice(0, 8)}…`)
                        : "—"}
                    </Table.Td>
                    <Table.Td>{p.checklist_ref ?? "—"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {planFormOpen && (
            <PlanForm programId={selected.id} opened onClose={() => setPlanFormOpen(false)} />
          )}
        </>
      )}
```

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/ProgrammePage.test.tsx`
Expected: PASS (all 9).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/PlanForm.tsx apps/web/src/features/audits/ProgrammePage.tsx apps/web/src/features/audits/ProgrammePage.test.tsx
git commit -m "feat(s-web-7d): per-programme plans table + Add-plan form (process/lead pickers degrade)"
```

---

### Task 12: `AuditDetailPage` skeleton + `App.tsx` route wiring

**Files:**
- Create: `apps/web/src/features/audits/AuditDetailPage.tsx`
- Test: `apps/web/src/features/audits/AuditDetailPage.test.tsx`
- Modify: `apps/web/src/App.tsx` (the /audits routes — all three pages now exist)

The page hosts `AuditLifecyclePanel` (Task 13) + `FindingsCard` (Task 14) — to keep THIS task
green standalone, the page renders the header/meta/plan-context only; Tasks 13/14 mount their
panels into the marked grid slots.

- [ ] **Step 1: Write the failing tests** (`AuditDetailPage.test.tsx`):

```tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuditDetailPage } from "./AuditDetailPage";

function harness(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/audits/:id" element={<AuditDetailPage />} />
    </Routes>,
    { route: `/audits/${id}` },
  );
}

test("renders header (identifier · title · state) + plan/programme context", async () => {
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText("REC-000061")).toBeInTheDocument();
  expect(screen.getByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  // getAllBy: once Task 13 mounts the stepper, "● In progress" appears twice (badge + current node).
  expect(screen.getAllByText(/● In progress/).length).toBeGreaterThan(0);
  expect(screen.getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  // Plan context: scheduled date + checklist ref + auditee process + the programme title.
  expect(await screen.findByText(/2026-05-28/)).toBeInTheDocument();
  expect(screen.getByText(/FRM-AUD-002/)).toBeInTheDocument();
  expect(screen.getByText(/Purchasing$/)).toBeInTheDocument();
  expect(screen.getByText(/2026 Internal Audit Programme/)).toBeInTheDocument();
});

test("404 → a calm not-found panel", async () => {
  harness("au-missing-0000-0000-0000-000000000000");
  expect(await screen.findByText(/Audit not found/)).toBeInTheDocument();
});

test("403 → a calm no-access panel", async () => {
  server.use(
    http.get("/api/v1/audits/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText(/don't have access to internal audits/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = harness("au000001-0001-0001-0001-000000000001");
  await screen.findByText("REC-000061");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditDetailPage.test.tsx`
Expected: FAIL — `./AuditDetailPage` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/AuditDetailPage.tsx`**:

```tsx
import {
  Alert, Anchor, Breadcrumbs, Container, Grid, Group, Loader, Paper, Text, Title,
} from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { AuditStateBadge } from "./badges";
import { useAudit, useAuditPlan, useAuditPrograms, useProcesses } from "./hooks";

// The /audits/:id destination (outside the tab layout — the documents/:id precedent). Hosts the
// plan/programme context card + (Tasks 13/14) the lifecycle panel and the findings card. The FSM
// write scope is the plan's auditee process (SYSTEM fallback) — resolved HERE and passed down.
export function AuditDetailPage() {
  const { id } = useParams<{ id: string }>();
  const audit = useAudit(id ?? null);
  const plan = useAuditPlan(audit.data?.plan_id ?? null);
  const programs = useAuditPrograms(); // cached list — programme title lookup, no extra endpoint
  const processes = useProcesses();
  const { data: directory } = useUserDirectory();

  if (audit.forbidden) {
    return (
      <Container size="xl" py="md">
        <Alert color="gray" title="No access">
          You don't have access to internal audits. They're available to roles holding{" "}
          <code>audit.read</code>.
        </Alert>
      </Container>
    );
  }
  if (audit.isLoading) {
    return (
      <Container size="xl" py="md">
        <Loader />
      </Container>
    );
  }
  if (audit.isError || !audit.data) {
    return (
      <Container size="xl" py="md">
        <Alert color="gray" title="Audit not found">
          This audit doesn't exist or was removed.{" "}
          <Anchor component={Link} to="/audits">
            Back to audits
          </Anchor>
        </Alert>
      </Container>
    );
  }

  const a = audit.data;
  const p = plan.data ?? null;
  const programTitle = p
    ? ((programs.data ?? []).find((x) => x.id === p.program_id)?.title ?? null)
    : null;
  const processName = p?.auditee_process_id
    ? ((processes.data ?? []).find((x) => x.id === p.auditee_process_id)?.name ??
      `${p.auditee_process_id.slice(0, 8)}…`)
    : null;
  const lead = a.lead_auditor_user_id
    ? ((directory ?? []).find((u) => u.id === a.lead_auditor_user_id)?.display_name ??
      `${a.lead_auditor_user_id.slice(0, 8)}…`)
    : "—";
  // The FSM/finding write scope (the _audit_scope mirror): PROCESS when the auditee is set, else SYSTEM.
  const scope: { level: string; id?: string } = p?.auditee_process_id
    ? { level: "PROCESS", id: p.auditee_process_id }
    : { level: "SYSTEM" };

  return (
    <Container size="xl" py="md">
      <Breadcrumbs mb="sm">
        <Anchor component={Link} to="/audits">
          Internal Audit
        </Anchor>
        <Text>{a.identifier ?? a.id.slice(0, 8)}</Text>
      </Breadcrumbs>
      <Group justify="space-between" mb="md" align="flex-start">
        <div>
          <Text size="sm" c="dimmed">
            {a.identifier ?? a.id.slice(0, 8)}
          </Text>
          <Title order={3}>{a.title ?? "Internal audit"}</Title>
          <Text size="sm" c="dimmed">
            Lead auditor {lead}
            {a.started_at ? ` · started ${a.started_at}` : ""}
            {a.completed_at ? ` · completed ${a.completed_at}` : ""}
          </Text>
        </div>
        <AuditStateBadge state={a.state} />
      </Group>
      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 7 }}>
          {/* Task 14 mounts <FindingsCard audit={a} scope={scope} /> here. */}
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper withBorder p="md" mb="md">
            <Title order={5} mb="xs">
              Plan
            </Title>
            {p ? (
              <Text size="sm">
                {programTitle ? `${programTitle} · ` : ""}
                {p.scheduled_date ?? "unscheduled"}
                {p.checklist_ref ? ` · ${p.checklist_ref}` : ""}
                {processName ? ` · Auditee process ${processName}` : ""}
              </Text>
            ) : (
              <Text size="sm" c="dimmed">
                Plan unavailable.
              </Text>
            )}
          </Paper>
          {/* Task 13 mounts <AuditLifecyclePanel audit={a} scope={scope} /> here. */}
        </Grid.Col>
      </Grid>
    </Container>
  );
}
```

- [ ] **Step 4: Wire the routes** in `apps/web/src/App.tsx` — imports:

```tsx
import { AuditsLayout } from "./features/audits/AuditsLayout";
import { AuditsListPage } from "./features/audits/AuditsListPage";
import { AuditDetailPage } from "./features/audits/AuditDetailPage";
import { ProgrammePage } from "./features/audits/ProgrammePage";
```

Routes (after the `/capa` block, before `ingestion`):

```tsx
        <Route path="audits" element={<AuditsLayout />}>
          <Route index element={<AuditsListPage />} />
          <Route path="programme" element={<ProgrammePage />} />
        </Route>
        <Route path="audits/:id" element={<AuditDetailPage />} />
```

⚠ Route-order note: react-router v6 ranks the STATIC `programme` segment above the `:id` param
automatically, so `/audits/programme` resolves to the layout child, never to `AuditDetailPage` —
no extra guard needed (assert it in the test below).

Append a route-resolution test to `AuditsLayout.test.tsx` proving `/audits/programme` is NOT
swallowed by `:id` when both are mounted (mirror the real App.tsx nesting):

```tsx
test("the static programme route outranks /audits/:id (the route-shadow guard)", async () => {
  renderWithProviders(
    <Routes>
      <Route path="audits" element={<AuditsLayout />}>
        <Route index element={<div>AUDITS-FACE</div>} />
        <Route path="programme" element={<div>PROGRAMME-FACE</div>} />
      </Route>
      <Route path="audits/:id" element={<div>DETAIL-FACE</div>} />
    </Routes>,
    { route: "/audits/programme" },
  );
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
  expect(screen.queryByText("DETAIL-FACE")).toBeNull();
});
```

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditDetailPage.test.tsx src/features/audits/AuditsLayout.test.tsx`
Then: `npx tsc --noEmit` (App.tsx wiring).
Expected: PASS + clean tsc.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/AuditDetailPage.tsx apps/web/src/features/audits/AuditDetailPage.test.tsx apps/web/src/features/audits/AuditsLayout.test.tsx apps/web/src/App.tsx
git commit -m "feat(s-web-7d): audit detail page (header + plan context) + /audits route wiring"
```

---

### Task 13: `AuditLifecyclePanel` (stepper + the one legal Advance)

**Files:**
- Create: `apps/web/src/features/audits/AuditLifecyclePanel.tsx`
- Modify: `apps/web/src/features/audits/AuditDetailPage.tsx` (mount in the marked slot)
- Test: `apps/web/src/features/audits/AuditLifecyclePanel.test.tsx`

The 7b `AdvancePanel` shape: `usePermissions(scope)` once, a calm read-only line when the key is
absent, 409s surfaced calmly inline. The gate key SWAPS at the close phase (`audit.conduct` →
`audit.close`), per `NEXT_TRANSITION`.

- [ ] **Step 1: Write the failing tests** (`AuditLifecyclePanel.test.tsx`; reuse the `grant` helper):

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Audit } from "../../lib/types";
import { AuditLifecyclePanel } from "./AuditLifecyclePanel";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

const base: Audit = {
  id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061",
  title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001",
  lead_auditor_user_id: null, state: "InProgress", started_at: "2026-05-28",
  completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00",
};
const SYSTEM = { level: "SYSTEM" } as const;

test("renders the 7-node stepper with done/current/pending and aria-current on the current step", async () => {
  grant(["audit.conduct"]);
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  // The current node sits inside the aria-current="step" wrapper.
  const current = await screen.findByText(/● In progress/);
  expect(current.closest("[aria-current='step']")).not.toBeNull();
  // Done steps carry the ✓ glyph; pending the ○.
  expect(screen.getByText(/✓ Scheduled/)).toBeInTheDocument();
  expect(screen.getByText(/○ Reported/)).toBeInTheDocument();
});

test("offers exactly the one legal next transition, gated audit.conduct", async () => {
  grant(["audit.conduct"]);
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  expect(await screen.findByRole("button", { name: "Draft findings" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Issue report" })).toBeNull();
});

test("without the gate key → a calm read-only line, no button", async () => {
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  expect(
    await screen.findByText(/don't hold the permission to advance this audit/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Draft findings" })).toBeNull();
});

test("the close phase gates on audit.close, not audit.conduct", async () => {
  grant(["audit.conduct"]); // conduct alone must NOT show the close-phase action
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  expect(
    await screen.findByText(/don't hold the permission to advance this audit/),
  ).toBeInTheDocument();
  grant(["audit.close"]);
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  expect(await screen.findByRole("button", { name: "Close audit" })).toBeInTheDocument();
});

test("409 audit_close_blocked surfaces the server message calmly", async () => {
  grant(["audit.close"]);
  server.use(
    http.post("/api/v1/audits/:id/close", () =>
      HttpResponse.json(
        { code: "audit_close_blocked", title: "Cannot close: 1 live NC finding(s) without a Closed CAPA (close the CAPA, or correct the finding NC→Observation/OFI)" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  await u.click(await screen.findByRole("button", { name: "Close audit" }));
  expect(await screen.findByText(/Cannot close: 1 live NC finding/)).toBeInTheDocument();
});

test("a Closed audit shows the terminal line (no action)", async () => {
  grant(["audit.close"]);
  renderWithProviders(
    <AuditLifecyclePanel
      audit={{ ...base, state: "Closed", completed_at: "2026-06-01" }}
      scope={SYSTEM}
    />,
  );
  expect(await screen.findByText(/Audit closed/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditLifecyclePanel.test.tsx`
Expected: FAIL — `./AuditLifecyclePanel` does not exist.

- [ ] **Step 3: Implement `apps/web/src/features/audits/AuditLifecyclePanel.tsx`**:

```tsx
import { Alert, Button, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import type { Audit } from "../../lib/types";
import { AUDIT_STATE_LABEL, AUDIT_STATE_ORDER, NEXT_TRANSITION } from "./labels";
import { useAdvanceAudit } from "./mutations";

// The 7-node lifecycle stepper + the ONE legal next transition (the backend FSM is linear).
// Gate = NEXT_TRANSITION[state].gate (audit.conduct → audit.close at the close phase), asked at
// the audit's PROCESS scope (SYSTEM fallback) — the 7b AdvancePanel shape. The server is the
// authority: 409s (invalid_audit_transition / audit_close_blocked) render calmly inline.
export function AuditLifecyclePanel({
  audit,
  scope,
}: {
  audit: Audit;
  scope: { level: string; id?: string };
}) {
  const perms = usePermissions(scope);
  const advance = useAdvanceAudit(audit.id);
  const next = NEXT_TRANSITION[audit.state];
  const currentIdx = AUDIT_STATE_ORDER.indexOf(audit.state);

  return (
    <Paper withBorder p="md">
      <Title order={5} mb="sm">
        Lifecycle
      </Title>
      <Stack gap={4} mb="md">
        {AUDIT_STATE_ORDER.map((s, i) => {
          const glyph = i < currentIdx ? "✓" : i === currentIdx ? "●" : "○";
          return (
            <div key={s} aria-current={i === currentIdx ? "step" : undefined}>
              <Text size="sm" fw={i === currentIdx ? 700 : 400} c={i > currentIdx ? "dimmed" : undefined}>
                {glyph} {AUDIT_STATE_LABEL[s]}
              </Text>
            </div>
          );
        })}
      </Stack>
      {next === null ? (
        <Text size="sm" c="dimmed">
          Audit closed{audit.completed_at ? ` on ${audit.completed_at}` : ""}.
        </Text>
      ) : perms.isLoading ? (
        <Loader size="sm" />
      ) : !perms.can(next.gate) ? (
        <Text size="sm" c="dimmed">
          You don't hold the permission to advance this audit.
        </Text>
      ) : (
        <Stack gap="sm">
          {advance.isError && (
            <Alert
              color="orange"
              title={
                advance.error instanceof ApiError && advance.error.code === "audit_close_blocked"
                  ? "Close blocked"
                  : "Couldn't advance"
              }
            >
              {advance.error instanceof ApiError ? advance.error.message : "Please try again."}
            </Alert>
          )}
          <Button onClick={() => advance.mutate(next.path)} loading={advance.isPending}>
            {next.label}
          </Button>
        </Stack>
      )}
    </Paper>
  );
}
```

- [ ] **Step 4: Mount it** in `AuditDetailPage.tsx` (replace the Task-13 comment in the right column):

```tsx
          <AuditLifecyclePanel audit={a} scope={scope} />
```

(+ the import).

- [ ] **Step 5: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/AuditLifecyclePanel.test.tsx src/features/audits/AuditDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/audits/AuditLifecyclePanel.tsx apps/web/src/features/audits/AuditLifecyclePanel.test.tsx apps/web/src/features/audits/AuditDetailPage.tsx
git commit -m "feat(s-web-7d): lifecycle stepper + the one legal Advance (conduct/close gate swap, calm 409s)"
```

---

### Task 14: `FindingsCard` + `FindingPanel` (list · chain · CAPA link · close-readiness)

**Files:**
- Create: `apps/web/src/features/audits/FindingsCard.tsx`
- Create: `apps/web/src/features/audits/FindingPanel.tsx`
- Modify: `apps/web/src/features/audits/AuditDetailPage.tsx` (mount in the left slot)
- Tests: `apps/web/src/features/audits/FindingsCard.test.tsx` + `FindingPanel.test.tsx`

The modals land in Task 15 — HERE the card exposes `onLog`/`onCorrect` callbacks and the page
holds the modal state; Task 15 fills the modals in. To stay green standalone, Task 14 wires the
buttons to the callbacks and tests fire them (no modal yet).

Close-readiness derivation mirrors the backend `finding_blocks_close`: blocking = `finding_type
=== "NC"` AND `superseded_by_correction === null` AND its `auto_capa_id` CAPA's `close_state !==
"Closed"` (cross-ref `useCapas()`; degrade entirely when `capa.read` is missing — the 409 stays
the truth).

- [ ] **Step 1: Write the failing `FindingPanel` tests** (`FindingPanel.test.tsx`):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import type { Finding } from "../../lib/types";
import { FindingPanel } from "./FindingPanel";

function r(ui: React.ReactElement) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>{ui}</MemoryRouter>
    </MantineProvider>,
  );
}

const nc: Finding = {
  id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062",
  title: "Supplier re-evaluation overdue for 2 vendors",
  audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major",
  clause_ref: "8.4", process_ref: "Purchasing",
  auto_capa_id: "ca000001-0001-0001-0001-000000000001",
  correction_of: null, superseded_by_correction: null,
};

test("a live NC renders badge + title + tags + the CAPA state chip + the deep-link", () => {
  r(<FindingPanel finding={nc} capaState="RootCause" canCorrect onCorrect={() => {}} />);
  expect(screen.getByText("REC-000062")).toBeInTheDocument();
  expect(screen.getByText(/⚑ Major NC/)).toBeInTheDocument();
  expect(screen.getByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  expect(screen.getByText("8.4")).toBeInTheDocument();
  expect(screen.getByText(/CAPA: Root cause/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View CAPA/ })).toHaveAttribute(
    "href",
    "/capa?capa=ca000001-0001-0001-0001-000000000001",
  );
});

test("the CAPA chip is omitted when capaState is undefined (capa.read degrade) — the link stays", () => {
  r(<FindingPanel finding={nc} capaState={undefined} canCorrect={false} onCorrect={() => {}} />);
  expect(screen.queryByText(/CAPA:/)).toBeNull();
  expect(screen.getByRole("link", { name: /View CAPA/ })).toBeInTheDocument();
});

test("a superseded finding renders muted with no Correct action", () => {
  r(
    <FindingPanel
      finding={{ ...nc, superseded_by_correction: "fd000004-0004-0004-0004-000000000004" }}
      capaState="Closed"
      canCorrect
      onCorrect={() => {}}
    />,
  );
  expect(screen.getByText(/Superseded by correction/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Correct/ })).toBeNull();
});

test("a successor shows its corrects-link; Correct fires the callback when allowed", async () => {
  const onCorrect = vi.fn();
  const u = userEvent.setup();
  r(
    <FindingPanel
      finding={{ ...nc, finding_type: "OBSERVATION", severity: null, auto_capa_id: null, correction_of: "fd000003-0003-0003-0003-000000000003" }}
      capaState={undefined}
      canCorrect
      onCorrect={onCorrect}
    />,
  );
  expect(screen.getByText(/Corrects an earlier finding/)).toBeInTheDocument();
  await u.click(screen.getByRole("button", { name: /Correct/ }));
  expect(onCorrect).toHaveBeenCalled();
});

test("a finding title with markup renders as literal text (XSS-safe)", () => {
  r(
    <FindingPanel
      finding={{ ...nc, title: "<img src=x onerror=alert(1)>" }}
      capaState={undefined}
      canCorrect={false}
      onCorrect={() => {}}
    />,
  );
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
});
```

- [ ] **Step 2: Write the failing `FindingsCard` tests** (`FindingsCard.test.tsx`; the `grant` helper again):

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Audit } from "../../lib/types";
import { FindingsCard } from "./FindingsCard";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

const audit: Audit = {
  id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061",
  title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001",
  lead_auditor_user_id: null, state: "InProgress", started_at: "2026-05-28",
  completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00",
};
const SYSTEM = { level: "SYSTEM" } as const;
const noop = { onLog: () => {}, onCorrect: () => {} };

test("renders the findings created-asc with per-row content; Log gated on finding.create", async () => {
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect(await screen.findByText("REC-000062")).toBeInTheDocument();
  expect(screen.getByText(/Findings \(4\)/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Log finding/ })).toBeNull();
  grant(["finding.create"]);
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect((await screen.findAllByRole("button", { name: /Log finding/ })).length).toBeGreaterThan(0);
});

test("Closed audit: Log/Correct hidden with the closed note", async () => {
  grant(["finding.create"]);
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closed" }} scope={SYSTEM} {...noop} />,
  );
  expect(await screen.findByText(/closed with the audit/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Log finding/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Correct/ })).toBeNull();
});

test("close-readiness note: Reported/Closing + 1 blocking NC (live, CAPA not Closed)", async () => {
  // Fixtures: fd000001 NC live, its CAPA ca000001 is RootCause → 1 blocker. The corrected NC
  // (fd000003, superseded) and the OFI must NOT count.
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closing" }} scope={SYSTEM} {...noop} />,
  );
  expect(
    await screen.findByText(/1 live NC finding without a Closed CAPA — closing will be blocked/),
  ).toBeInTheDocument();
});

test("the note is omitted pre-Reported, and when capa.read is denied (degrade)", async () => {
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />); // InProgress
  await screen.findByText("REC-000062");
  expect(screen.queryByText(/closing will be blocked/)).toBeNull();
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closing" }} scope={SYSTEM} {...noop} />,
  );
  await screen.findByText("REC-000062");
  expect(screen.queryByText(/closing will be blocked/)).toBeNull();
});

test("finding.read denied → a calm no-access note inside the card", async () => {
  server.use(
    http.get("/api/v1/audits/:id/findings", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect(await screen.findByText(/don't have access to findings/)).toBeInTheDocument();
});

test("per-row CAPA chips come from the capa cross-ref; Correct fires onCorrect", async () => {
  grant(["finding.create"]);
  const onCorrect = vi.fn();
  const u = userEvent.setup();
  renderWithProviders(
    <FindingsCard audit={audit} scope={SYSTEM} onLog={() => {}} onCorrect={onCorrect} />,
  );
  const row = (await screen.findByText("REC-000062")).closest("[data-finding]") as HTMLElement;
  expect(within(row).getByText(/CAPA: Root cause/)).toBeInTheDocument();
  await u.click(within(row).getByRole("button", { name: /Correct/ }));
  expect(onCorrect).toHaveBeenCalledWith(expect.objectContaining({ id: "fd000001-0001-0001-0001-000000000001" }));
});
```

- [ ] **Step 3: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/FindingPanel.test.tsx src/features/audits/FindingsCard.test.tsx`
Expected: FAIL — modules don't exist.

- [ ] **Step 4: Implement `apps/web/src/features/audits/FindingPanel.tsx`**:

```tsx
import { Anchor, Badge, Button, Group, Paper, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { CapaCloseState, Finding } from "../../lib/types";
import { FindingTypeBadge } from "./badges";

// One finding. capaState: the cross-ref'd CAPA close_state (undefined = capa.read denied →
// chip omitted; the deep-link always renders — the board page enforces its own access).
const CAPA_STATE_LABEL: Record<CapaCloseState, string> = {
  Raised: "Raised", Containment: "Containment", RootCause: "Root cause",
  ActionPlan: "Action plan", Implement: "Implement", Verify: "Verify",
  Closed: "Closed", Rejected: "Rejected",
};

export function FindingPanel({
  finding,
  capaState,
  canCorrect,
  onCorrect,
}: {
  finding: Finding;
  capaState: CapaCloseState | undefined;
  canCorrect: boolean;
  onCorrect: (finding: Finding) => void;
}) {
  const superseded = finding.superseded_by_correction !== null;
  return (
    <Paper withBorder p="sm" data-finding style={{ opacity: superseded ? 0.6 : 1 }}>
      <Group justify="space-between" mb={4}>
        <Text size="sm" fw={600}>
          {finding.identifier ?? finding.id.slice(0, 8)}
        </Text>
        <FindingTypeBadge type={finding.finding_type} severity={finding.severity} />
      </Group>
      <Text size="sm" mb={4}>
        {finding.title ?? "—"}
      </Text>
      <Group gap="xs" mb={4}>
        {finding.clause_ref && <Badge variant="outline" color="gray">{finding.clause_ref}</Badge>}
        {finding.process_ref && (
          <Badge variant="outline" color="gray">
            {finding.process_ref}
          </Badge>
        )}
      </Group>
      {superseded && (
        <Text size="xs" c="dimmed">
          ✕ Superseded by correction
        </Text>
      )}
      {finding.correction_of && (
        <Text size="xs" c="dimmed">
          ↪ Corrects an earlier finding
        </Text>
      )}
      <Group justify="space-between" mt={4}>
        <Group gap="xs">
          {finding.auto_capa_id && capaState !== undefined && (
            <Badge variant="light" color={capaState === "Closed" ? "green" : "orange"}>
              CAPA: {CAPA_STATE_LABEL[capaState]}
            </Badge>
          )}
          {finding.auto_capa_id && (
            <Anchor component={Link} size="sm" to={`/capa?capa=${finding.auto_capa_id}`}>
              View CAPA →
            </Anchor>
          )}
        </Group>
        {canCorrect && !superseded && (
          <Button size="xs" variant="subtle" onClick={() => onCorrect(finding)}>
            Correct
          </Button>
        )}
      </Group>
    </Paper>
  );
}
```

NOTE: `CapaCloseState` already exists in `lib/types.ts` (7a).

- [ ] **Step 5: Implement `apps/web/src/features/audits/FindingsCard.tsx`**:

```tsx
import { Alert, Button, Group, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Audit, CapaCloseState, Finding } from "../../lib/types";
import { useCapas } from "../capa/hooks";
import { FindingPanel } from "./FindingPanel";
import { useFindings } from "./hooks";

// blocking mirrors the backend finding_blocks_close: a LIVE (non-superseded) NC whose auto-CAPA
// is not Closed. Advisory only — the server 409 is the authority (degrade without capa.read).
function isBlocking(f: Finding, capaStates: Map<string, CapaCloseState>): boolean {
  if (f.finding_type !== "NC" || f.superseded_by_correction !== null) return false;
  if (!f.auto_capa_id) return true;
  return capaStates.get(f.auto_capa_id) !== "Closed";
}

export function FindingsCard({
  audit,
  scope,
  onLog,
  onCorrect,
}: {
  audit: Audit;
  scope: { level: string; id?: string };
  onLog: () => void;
  onCorrect: (finding: Finding) => void;
}) {
  const findings = useFindings(audit.id);
  const perms = usePermissions(scope);
  const capas = useCapas(); // cross-ref for the per-NC CAPA state chips; degrades on 403
  const closed = audit.state === "Closed";
  const canCreate = !perms.isLoading && perms.can("finding.create");

  if (findings.forbidden) {
    return (
      <Paper withBorder p="md">
        <Title order={5} mb="xs">
          Findings
        </Title>
        <Text size="sm" c="dimmed">
          You don't have access to findings (<code>finding.read</code>).
        </Text>
      </Paper>
    );
  }
  if (findings.isLoading) {
    return (
      <Paper withBorder p="md">
        <Loader size="sm" />
      </Paper>
    );
  }

  const rows = findings.data ?? [];
  const capaStates = new Map<string, CapaCloseState>(
    (capas.forbidden ? [] : (capas.data ?? [])).map((c) => [c.id, c.close_state]),
  );
  const blocking =
    capas.forbidden ? 0 : rows.filter((f) => isBlocking(f, capaStates)).length;
  const showReadiness =
    !capas.forbidden && (audit.state === "Reported" || audit.state === "Closing") && blocking > 0;

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="sm">
        <Title order={5}>Findings ({rows.length})</Title>
        {canCreate && !closed && (
          <Button size="xs" variant="light" onClick={onLog}>
            ＋ Log finding
          </Button>
        )}
      </Group>
      {closed && (
        <Text size="sm" c="dimmed" mb="sm">
          Findings are closed with the audit.
        </Text>
      )}
      {showReadiness && (
        <Alert color="orange" mb="sm" title="Close readiness">
          {blocking} live NC finding{blocking === 1 ? "" : "s"} without a Closed CAPA — closing
          will be blocked. Close the CAPA, or correct the finding to Observation/OFI.
        </Alert>
      )}
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed">
          No findings logged yet.
        </Text>
      ) : (
        <Stack gap="sm">
          {rows.map((f) => (
            <FindingPanel
              key={f.id}
              finding={f}
              capaState={
                f.auto_capa_id && !capas.forbidden ? capaStates.get(f.auto_capa_id) : undefined
              }
              canCorrect={canCreate && !closed}
              onCorrect={onCorrect}
            />
          ))}
        </Stack>
      )}
    </Paper>
  );
}
```

- [ ] **Step 6: Mount it in `AuditDetailPage.tsx`** (replace the Task-14 comment in the left
column). The modal STATE arrives with the modals in Task 15 — here the callbacks are no-ops, so no
unused-local sneaks in:

```tsx
          <FindingsCard audit={a} scope={scope} onLog={() => {}} onCorrect={() => {}} />
```

(+ the `FindingsCard` import. Task 15 replaces the two no-op callbacks with real state setters.)

- [ ] **Step 7: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/FindingPanel.test.tsx src/features/audits/FindingsCard.test.tsx src/features/audits/AuditDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/features/audits/FindingPanel.tsx apps/web/src/features/audits/FindingsCard.tsx apps/web/src/features/audits/FindingPanel.test.tsx apps/web/src/features/audits/FindingsCard.test.tsx apps/web/src/features/audits/AuditDetailPage.tsx
git commit -m "feat(s-web-7d): findings card + panels — correction chain, CAPA cross-ref, close-readiness note"
```

---

### Task 15: `LogFindingModal` + `CorrectFindingModal`

**Files:**
- Create: `apps/web/src/features/audits/LogFindingModal.tsx`
- Create: `apps/web/src/features/audits/CorrectFindingModal.tsx`
- Modify: `apps/web/src/features/audits/AuditDetailPage.tsx` (replace the no-op fragment with the mounts)
- Tests: `apps/web/src/features/audits/LogFindingModal.test.tsx` + `CorrectFindingModal.test.tsx`

NC severity rule (both modals): the confirm button is DISABLED while `finding_type === "NC"` and
no severity is picked (the 7c SpawnCapaModal no-dead-end lesson) — AND the server 422 still
renders calmly if it slips through.

- [ ] **Step 1: Write the failing `LogFindingModal` tests**:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LogFindingModal } from "./LogFindingModal";

const AUDIT_ID = "au000001-0001-0001-0001-000000000001";

test("an NC requires a severity (disabled until picked); POSTs the body", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.post("/api/v1/audits/:id/findings", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "fd-new", identifier: "REC-000070", title: String(body!["summary"]), audit_id: AUDIT_ID, finding_type: "NC", severity: "Major", clause_ref: "8.4", process_ref: null, auto_capa_id: "ca-auto-1", correction_of: null, superseded_by_correction: null },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  expect(within(dialog).getByRole("button", { name: /Log finding/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.type(within(dialog).getByLabelText(/Summary/), "Re-evaluation overdue");
  await u.type(within(dialog).getByLabelText(/Clause ref/), "8.4");
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!["finding_type"]).toBe("NC");
  expect(body!["severity"]).toBe("Major");
  expect(body!["summary"]).toBe("Re-evaluation overdue");
});

test("an NC success shows the auto-CAPA confirmation with the deep-link", async () => {
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  // The default handler returns createdNcFindingFixture (auto_capa_id set).
  expect(await within(dialog).findByText(/CAPA auto-created/)).toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: /View CAPA/ })).toHaveAttribute(
    "href",
    "/capa?capa=ca-auto-00-0000-0000-0000-000000000000",
  );
});

test("an OBSERVATION needs no severity and closes on success", async () => {
  let closed = false;
  server.use(
    http.post("/api/v1/audits/:id/findings", () =>
      HttpResponse.json(
        { id: "fd-obs", identifier: "REC-000071", title: "Obs", audit_id: AUDIT_ID, finding_type: "OBSERVATION", severity: null, clause_ref: null, process_ref: null, auto_capa_id: null, correction_of: null, superseded_by_correction: null },
        { status: 201 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => (closed = true)} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "Observation" }));
  expect(within(dialog).getByRole("button", { name: /Log finding/ })).toBeEnabled();
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  await waitFor(() => expect(closed).toBe(true));
});

test("a server error (409 audit closed) renders calmly in the modal", async () => {
  server.use(
    http.post("/api/v1/audits/:id/findings", () =>
      HttpResponse.json(
        { code: "audit_finding_audit_closed", title: "Cannot add a finding to a Closed audit" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "OFI" }));
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  expect(await within(dialog).findByText(/Cannot add a finding to a Closed audit/)).toBeInTheDocument();
});

test("no axe violations with the modal open (the spec §9 modal-open gate)", async () => {
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  expect(await axe(dialog)).toHaveNoViolations();
});
```

(add `import { axe } from "jest-axe";` to the test file's imports).

- [ ] **Step 2: Write the failing `CorrectFindingModal` tests**:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Finding } from "../../lib/types";
import { CorrectFindingModal } from "./CorrectFindingModal";

const nc: Finding = {
  id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062",
  title: "Supplier re-evaluation overdue for 2 vendors",
  audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major",
  clause_ref: "8.4", process_ref: "Purchasing",
  auto_capa_id: "ca000001-0001-0001-0001-000000000001",
  correction_of: null, superseded_by_correction: null,
};

test("pre-fills from the finding; retype NC→OFI POSTs the correction with a reason", async () => {
  let body: Record<string, unknown> | null = null;
  let path = "";
  server.use(
    http.post("/api/v1/findings/:id/correction", async ({ request, params }) => {
      path = String(params.id);
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "fd-corr", identifier: "REC-000072", title: "Declassified", audit_id: nc.audit_id, finding_type: "OFI", severity: null, clause_ref: "8.4", process_ref: "Purchasing", auto_capa_id: null, correction_of: nc.id, superseded_by_correction: null },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal finding={nc} auditId={nc.audit_id} opened onClose={() => {}} />,
  );
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Clause ref/)).toHaveValue("8.4");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "OFI" }));
  await u.type(within(dialog).getByLabelText(/Reason/), "Declassified");
  await u.click(within(dialog).getByRole("button", { name: /Save correction/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(path).toBe(nc.id);
  expect(body!["finding_type"]).toBe("OFI");
  expect(body!["reason"]).toBe("Declassified");
});

test("retype TO NC requires a severity (disabled until picked)", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal
      finding={{ ...nc, finding_type: "OBSERVATION", severity: null, auto_capa_id: null }}
      auditId={nc.audit_id}
      opened
      onClose={() => {}}
    />,
  );
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  expect(within(dialog).getByRole("button", { name: /Save correction/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  expect(within(dialog).getByRole("button", { name: /Save correction/ })).toBeEnabled();
});

test("409 finding_already_corrected renders calmly", async () => {
  server.use(
    http.post("/api/v1/findings/:id/correction", () =>
      HttpResponse.json(
        { code: "finding_already_corrected", title: "This finding is already superseded" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal finding={nc} auditId={nc.audit_id} opened onClose={() => {}} />,
  );
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Save correction/ }));
  expect(await within(dialog).findByText(/already superseded/)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run to verify failure**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/LogFindingModal.test.tsx src/features/audits/CorrectFindingModal.test.tsx`
Expected: FAIL — modules don't exist.

- [ ] **Step 4: Implement `apps/web/src/features/audits/LogFindingModal.tsx`**:

```tsx
import { Alert, Anchor, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import type { Finding, FindingType, NcSeverity } from "../../lib/types";
import { useCreateFinding } from "./mutations";

const TYPE_OPTIONS = [
  { value: "NC", label: "NC" },
  { value: "OBSERVATION", label: "Observation" },
  { value: "OFI", label: "OFI" },
];
const SEVERITY_OPTIONS = ["Critical", "Major", "Minor"].map((s) => ({ value: s, label: s }));

// An NC REQUIRES a severity (the backend 422s; the auto-CAPA needs one) — the confirm stays
// disabled until picked (the 7c SpawnCapaModal no-dead-end lesson). An NC success shows the
// auto-created CAPA confirmation + deep-link instead of silently closing.
export function LogFindingModal({
  auditId,
  opened,
  onClose,
}: {
  auditId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [type, setType] = useState<FindingType | null>(null);
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [summary, setSummary] = useState("");
  const [clauseRef, setClauseRef] = useState("");
  const [processRef, setProcessRef] = useState("");
  const [created, setCreated] = useState<Finding | null>(null);
  const create = useCreateFinding(auditId);

  const ncWithoutSeverity = type === "NC" && severity === null;

  function submit() {
    if (!type || ncWithoutSeverity) return;
    create.mutate(
      {
        finding_type: type,
        ...(severity ? { severity } : {}),
        ...(summary.trim() ? { summary: summary.trim() } : {}),
        ...(clauseRef.trim() ? { clause_ref: clauseRef.trim() } : {}),
        ...(processRef.trim() ? { process_ref: processRef.trim() } : {}),
      },
      {
        onSuccess: (f) => {
          if (f.auto_capa_id) setCreated(f); // NC → show the CAPA confirmation
          else onClose();
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Log finding">
      {created ? (
        <Stack gap="sm">
          <Alert color="green" title="Finding logged">
            <Text size="sm" mb="xs">
              {created.identifier} — CAPA auto-created for this NC.
            </Text>
            <Anchor component={Link} to={`/capa?capa=${created.auto_capa_id}`}>
              View CAPA →
            </Anchor>
          </Alert>
          <Group justify="flex-end">
            <Button onClick={onClose}>Done</Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap="sm">
          <Select label="Type" required data={TYPE_OPTIONS} value={type} onChange={(v) => setType(v as FindingType | null)} />
          <Select
            label={type === "NC" ? "Severity (required for an NC)" : "Severity"}
            data={SEVERITY_OPTIONS}
            value={severity}
            onChange={(v) => setSeverity(v as NcSeverity | null)}
            clearable
          />
          <TextInput label="Summary" maxLength={300} value={summary} onChange={(e) => setSummary(e.currentTarget.value)} />
          <TextInput label="Clause ref" placeholder="e.g. 8.4" value={clauseRef} onChange={(e) => setClauseRef(e.currentTarget.value)} />
          <TextInput label="Process ref" value={processRef} onChange={(e) => setProcessRef(e.currentTarget.value)} />
          {create.isError && (
            <Alert color="red" title="Couldn't log the finding">
              {create.error instanceof ApiError ? create.error.message : "Please try again."}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!type || ncWithoutSeverity} loading={create.isPending}>
              Log finding
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
```

- [ ] **Step 5: Implement `apps/web/src/features/audits/CorrectFindingModal.tsx`**:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Finding, FindingType, NcSeverity } from "../../lib/types";
import { useCorrectFinding } from "./mutations";

const TYPE_OPTIONS = [
  { value: "NC", label: "NC" },
  { value: "OBSERVATION", label: "Observation" },
  { value: "OFI", label: "OFI" },
];
const SEVERITY_OPTIONS = ["Critical", "Major", "Minor"].map((s) => ({ value: s, label: s }));

// Correct-don't-edit: a retype in ANY direction captures a superseding successor. Pre-filled from
// the original; a retype TO NC requires a severity (422 otherwise — disabled until picked).
export function CorrectFindingModal({
  finding,
  auditId,
  opened,
  onClose,
}: {
  finding: Finding;
  auditId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [type, setType] = useState<FindingType>(finding.finding_type);
  const [severity, setSeverity] = useState<NcSeverity | null>(finding.severity);
  const [clauseRef, setClauseRef] = useState(finding.clause_ref ?? "");
  const [processRef, setProcessRef] = useState(finding.process_ref ?? "");
  const [reason, setReason] = useState("");
  const correct = useCorrectFinding(auditId);

  const ncWithoutSeverity = type === "NC" && severity === null;

  function submit() {
    if (ncWithoutSeverity) return;
    correct.mutate(
      {
        findingId: finding.id,
        body: {
          finding_type: type,
          ...(severity ? { severity } : {}),
          ...(clauseRef.trim() ? { clause_ref: clauseRef.trim() } : {}),
          ...(processRef.trim() ? { process_ref: processRef.trim() } : {}),
          ...(reason.trim() ? { reason: reason.trim() } : {}),
        },
      },
      { onSuccess: onClose },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Correct ${finding.identifier ?? "finding"}`}>
      <Stack gap="sm">
        <Select label="Type" required data={TYPE_OPTIONS} value={type} onChange={(v) => v && setType(v as FindingType)} />
        <Select
          label={type === "NC" ? "Severity (required for an NC)" : "Severity"}
          data={SEVERITY_OPTIONS}
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          clearable
        />
        <TextInput label="Clause ref" value={clauseRef} onChange={(e) => setClauseRef(e.currentTarget.value)} />
        <TextInput label="Process ref" value={processRef} onChange={(e) => setProcessRef(e.currentTarget.value)} />
        <TextInput label="Reason" maxLength={300} value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
        {correct.isError && (
          <Alert color="red" title="Couldn't correct the finding">
            {correct.error instanceof ApiError ? correct.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={ncWithoutSeverity} loading={correct.isPending}>
            Save correction
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 6: Mount the modals** in `AuditDetailPage.tsx` — add the state (top of the component,
with the other hooks):

```tsx
  const [logOpen, setLogOpen] = useState(false);
  const [correcting, setCorrecting] = useState<Finding | null>(null);
```

Replace Task 14's no-op callbacks on the `FindingsCard` mount:

```tsx
          <FindingsCard
            audit={a}
            scope={scope}
            onLog={() => setLogOpen(true)}
            onCorrect={setCorrecting}
          />
```

And mount the modals before the closing `</Container>`:

```tsx
      <LogFindingModal auditId={a.id} opened={logOpen} onClose={() => setLogOpen(false)} />
      {correcting && (
        <CorrectFindingModal
          key={correcting.id}
          finding={correcting}
          auditId={a.id}
          opened
          onClose={() => setCorrecting(null)}
        />
      )}
```

(+ imports: `useState`, `Finding` type, `LogFindingModal`, `CorrectFindingModal`.)

- [ ] **Step 7: Run to verify pass**

Run: `npm test -- --pool=forks --poolOptions.forks.singleFork=true src/features/audits/LogFindingModal.test.tsx src/features/audits/CorrectFindingModal.test.tsx src/features/audits/AuditDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/features/audits/LogFindingModal.tsx apps/web/src/features/audits/CorrectFindingModal.tsx apps/web/src/features/audits/LogFindingModal.test.tsx apps/web/src/features/audits/CorrectFindingModal.test.tsx apps/web/src/features/audits/AuditDetailPage.tsx
git commit -m "feat(s-web-7d): log/correct finding modals — NC-severity no-dead-end, auto-CAPA confirmation"
```

---

### Task 16: Full verification gate

**Files:** none (verification only).

- [ ] **Step 1: Full web gate**

Run from `apps/web`:
`npx eslint . && npx tsc --noEmit && npm run build && npm test -- --pool=forks --poolOptions.forks.singleFork=true`
Expected: all clean; the suite grows from 429 to ~490+ — note the exact count for the PR body.

- [ ] **Step 2: API static gates + contracts**

Run: `/check-api` (ruff + format + mypy-strict + unit where runnable) and `/check-contracts`.
Expected: clean (integration tests prove the enrichment in Linux CI).

- [ ] **Step 3: Fix anything that surfaced, then commit**

```bash
git add -A
git commit -m "chore(s-web-7d): full-gate fixes (lint/tsc/build/test sweep)"
```

(Skip the commit if the sweep was already clean.)

---

## Post-plan workflow (not plan tasks — the session driver runs these)

1. `diff-critic` agent on the branch diff (false-PASS hunting; fixtures-vs-serializer, scope gating, XSS).
2. Pre-merge live smoke (grant `demo` SYSTEM overrides of `audit.read audit.plan audit.create audit.conduct audit.close finding.create finding.read capa.read`, org `AHT`): programme → plan → audit → walk to FindingsDraft → log Major NC → View CAPA deep-link opens the board drawer → Reported → Closing → Close blocked (409 calm) → correct NC→OFI → Close succeeds. Rebuild images first (`docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build api web`).
3. PR → green CI (5 jobs) → address every Codex thread (reply + resolve via `gh api`, path WITHOUT a leading slash) → squash-merge.
4. Update `docs/slice-history.md` + CLAUDE.md Current status in-PR.
