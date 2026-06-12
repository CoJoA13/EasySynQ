# S-obj-2 — Quality Objectives UI (clause 6.2) — design

> Status: approved design (2026-06-11). The trailing, **front-end-only** slice that completes the
> Quality Objectives family opened by S-obj-1 (PR #115, mig `0049`, R44). Mirrors the S-ack-2
> front-end-only trailing-slice shape. Implementation plan to follow under
> `docs/superpowers/plans/`.

## 1. Context & scope

S-obj-1 shipped the Quality Objectives **backend** (clause 6.2): `quality_objective` as a
`kind=DOCUMENT` shared-PK subtype (type `OBJ`), `objective_plan`, append-only `KPI_READING`
measurements rolled latest-period-wins into `current_value`, and a direction-aware + amber RAG
computed at read. The 8-endpoint `/api/v1/objectives*` surface is live and already documented in
`packages/contracts/openapi.yaml`.

S-obj-2 builds the **web UI** that consumes that surface: a PLAN-phase **register** (Mara's
surface), a **detail page** (commitment + plans + measurement history), and the **create** +
**record-measurement** write affordances. It is **front-end-only**:

- **No** migration, **no** new permission key (rides the already-seeded `objective.*`/`kpi.*`
  keys — catalog stays 100), **no** new endpoint, **no** contract change. The OpenAPI paths and
  schemas already exist from S-obj-1; `contracts` CI is unaffected.

Slice position: after S-obj-2 lands, the **PDCA dashboard** (Home) becomes buildable — it needs
both acknowledgements (✅ S-ack) and objectives (✅ S-obj), now both shipped. The dashboard is the
**next** slice, not this one; `HomePage.tsx` stays a placeholder until then.

## 2. Non-goals / named deferrals (carry forward — name, don't fake)

- **Objective lifecycle (approve/release).** S-obj-1 creates objectives as **Draft**, and the
  objectives router has **no** approve/release endpoint (an OBJ is a `kind=DOCUMENT` subtype, so
  Draft→Effective would ride the existing document review/approve/release flow). S-obj-2 stays
  **create + plans + measure + read**. The `current_state` badge shows `Draft` honestly. The
  **6.2 ★ compliance-checklist node stays not-COVERED** (it requires an Effective objective) — a
  named deferral for a later slice that wires release.
- **KPI trend views / sparklines / SPC** — v1.x (N6 descriptive-only, no SPC/forecast). The
  measurement history is a **plain append-only table**, never a chart, in S-obj-2.
- **Per-process objective reads** — v1 SYSTEM-gates reads (matching the capa/audit precedent;
  Codex P2). The detail page may *refine* scope later; v1 gating is at `objective.read` (SYSTEM).
- **Commitment version-snapshot freeze** (target/unit/direction/due into
  `document_version.metadata_snapshot`) — v1.x. The history's `target_at_capture` column is the
  v1 honesty device for target drift.
- **PDCA dashboard (Home)** — the next slice.

## 3. Data contract — pinned to the real serializers

> ⚠ **The #1 false-PASS rule.** Pin every MSW fixture and TS type to the **as-built**
> `apps/api/src/easysynq_api/api/objectives.py` serializers below — **not** the spec-sketch
> shapes that float around the family design doc (those diverge: they used `rag_status` not
> `rag`, a bare array not `{data:[…]}`, a `{summary:{…}}` scorecard, and embedded `measurements`
> in detail — all wrong). Numerics are **decimal strings** everywhere except `pct_toward_target`
> (a JSON **number**|null). Use `satisfies <Type>` on fixtures so strict `tsc` enforces the shape.

### 3.1 Endpoints (verbatim, `apps/api/src/easysynq_api/api/objectives.py`)

| # | Method + path | Gate (scope) | Request | Response |
|---|---|---|---|---|
| 1 | `POST /api/v1/objectives` | `objective.manage` (PROCESS from body `process_id`, SYSTEM fallback) | `ObjectiveCreate` | `201` → `Objective` |
| 2 | `GET /api/v1/objectives?process_id=` | `objective.read` (org-scoped query) | — | `200` → `{ data: Objective[] }` |
| 3 | `GET /api/v1/objectives/scorecard?process_id=` | `objective.read` | — | `200` → `ObjectiveScorecard` |
| 4 | `GET /api/v1/objectives/{id}` | `objective.read` | — | `200` → `Objective` (with `plans[]`); `404` if absent |
| 5 | `POST /api/v1/objectives/{id}/measurements` | `kpi.record` (PROCESS via `_objective_scope`) | `MeasurementCreate` | `201` → `Measurement` (**not** wrapped); `404`/`422` |
| 6 | `GET /api/v1/objectives/{id}/measurements` | `kpi.read` (PROCESS) | — | `200` → `{ data: Measurement[] }` |
| 7 | `POST /api/v1/objectives/{id}/plans` | `objective.manage` (PROCESS) | `PlanCreate` | `201` → `ObjectivePlan` (**not** wrapped); `404` |
| 8 | `DELETE /api/v1/objectives/{id}/plans/{plan_id}` | `objective.manage` (PROCESS) | — | `204` empty body; `404` |

Notes that bite:
- **`/objectives/scorecard` accepts `?process_id=`** (server-side filter) — so the register's
  process filter is a query param, not a client-side recompute. Same `process_id` filter on
  `GET /objectives`.
- The scorecard's `objectives[]` rows carry **empty `plans: []`** (plans are loaded only on the
  single-objective GET). The detail GET is the only response with populated `plans`.
- Measurements never appear inside the objective serializer — always the separate endpoint #6.
- POST measurement / POST plan return the bare object (`201`), **not** `{data:…}`.
- `404` on a missing objective is returned **after** the auth gate passes (the gate's
  `_objective_scope` silently falls back to SYSTEM on a bad/absent id — it never 404s itself).

### 3.2 Serializer field shapes (verbatim) → TypeScript types (`lib/types.ts` additions)

```ts
export type ObjectiveDirection = "HIGHER_IS_BETTER" | "LOWER_IS_BETTER";
export type ObjectiveRag = "green" | "amber" | "red" | "unmeasured";
export type ObjectiveAttainment = "in_progress" | "met" | "missed";
// current_state mirrors DocumentedInformation.current_state (the 7 canonical vault states):
export type DocumentCurrentState =
  | "Draft" | "InReview" | "Approved" | "Effective"
  | "UnderRevision" | "Superseded" | "Obsolete";

export interface Objective {
  id: string;
  identifier: string;            // e.g. "OBJ-001"
  title: string;
  current_state: DocumentCurrentState;
  target_value: string;          // decimal string
  unit: string;
  baseline_value: string | null; // decimal string | null
  current_value: string | null;  // decimal string | null (latest-period-wins rollup)
  direction: ObjectiveDirection;
  at_risk_threshold: string | null; // decimal string | null
  due_date: string;              // ISO date (YYYY-MM-DD)
  process_id: string | null;
  policy_id: string | null;
  rag: ObjectiveRag;             // always present (computed)
  pct_toward_target: number | null; // JSON number | null (NOT a string)
  attainment: ObjectiveAttainment;  // always present (computed)
  plans: ObjectivePlan[];        // [] in list/scorecard rows; populated on detail GET
}

export interface ObjectivePlan {
  id: string;
  objective_id: string;
  action: string;
  resource: string | null;
  responsible_user_id: string | null;
  due_date: string | null;       // ISO date | null
}

export interface Measurement {
  id: string;
  objective_id: string | null;
  record_id: string;             // underlying KPI_READING evidence record
  period: string;                // ISO date
  value: string;                 // decimal string
  target_at_capture: string;     // decimal string — target_value frozen at capture
  unit: string;
  source: string | null;
  created_at: string;            // ISO date-time
}

export interface ObjectiveScorecard {
  total: number;
  on_target: number;             // == by_rag.green
  by_rag: { green: number; amber: number; red: number; unmeasured: number };
  objectives: Objective[];       // full rows, plans: []
}

export interface ObjectiveListResponse { data: Objective[] }
export interface MeasurementListResponse { data: Measurement[] }

// request bodies
export interface ObjectiveCreate {
  title: string;                 // 1..300
  target_value: string;          // decimal string
  unit: string;                  // 1..50
  direction: ObjectiveDirection;
  due_date: string;              // ISO date
  baseline_value?: string | null;
  at_risk_threshold?: string | null;
  process_id?: string | null;
  policy_id?: string | null;
}
export interface MeasurementCreate {
  period: string;                // ISO date — REQUIRED by the backend
  value: string;                 // decimal string
  unit: string;                  // 1..50 — must equal the objective's unit (422 otherwise)
  source?: string | null;        // ..300
}
export interface PlanCreate {
  action: string;                // 1..2000
  resource?: string | null;      // ..500
  responsible_user_id?: string | null;
  due_date?: string | null;
}
```

### 3.3 The computation rules (for consistent UI labelling/colouring — never recomputed client-side)

The UI **reads** `rag` / `attainment` / `pct_toward_target` from the server; it never recomputes
them. It only needs to know what they mean to colour/label:

- **`rag`** (direction-aware, `domain/objectives/rules.py`): `unmeasured` when `current_value`
  is null; `green` when on-or-better than target; `amber` when within the at-risk band
  (requires `at_risk_threshold` set — else collapses to `red`); else `red`.
  - HIGHER: amber = `threshold ≤ current < target`. LOWER: amber = `target < current ≤ threshold`.
- **`pct_toward_target`** (number|null): direction-aware fraction from baseline→target. **May
  exceed 1.0** (overachievement) and is **null** when unmeasured, when span is zero, or for a
  LOWER objective with **no baseline**. The progress bar clamps the *fill* to [0,100]% but the
  numeric readout (if shown) prints the true value; a null renders as "—" / no bar.
- **`attainment`** (time-aware): `in_progress` before `due_date`; at/after due, `met` iff target
  reached else `missed` (null current at/after due ⇒ `missed`).

## 4. Authorization & gating

Permission keys (already seeded; PROCESS finest-scope + SYSTEM fallback in v1):

| Key | Gates | UI affordance |
|---|---|---|
| `objective.read` | reads #2/#3/#4 | the `/objectives` nav entry + register + detail reads |
| `objective.manage` | create #1, plans #7/#8 | "New objective", "Add plan", plan "Remove" |
| `kpi.read` | measurements #6 | the measurement-history table |
| `kpi.record` | measurements #5 | "Record measurement" |

- `usePermissions().can(key)` (SYSTEM scope in v1) gates **write affordances** — never render a
  button the caller can't exercise.
- **Reads filter/403 calmly**: each read hook exposes a `forbidden` flag
  (`ApiError.status === 403` + `retry: false`) → a calm gray "No access" panel, never a crash.
- **The nav entry is gated** `{can("objective.read") && <NavLink to="/objectives" …/>}` (the
  `drift.read` precedent), slotted after Drift in the PLAN region of `LeftRail`.
- **Live-smoke note**: `demo` (System Administrator) holds **none** of `objective.*`/`kpi.*` →
  every surface is a calm no-access panel until SYSTEM overrides are granted on the **live login's
  app_user row** (org `AHT`), Chrome-MCP-driven.

## 5. UX design (owner-approved 2026-06-11)

### 5.1 Register — `/objectives` (layout **A**: scorecard band + table)

A calm **scorecard band** (`total`, `on_target` "N / M on target", and RAG count chips
green/amber/red/unmeasured) above a dense Mantine **Table**. Reads `GET /objectives/scorecard`
once — band from `{total,on_target,by_rag}`, rows from `objectives[]`. Columns: **Ref**
(`identifier`, link), **Objective** (`title`), **Current / target** (`current_value` `/`
`target_value` `unit`; "—" when unmeasured), **Status** (RAG badge), **Due** (`due_date`). Row →
`/objectives/{id}`.

- **Filters** (client-light): optional **process** filter (re-fetch scorecard with `?process_id=`)
  and an optional **RAG** chip filter (client-side row narrowing). Keep minimal — the CapaBoard
  `useMemo` filter idiom.
- **"New objective"** button (gated `objective.manage`) opens `NewObjectiveModal`.
- **Empty state**: no objectives → a calm card + the create CTA (when `objective.manage`).
- Rationale: this *is* the auditable register (the ISO 9.3.2 management-review input); the band is
  the reporting doc's "gauges + table-lite"; the big donut is the PDCA dashboard's job (next slice).

### 5.2 Create — `NewObjectiveModal` (single modal; measurable-by-construction)

Single calm modal (no wizard — 9 fields; plans are added later on the detail page, the create
endpoint takes none). Fields:

- **Required**: `title`; `target_value` + `unit` (a value+unit row); `direction`
  (**SegmentedControl** "Higher is better" / "Lower is better"); `due_date`.
- **Optional, behind a disclosure** ("Amber 'at-risk' band & baseline"): `baseline_value`,
  `at_risk_threshold`, and a **live `BandPreview` strip** that draws the green/amber/red zones
  from `{target, threshold, direction}` so the at-risk band is *seen*, not decoded.
- **Optional**: `process_id` (a process `Select`); **policy** as a single **checkbox** "Link to the
  current Quality Policy (POL-…)" — because the backend *requires* `policy_id` to equal the
  Effective POL singleton or be null; a free picker would be a trap. The checkbox is **disabled**
  when there is no Effective policy.
- **Amber-band guidance**: the preview strip **plus a soft, non-blocking inline warning** when the
  threshold sits on the wrong side of target for the chosen direction (a HIGHER threshold ≥ target,
  or a LOWER threshold ≤ target). The backend won't 422 it (it silently collapses to red), so we
  *warn*, not block.
- Save → `POST /objectives`; a **422** (policy/process validation) surfaces as an inline alert;
  on success, invalidate the scorecard and navigate to the new detail page (or close + refresh).

### 5.3 Detail — `/objectives/:id` (single-scroll sections)

A full page (the `/audits/:id` precedent). Composition:

- **Header**: `identifier` + `current_state` badge (honest "Draft") + `title`.
- **Commitment hero** (a `--color-background-secondary` panel): big `current_value` vs
  `target_value` `unit`, a progress bar (fill clamped to `pct_toward_target` ∈ [0,100]%), RAG +
  attainment badges; a meta column (direction, baseline → at-risk, due, process link, policy link).
- **Plans** section: a list of plan rows (`action`; `responsible_user_id` → directory name;
  `due_date`). **"Add plan"** (gated `objective.manage`) → `AddPlanModal`; each row has a
  **Remove** affordance (gated `objective.manage`, confirm) → `DELETE …/plans/{id}`.
- **Measurement history** section: **"Record measurement"** (gated `kpi.record`) → modal; a
  **plain table** (Period · Value · **Target then** = `target_at_capture` · Source · Recorded),
  newest-first, with a one-line "trend charts arrive in a later release" footnote. **No chart**
  (N6). Reads `GET …/measurements` (gated `kpi.read` — calm 403 panel within the section).

### 5.4 Record measurement — `RecordMeasurementModal`

A small modal: **period** (date, required; defaults to today, editable for back-dating a quarter),
**value** (number), optional **source**. The **unit is locked** to the objective's `unit` — shown
as a fixed adornment on the value field and sent verbatim — so the form physically can't send a
divergent unit and trip the 422. A 422/404/409 still surfaces as a calm inline alert. Success
invalidates `["objective", id]` (the rollup `current_value` changes) and
`["objective-measurements", id]`.

## 6. Component architecture / file layout

A flat feature folder `apps/web/src/features/objectives/` (the CAPA/Audits idiom):

```
features/objectives/
  hooks.ts                  useObjectiveScorecard(processId?), useObjective(id),
                            useObjectiveMeasurements(id)  — each {…query, forbidden}
  mutations.ts              useCreateObjective, useRecordMeasurement, useAddPlan, useRemovePlan
  labels.ts                 RAG_COLOR/RAG_LABEL, ATTAINMENT_LABEL, DIRECTION_LABEL,
                            fmtValueUnit(), bandZones() (pure, for BandPreview + soft-warn)
  ObjectivesRegisterPage.tsx
  ObjectiveScorecardBand.tsx
  ObjectivesTable.tsx
  ObjectiveDetailPage.tsx
  CommitmentHero.tsx
  PlansSection.tsx          + AddPlanModal
  MeasurementsSection.tsx   + RecordMeasurementModal
  NewObjectiveModal.tsx
  BandPreview.tsx
  *.test.tsx                co-located vitest + MSW + jest-axe
```

- **Routing** (`App.tsx`): `/objectives` (register) and `/objectives/:id` (detail) under
  `AppShell`, before the wildcard.
- **Nav** (`LeftRail.tsx`): `{can("objective.read") && <NavLink to="/objectives" label="Objectives" …/>}`
  after the Drift entry.
- **Types** in `lib/types.ts` (§3.2). No new `lib/api.ts` surface — `useApi()` covers it.

## 7. Data flow & react-query keys

| Surface | Read | Key |
|---|---|---|
| Register band + rows | `GET /objectives/scorecard?process_id=` | `["objectives-scorecard", processId ?? null]` |
| Detail commitment + plans | `GET /objectives/{id}` | `["objective", id]` |
| Measurement history | `GET /objectives/{id}/measurements` | `["objective-measurements", id]` |

Mutations invalidate: create → `["objectives-scorecard"]`; add/remove plan → `["objective", id]`;
record measurement → `["objective", id]` **and** `["objective-measurements", id]` (the rollup
moves). No optimistic updates (server is source of truth), `onSuccess` invalidation.

## 8. Error handling

- **403** on any read → `forbidden` flag (`ApiError.status===403`, `retry:false`) → calm gray
  "No access" panel (page-level for the register/detail reads; section-level for the measurement
  read). Prove no retry-hammer with a **production-defaults** QueryClient in the relevant test.
- **404** on detail (`GET /objectives/{id}`) → a calm "not found / no access" alert.
- **422** on create (policy/process) and on measurement (unit mismatch — near-impossible given the
  locked unit) → inline alert mapping the problem `title`.
- Write affordances are **hidden** (not disabled) when the gating key is absent.

## 9. Testing strategy

- `vitest run` + MSW + jest-axe; `renderWithProviders` + `TEST_AUTH`; per-test `server.use()`
  overrides; fixtures `satisfies <Type>` pinned to §3.2.
- Coverage: register band counts + table + empty/forbidden; RAG/attainment badge mapping; the
  `BandPreview` zones + the soft-warn trigger (pure `bandZones()` unit-tested + a component test);
  the locked-unit measure form; create-modal validation + 422 mapping; plan add/remove gating;
  detail calm-403 (production-defaults QueryClient); nav-entry gating; jest-axe on each page.
- **Strict gate**: `noUncheckedIndexedAccess` (array-index nits) + the full `/check-web`
  (eslint + `tsc --noEmit` + build + suite) before the PR. Track the web-test delta from the
  **589** baseline (post S-ack-2).

## 10. Build sequence (subagent-driven TDD; mirrors the S-ack-2 template)

Sequential phases, each task `write failing test → run (fail) → implement → run (pass) → commit`,
per-task spec + quality review:

1. **Foundation** — `lib/types.ts` additions (§3.2) + MSW handlers & fixtures for all 8 endpoints
   (pinned, `satisfies`); `labels.ts` + `bandZones()` pure unit.
2. **Register** — `useObjectiveScorecard`, `ObjectiveScorecardBand`, `ObjectivesTable`,
   `ObjectivesRegisterPage` (band + table + filters + empty/forbidden).
3. **Detail (read)** — `useObjective`, `useObjectiveMeasurements`, `ObjectiveDetailPage`,
   `CommitmentHero`, `MeasurementsSection` (read-only table), `PlansSection` (read-only list).
4. **Writes** — `NewObjectiveModal` + `BandPreview` + soft-warn + `useCreateObjective`;
   `RecordMeasurementModal` (locked unit) + `useRecordMeasurement`; `AddPlanModal` +
   `useAddPlan`/`useRemovePlan`.
5. **Wire + close** — `App.tsx` routes, `LeftRail` gated entry; full `/check-web`; `diff-critic`
   on the branch diff; Chrome-MCP live smoke (SYSTEM overrides on the live `demo` row, org AHT);
   `docs/slice-history.md` entry + a `CLAUDE.md` Recent-learnings line + the web-test delta;
   PR → Codex triage (disregard the multi-tenant nitpicks — moot under D1) → squash-merge on green.

## 11. Verification & closeout

- Web loop runs fully locally: `/check-web`.
- `diff-critic` before the PR (read-only adversarial, pre-loaded with the invariants).
- Live smoke via Chrome MCP (find-then-click in separate batches, text-first verification,
  client-side nav only; grant the `objective.*`/`kpi.*` SYSTEM overrides to the LIVE login's
  app_user row; `just up s --build` first to pick up the merged `0049` + router).
- No backend gates run (front-end-only) — but confirm `contracts` stays green (no `openapi.yaml`
  change expected).
