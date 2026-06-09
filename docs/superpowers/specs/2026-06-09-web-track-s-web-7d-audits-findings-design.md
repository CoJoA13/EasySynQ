# S-web-7d — Audits & findings (slice design)

> **Status:** owner forks resolved (2026-06-09); spec pending owner review. **Track:** web-UI.
> **Epic:** S-web-7 Nonconformity & CAPA front door
> (`docs/superpowers/specs/2026-06-08-web-track-s-web-7-nc-capa-design.md`), PR 4 of 4 — the LAST slice.
> **Depends on:** 7a (#101) + 7b (#102) + 7c (#103) — all shipped (the `?capa=<id>` board deep-link seam
> from 7c is the finding→CAPA hand-off). **Closes:** the CHECK-phase internal-audit loop in the SPA
> (mockup `#screen-audit`) — programs/plans/audits + lifecycle, findings with NC→auto-CAPA, and the
> R39 block-until-corrected close gate.

## 0. Owner forks (resolved 2026-06-09, via AskUserQuestion)

1. **One cohesive PR** (not 7d-i/7d-ii) — the close gate is load-bearing for the whole module; the
   headline demo (NC → auto-CAPA → blocked close) needs both halves; per-task TDD commits keep the
   large diff reviewable.
2. **Thin read-enrichment for audits AND findings** (§4) — the exact 7a `_capa` precedent. Without it
   the audits table is UUID rows and finding cards have no text.
3. **Full page `/audits/:id`** for the audit detail (findings + lifecycle + close gate) — the
   `documents/:id` precedent; this much interaction outgrows a drawer.
4. **Programme tab with tables** (no calendar strip) — programs + per-program plans as honest tables;
   the mockup's 12-month strip + coverage-% tile have no backing data worth building against in v1.

## 1. Why / what

The CAPA board (7a/7b) and the complaint/NCR intake (7c) cover the ACT phase; the third and largest
CAPA inlet — **internal audits** (ISO 9001 Cl 9.2, CHECK phase) — has a complete backend (S-aud-1/2:
programmes, plans, audits-as-records, the 7-state FSM, findings with the mandatory NC→CAPA auto-link,
correction/retype, and the R39 close gate) and **no UI**. This slice surfaces it:

- **Audit module** — programmes + plans (the maintained schedule) · audits list/create · the 6
  lifecycle transitions with a stepper + one contextual Advance action.
- **Findings** — log against an audit (NC requires severity; NC auto-creates a linked CAPA →
  `auto_capa_id` → "View CAPA" deep-links the 7c `?capa=<id>` board drawer) · correct/retype in any
  direction (correct-don't-edit; a successor record supersedes the original).
- **The close gate** — `POST /audits/{id}/close` 409s `audit_close_blocked` while any live NC finding
  lacks a Closed CAPA; surfaced calmly, with an honest client-side "close readiness" derivation.

**Backend change: ONE thin read-enrichment** (§4) — serializer field-adds only. **No migration (head
stays `0044`), no new permission key, no new endpoint.** Everything else is already shipped + contracted
(`packages/contracts/openapi.yaml:2448-2790`).

## 2. Architecture — a new CHECK-phase front door at `/audits`

```
<Route path="audits" element={<AuditsLayout/>}>      # tab strip (Audits · Programme) + <Outlet/>
  <Route index            element={<AuditsListPage/>}/>
  <Route path="programme" element={<ProgrammePage/>}/>
</Route>
<Route path="audits/:id" element={<AuditDetailPage/>}/>   # full page, outside the tab layout
```

- **`LeftRail`** gains one **unconditional** entry "Internal Audit" → `/audits` (active
  `pathname.startsWith("/audits")`) — the CAPA-entry precedent: core QMS areas stay discoverable; the
  page itself renders a **calm no-access panel** for callers without `audit.read` (the S-web-6 pattern).
- **`AuditsLayout`** mirrors 7c's `CapaLayout`: a secondary tab strip (Audits · Programme), active tab
  from `useLocation().pathname`, no title of its own (each page keeps its own). Both tabs deep-linkable.
- **`AuditDetailPage`** sits outside the layout (like `documents/:id` vs the library) — it is a
  destination, not a tab.
- New code in **`features/audits/`**; dep direction `audits → capa (deep-link URL + CAPA state
  cross-ref types only) → lib`, acyclic. Nothing in `features/capa/` changes.

## 3. Verified backend surface (pin every fixture to THESE shapes)

All citations `apps/api/src/easysynq_api/api/audits.py` + `services/audits/service.py`. The serializers
are the runtime truth — pin MSW fixtures to them, never the mockup (the #1 false-PASS lesson).

### 3.1 Serializers (current → §4 enriches two of them)

- **`_program`** (`audits.py:94`): `id · identifier (AUDPROG-NNN) · title · period (string|null) ·
  coverage (object|null, free-form) · archived (bool) · created_at (ISO)`.
- **`_plan`** (`audits.py:106`): `id · program_id · auditee_process_id (string|null) ·
  lead_auditor_user_id (string|null) · scheduled_date (date ISO|null) · checklist_ref (string|null) ·
  created_at (ISO)`.
- **`_audit`** (`audits.py:118`): `id · plan_id · lead_auditor_user_id (string|null) · state
  (AuditState) · started_at (date ISO|null) · completed_at (date ISO|null) · result_summary
  (string|null — NEVER written in v1, no endpoint sets it)`. **No identifier, no title, no created_at**
  → §4. An audit is a `kind=RECORD` shared-PK subtype; identifier/title/created_at live on the
  `documented_information` base row, which `list_audits` (`repository.py:75`) does not join.
- **`_finding`** (`audits.py:130`): `id · identifier (string|null, REC-…) · audit_id · finding_type
  (FindingType) · severity (NcSeverity|null) · clause_ref (string|null) · process_ref (string|null) ·
  auto_capa_id (string|null) · correction_of (string|null) · superseded_by_correction (string|null)`.
  **No summary text** — the logged summary is stored as the record's title (`service.py:363`) and the
  `_finding_select` join (`repository.py:86`) doesn't select it → §4.

### 3.2 Enums (`db/models/_iso_audit_enums.py`)

- `AuditState` = `Scheduled → Planned → InProgress → FindingsDraft → Reported → Closing → Closed`
  (a linear forward chain; no skips, no backward moves).
- `FindingType` = `NC | OBSERVATION | OFI`. Finding severity reuses `NcSeverity`
  (`Critical | Major | Minor`).

### 3.3 Endpoints + gates

| Endpoint | Gate (scope) | Notes |
|----------|--------------|-------|
| `POST /audit-programs` | `audit.plan` (SYSTEM) | ← `{title* (1..300), period? (≤100), coverage? (object)}` → 201 |
| `GET /audit-programs` | `audit.read` | → `{data: Program[]}`, org-scoped, newest-first |
| `GET /audit-programs/{id}` | `audit.read` | 404 cross-org |
| `PATCH /audit-programs/{id}` | `audit.plan` | all-optional `{title?, period?, coverage?, archived?}` |
| `POST /audit-programs/{id}/plans` | `audit.plan` | ← `{auditee_process_id?, lead_auditor_user_id?, scheduled_date?, checklist_ref? (≤300)}` → 201; **409 `program_archived`**; 404 bad process |
| `GET /audit-programs/{id}/plans` | `audit.read` | → `{data: Plan[]}`, newest-first |
| `GET /audit-plans/{id}` | `audit.read` | |
| `POST /audits` | `audit.create` (SYSTEM) | ← `{plan_id*, title? (1..300), lead_auditor_user_id?}` → 201 at `Scheduled`; lead defaults to the plan's; title defaults `"Internal Audit (<scheduled_date|unscheduled>)"` |
| `GET /audits` | `audit.read` | → `{data: Audit[]}` (NO server ordering — sort client-side) |
| `GET /audits/{id}` | `audit.read` | |
| `POST /audits/{id}/plan` · `/conduct` · `/draft-findings` · `/report` | `audit.conduct` (PROCESS via the plan's `auditee_process_id`; SYSTEM fallback when unset) | each = one forward FSM step; **409 `invalid_audit_transition`** (message names the next legal state) |
| `POST /audits/{id}/begin-closing` · `/close` | `audit.close` (PROCESS, same resolver) | `/close` runs the gate: **409 `audit_close_blocked`** ("Cannot close: N live NC finding(s) without a Closed CAPA (close the CAPA, or correct the finding NC→Observation/OFI)") |
| `POST /audits/{id}/findings` | `finding.create` (PROCESS via the audit) | ← `{finding_type*, severity (REQUIRED for NC → 422 `validation_error` else), clause_ref? (≤100), process_ref? (≤300), summary? (1..300)}` → 201; **409 `audit_finding_audit_closed`** once Closed; an NC auto-creates its CAPA in the same txn (`auto_capa_id` set on the response) |
| `GET /audits/{id}/findings` | `finding.read` | → `{data: Finding[]}`, created-asc |
| `GET /findings/{id}` | `finding.read` | |
| `POST /findings/{id}/correction` | `finding.create` (PROCESS via the finding's audit) | ← `{finding_type*, severity?, clause_ref?, process_ref?, reason? (≤300)}` → 201 **successor** (`correction_of` = original); original's `superseded_by_correction` set; clause/process refs inherit when omitted; retype TO NC needs severity (422) + auto-creates its CAPA; **409 `finding_already_corrected`**, **409 `audit_finding_audit_closed`** |

Misc: `advance_audit` stamps `started_at` on →InProgress and `completed_at` on →Closed. The audit's
own audit-trail events ride `object_type=record` (the audit IS a record). No endpoint takes an
`Idempotency-Key` (no server replay latch) — mutations guard double-submit by disabled-while-pending
only.

### 3.4 Permission keys + who holds them (`migrations/versions/0004_seed_authz.py`)

| Key | Seeded role holders | `demo` (System Admin)? |
|-----|---------------------|------------------------|
| `audit.read` | QMS-Owner, Process-Owner, Internal-Auditor | **No** |
| `finding.read` | QMS-Owner, Process-Owner, Internal-Auditor | **No** |
| `audit.plan` | QMS-Owner | **No** |
| `audit.create` | Internal-Auditor | **No** |
| `audit.conduct` / `audit.close` / `finding.create` | Internal-Auditor (PROCESS-scoped, `:assigned_process`) | **No** |
| `finding.link_capa` | Internal-Auditor | **No** — and **no v1 endpoint exercises it** (the CAPA link is auto-only) → no UI |

Consequences:
- **`demo` calm-403s everywhere** (the board precedent). Live smoke: grant `demo` SYSTEM overrides of
  `audit.read audit.plan audit.create audit.conduct audit.close finding.create finding.read capa.read`
  (org `AHT`) — one admin drives the whole loop (no SoD on the audit FSM).
- The Internal-Auditor PROCESS keys use the `:assigned_process` placeholder — concrete bindings land
  with owner-assignment, so **in v1 practice the FSM writes ride SYSTEM overrides** (the epic §5.2 /
  7b posture). The UI still queries `usePermissions` at the resource's true scope (PROCESS when the
  plan's `auditee_process_id` is set, SYSTEM otherwise — the `_audit_scope` mirror), so it is correct
  under both postures.

### 3.5 Supporting reads (existing, reused)

- **`useUserDirectory()`** (`app/shell/useUserDirectory.ts` → `GET /directory/users`) — lead-auditor
  picker options + id→name resolution, **degrade to a short raw id** when empty/denied (the 7c
  authorizer pattern).
- **`GET /processes`** (gate `process.read` — held by all three audit personas) — the plan form's
  auditee-process picker + process-name resolution; **degrade to absent/raw-id** on 403.
- **`GET /capas`** (gate `capa.read`) — the close-readiness cross-ref (§6.5): finding `auto_capa_id` →
  CAPA `close_state`. Degrade: without `capa.read` the per-NC CAPA-state chip is omitted and the close
  gate is server-message-only.

## 4. The one backend change — thin read-enrichment (the 7a §4 precedent)

1. **`_audit` gains `identifier`, `title`, `created_at`** (list + detail): `list_audits` becomes a
   3-column join to `DocumentedInformation` (same PK — zero-cost, the `list_capas` shape);
   `get_audit_endpoint` reads the base row (`repository.get_identifier` generalized to return the
   base row, or one `session.get(DocumentedInformation, id)`). The serializer takes them as args.
2. **`_finding` gains `title`** (the logged summary / correction reason): `_finding_select`
   **already joins `DocumentedInformation`** — add `DocumentedInformation.title` to the select; thread
   through `FindingRow`. Zero new queries anywhere.
3. **`openapi.yaml`**: field-adds to the `Audit` + `Finding` schemas (`/check-contracts` gates it).

No migration. No new key. No new endpoint. Gated locally by `/check-api` static (ruff/format/mypy
— the api test suites are Linux-CI-only on this box) + `/check-contracts`; ~2 CI integration tests
assert the new fields on the live serializers (the 7b precedent).

> Rationale (same as 7a): without it the audits table reads `3f2a… · InProgress` — no human label —
> and finding panels have no text at all (the summary IS the record title). This is the difference
> between a usable module and a code-only one.

## 5. Components (all new, under `apps/web/src/features/audits/`)

| File | Role |
|------|------|
| `AuditsLayout.tsx` | tab strip (Audits · Programme) + `<Outlet/>`; active from pathname (the `CapaLayout` shape) |
| `AuditsListPage.tsx` | calm-403 on `audit.read` · honest tiles (Total / Active / Closed — client-computed; **Active = `state ≠ Closed`**) · segmented **All / Active / Closed** filter (same definition) · audits table (Identifier · Title · Lead [directory, degrade-to-id] · State badge · Started/Created) sorted newest-first client-side · row → `/audits/:id` · "New audit" (gated `audit.create`) |
| `NewAuditModal.tsx` | programme `Select` → that programme's plans `Select` (label: scheduled_date · process name · checklist_ref) + optional title + optional lead (directory picker) → `POST /audits`; calm empty-state pointing at the Programme tab when no plans exist |
| `ProgrammePage.tsx` | programmes table (Identifier · Title · Period · Plans · Archived badge · Created) + "New programme" (gated `audit.plan`) + per-row Edit/Archive (gated `audit.plan`) + selected-programme **plans table** (row-click selects; newest programme selected by default) (Scheduled · Process [name, degrade] · Lead [directory] · Checklist ref · Created) + "Add plan" (gated `audit.plan`; archived programme → button hidden + 409 `program_archived` surfaced calmly) |
| `ProgramForm.tsx` | create/edit modal: `title*` · `period` (+ archive toggle on edit); `coverage` NOT exposed (free-form dict, no honest form) |
| `PlanForm.tsx` | add-plan modal: scheduled date · auditee process `Select` (from `GET /processes`, omitted on 403) · lead auditor `Select` (directory, omitted when empty) · checklist ref |
| `AuditDetailPage.tsx` | header (identifier · title · `AuditStateBadge` · lead · created/started/completed) + plan/programme context card (plan via `GET /audit-plans/{plan_id}`; programme title from the cached programmes list) + `AuditLifecyclePanel` + `FindingsCard`; calm-403; 404 → calm not-found |
| `AuditLifecyclePanel.tsx` | the 7-node vertical stepper (done/current/pending, DP-7 non-color glyph+label) + **one contextual Advance action** = the single legal next transition, gated `audit.conduct` (plan/conduct/draft-findings/report) or `audit.close` (begin-closing/close) at the audit's PROCESS scope (SYSTEM fallback) — the 7b `AdvancePanel` shape: a calm read-only line when the key is absent; 409s surfaced calmly inline (`invalid_audit_transition` refetches — stale state) |
| `FindingsCard.tsx` | findings list (created-asc) + "Log finding" (gated `finding.create`, hidden-with-note once Closed) + the §6.5 close-readiness note; calm no-access note when `finding.read` is missing but `audit.read` held |
| `FindingPanel.tsx` | one finding: identifier · `FindingTypeBadge` (type + severity) · title (summary) · clause/process tags · CAPA affordance (`auto_capa_id` → CAPA state chip [cross-ref, degrade] + "View CAPA →" `/capa?capa=<id>`) · correction chain (a superseded finding renders muted "Superseded by <id>"; a successor shows "Corrects <id>") · "Correct" action (gated `finding.create`, live findings only) |
| `LogFindingModal.tsx` | `finding_type*` Select · severity Select (**required iff NC** — client-validated AND the server 422 surfaced calmly) · summary (≤300) · clause_ref · process_ref; on NC success → inline "CAPA auto-created → View CAPA" |
| `CorrectFindingModal.tsx` | pre-filled retype modal (any direction) + reason; to-NC requires severity; 409 `finding_already_corrected` / `audit_finding_audit_closed` calm |
| `AuditStateBadge.tsx` / `FindingTypeBadge.tsx` | DP-7 glyph+label badges (distinct accessible names — the duplicate-`aria-label` trap) |
| `hooks.ts` / `mutations.ts` | §5.1/§5.2 |
| `fixtures.ts` (test) | MSW fixtures pinned to §3.1 + §4 shapes |

### 5.1 Hooks (`hooks.ts` — the `forbidden`-flag + `retry:false` idiom)

`useAuditPrograms()` · `useAuditPlans(programId)` · `useAudits()` · `useAudit(id)` ·
`useAuditPlan(planId)` · `useFindings(auditId)` — each → its §3.3 read; 403 → `forbidden`.
Cross-ref reads reused from existing features: `useUserDirectory()`, a thin `useProcesses()`
(new, `GET /processes`, degrade), and the capa list hook for close-readiness.

### 5.2 Mutations (`mutations.ts` — invalidate + refetch, never optimistic)

- `useCreateProgram()` / `useUpdateProgram(id)` → invalidate `["audit-programs"]`.
- `useCreatePlan(programId)` → invalidate `["audit-plans", programId]`.
- `useCreateAudit()` → invalidate `["audits"]`.
- `useAdvanceAudit(id)` → POST the transition sub-resource; invalidate `["audits"]` + `["audit", id]`.
- `useCreateFinding(auditId)` / `useCorrectFinding(findingId)` → invalidate `["findings", auditId]`
  (+ `["capas"]` when the response carries an `auto_capa_id` — the board must see the new CAPA).

## 6. The five flows

### 6.1 New audit (gate `audit.create`)
Programme Select → plans of that programme → optional title/lead → `POST /audits` → 201 at
`Scheduled` → navigate to `/audits/:id`. No plans anywhere → calm guidance to the Programme tab.

### 6.2 Programme & plan upkeep (gate `audit.plan`)
Create/edit/archive programmes; add plans. An archived programme hides "Add plan" (and a racing
409 `program_archived` is calm). Plans are create-only in v1 (no PATCH/DELETE endpoint exists).

### 6.3 Walk the FSM (gates `audit.conduct` / `audit.close`)
The stepper shows all 7 states; the Advance action offers exactly the one legal next transition with
an honest label ("Finalize plan" → Planned, "Begin fieldwork" → InProgress, "Draft findings" →
FindingsDraft, "Issue report" → Reported, "Begin closing" → Closing, "Close audit" → Closed). The two
close-phase transitions swap to the `audit.close` gate. A 409 `invalid_audit_transition` (stale tab)
surfaces calmly + refetches.

### 6.4 Log / correct findings (gate `finding.create`)
Log: type + severity-iff-NC + summary/clause/process. An NC response carries `auto_capa_id` → the
panel + a success note link "View CAPA →" (`/capa?capa=<auto_capa_id>` — the 7c deep-link seam; also
invalidate `["capas"]`). Correct: any-direction retype on a LIVE finding (correct-don't-edit) — the
successor appears (created-asc), the original renders muted/superseded. NC→OBS/OFI declassifies (the
close gate clears); OBS/OFI→NC creates the successor's CAPA.

### 6.5 Close + the R39 gate (server-authoritative, honestly mirrored)
"Close audit" is always enabled when `audit.close` is held and the state is `Closing` — the server is
the gate. A 409 `audit_close_blocked` renders the server message calmly. The UI ALSO derives a
client-side **close-readiness note** (rendered in `FindingsCard` whenever the state is `Reported` or
`Closing`): blocking = live (non-superseded) NC findings whose `auto_capa_id` CAPA is not
`close_state=Closed`, cross-ref'd from the org CAPA list (`capa.read`; **degrade**: without it, omit
the per-finding CAPA chips and the note's count — the 409 message remains the truth). The note names
the two honest remediations (close the CAPA / correct the finding NC→OFI), mirroring the server text.

## 7. Cross-cutting decisions (inherited from the epic §5)

1. **Fixtures pinned to §3 + §4** — `{data:[]}` envelopes; exact serializer fields incl. the enriched
   ones; `satisfies <Type>` so strict tsc enforces shape.
2. **Gating** per-key at the resource's scope: SYSTEM keys (`audit.plan`/`audit.create`) at SYSTEM;
   FSM + finding writes at the audit's PROCESS scope with SYSTEM fallback (the 7b scope-aware
   `usePermissions` pattern). Never render a write affordance the caller can't exercise.
3. **Server-only truths surfaced calmly**: `audit_close_blocked`, `invalid_audit_transition`,
   `audit_finding_audit_closed`, `finding_already_corrected`, `program_archived`, the NC-severity 422.
4. **Calm-403 / no-access** per face; nav stays discoverable.
5. **Honest tiles/affordances only**: Total/Active/Closed from the list. The mockup's coverage-%,
   open-findings-org-wide, and NC→CAPA-closure-% tiles are **dropped** (no backing data without N+1);
   `result_summary` is never written in v1 → not rendered; `coverage` (free-form dict) not exposed.
6. **All user text rendered XSS-safely** (titles, summaries, periods, checklist refs — Mantine `Text`
   / text nodes, never `dangerouslySetInnerHTML`).
7. **Degrade-gracefully on auxiliary reads** (directory, processes, capas) — never hard-depend.

## 8. Types (`apps/web/src/lib/types.ts` — extend the S-web-7 block)

```ts
export type AuditState =
  | "Scheduled" | "Planned" | "InProgress" | "FindingsDraft"
  | "Reported" | "Closing" | "Closed";
export type FindingType = "NC" | "OBSERVATION" | "OFI";

export interface AuditProgram {
  id: string; identifier: string; title: string;
  period: string | null; coverage: Record<string, unknown> | null;
  archived: boolean; created_at: string;
}
export interface AuditPlan {
  id: string; program_id: string;
  auditee_process_id: string | null; lead_auditor_user_id: string | null;
  scheduled_date: string | null; checklist_ref: string | null; created_at: string;
}
export interface Audit {
  id: string; identifier: string | null; title: string | null;   // §4 enrichment
  plan_id: string; lead_auditor_user_id: string | null;
  state: AuditState; started_at: string | null; completed_at: string | null;
  result_summary: string | null; created_at: string;              // §4 enrichment
}
export interface Finding {
  id: string; identifier: string | null; title: string | null;    // §4 enrichment
  audit_id: string; finding_type: FindingType; severity: NcSeverity | null;
  clause_ref: string | null; process_ref: string | null;
  auto_capa_id: string | null;
  correction_of: string | null; superseded_by_correction: string | null;
}
// request bodies
export interface AuditProgramCreateBody { title: string; period?: string; }
export interface AuditProgramUpdateBody {
  title?: string; period?: string; archived?: boolean;
}
export interface AuditPlanCreateBody {
  auditee_process_id?: string; lead_auditor_user_id?: string;
  scheduled_date?: string; checklist_ref?: string;
}
export interface AuditCreateBody {
  plan_id: string; title?: string; lead_auditor_user_id?: string;
}
export interface FindingCreateBody {
  finding_type: FindingType; severity?: NcSeverity;
  clause_ref?: string; process_ref?: string; summary?: string;
}
export interface FindingCorrectionBody {
  finding_type: FindingType; severity?: NcSeverity;
  clause_ref?: string; process_ref?: string; reason?: string;
}
```

Plus `{data: T[]}` list wrappers and the audit-transition path map
(`Scheduled→"plan"`, `Planned→"conduct"`, `InProgress→"draft-findings"`, `FindingsDraft→"report"`,
`Reported→"begin-closing"`, `Closing→"close"`).

## 9. Testing (vitest + MSW + jest-axe; fixtures pinned to §3 + §4)

- **Nav/routes** — the LeftRail entry; `/audits` tabs; `/audits/:id` deep-link; calm-403 per face.
- **List page** — tiles math; All/Active/Closed filter; table render (lead degrade-to-id);
  newest-first sort; New-audit gating.
- **New audit** — programme→plan cascade; empty-state; POST + navigate.
- **Programme** — programmes/plans tables; create/edit/archive; `program_archived` 409 calm; the
  plan form's process/lead pickers degrade (403 directory/processes).
- **Detail page** — header + plan context; 404 calm; per-state stepper nodes.
- **Lifecycle** — exactly one legal Advance per state; conduct-vs-close gate swap; the read-only line
  when the key is absent; `invalid_audit_transition` calm + refetch.
- **Findings** — list render (title + tags + type badges, distinct accessible names); NC severity
  required (client + 422 calm); NC success links `/capa?capa=<id>` + invalidates `["capas"]`;
  correction chain render (superseded muted, successor labelled); `finding_already_corrected` calm;
  Closed-audit log/correct hidden-with-note.
- **Close gate** — `audit_close_blocked` 409 calm with the server message; the close-readiness note's
  blocking derivation (NC+live+CAPA-not-Closed; a superseded NC and an OBS don't block; degrade
  without `capa.read`).
- **XSS** — a `<script>` in an audit title / finding summary renders as literal text.
- **jest-axe release gates** on the list page, programme page, detail page (incl. modals open).
- Run the full **`/check-web`** before the PR (the cross-file-drift lesson).

Estimated **~14 TDD tasks / +60–80 web tests** (suite currently 429).

## 10. Out of scope

- The mockup's **calendar strip**, coverage-% / org-wide-open-findings / NC→CAPA-closure-% tiles
  (unbacked), and the "Audit Program POL-QMS-009" controlled-document chip (no such linkage exists).
- `result_summary` (never written in v1) · `coverage` editing/display · plan edit/delete (no endpoint).
- `finding.link_capa` UI (no v1 endpoint exercises the key; the CAPA link is auto-only).
- The audit/finding **record-detail** surfaces (`GET /documents/{id}` etc. — the records family owns
  those) and the per-audit audit-trail tab (`system.audit_log.read`, SYSTEM-gated).
- Org-wide findings metrics (N+1 across audits — no aggregate endpoint).
- Any migration / new key / new endpoint beyond the §4 serializer field-adds.

## 11. Risks / watch-items

- **Wrong-shape fixture** → §3/§4 pin to the serializers; verify against `apps/api`, not the mockup.
- **PROCESS-scope gating** → `usePermissions` must query at the plan's auditee-process scope for
  FSM/finding writes (SYSTEM fallback when unset) — the S-pack-1 R28 lesson, SPA side.
- **Close-readiness honesty** → the client derivation is advisory; the 409 is the truth. Degrade
  without `capa.read`; never fake a "ready" signal.
- **Findings-list identity** → finding panels are looped components: keep accessible names distinct
  per finding (the S-web-6/7b duplicate-`aria-label` trap).
- **Enrichment is list+detail-consistent** → both `GET /audits` and `GET /audits/{id}` must carry the
  new fields (the 7a precedent enriched both; a detail-only enrichment makes the list a UUID table).
- **Live smoke** (pre-merge, the 7c lesson): grant `demo` the §3.4 SYSTEM overrides (org `AHT`) →
  programme → plan → audit → walk to FindingsDraft → log a Major NC (auto-CAPA → View CAPA deep-link
  → 7c drawer) → Reported → Closing → **Close blocked (409, calm)** → correct NC→OFI (declassify) →
  **Close succeeds**. One user, exercises every flow incl. the gate + correction.
