# S-dcr-ui-1 — DCR read spine (clause §10→§7.5 change control) — design

- **Date:** 2026-06-13
- **Slice:** `S-dcr-ui-1` — the **first** front-end for the Document Change Request (DCR) domain, which has had a fully-built backend (S-dcr-1…5 + S-dcr-3b) and **zero** SPA surface. This slice is the **read spine**; the lifecycle writes (S-dcr-ui-2) and the page-image visual diff (S-dcr-ui-3) are explicitly deferred.
- **Status:** owner-approved design (brainstorm 2026-06-13, after a six-reader source-verification sweep — every claim below is checked against code, not slice-history narrative).
- **Significance:** gives the DCR lifecycle a browser cockpit (read-only this slice). It **unblocks** the three already-built spawn seams (`POST /dcrs`, `POST /capas/{id}/raise-dcr`, `POST /management-reviews/{id}/outputs/{oid}/raise-dcr`) which all return a deep-linkable DCR but, per the recorded S-mr-3 finding, currently "spawn into a black hole" because there is no `/dcrs/:id` landing page. After this slice, S-dcr-ui-2 can wire those seams + the write transitions.
- **Scope decision — 100% FRONT-END-ONLY.** The owner chose **read-spine-only** (F1) and **strictly-front-end** (F5): no migration, no new permission key, no new endpoint, no contract change. Gate: **`/check-web` only**. diff-critic on the branch diff pre-PR; Chrome-MCP live smoke with a `changeRequest.read` (+ `document.read`) SYSTEM override on the live `demo` app_user (org AHT).
- **Doc grounding:** doc-05 (the change-control loop §10→§7.5) · doc-07 (the `changeRequest.*` catalog — 7 keys) · doc-15 §8.7 (DCR endpoints — ⚠ its prose is **stale**, see s0/Accepted-reconciliations) · R40 (the changes-requested loop targets `Open`) · R26 (non-renderable renditions — surfaces only in the deferred diff slice) · N9 (status against a rule, never an auto-verdict) · doc-11 (calm, progressively-disclosed UI). Web SPA testing rules: `.claude/rules/engineering-patterns.md` "Web SPA testing".
- **As-built anchors (verified this session — pin MSW fixtures HERE, NOT the mockup or doc-15 prose):**
  - **Serializers:** `apps/api/src/easysynq_api/api/dcr.py` — `_dcr` (`:118-137`, 16 flat fields, **no `capabilities` block**), `_impact` (`:140-148`), `_stage_event` (`:151-160`). Scope resolver `_dcr_scope`/`_dcr_doc_scope` (`:166-208`); gates (`:211-215`); spawn endpoints (`:444-475`).
  - **State machine:** `domain/dcr/fsm.py:32-46` (9 states + `transition_allowed`).
  - **Enums:** `db/models/_dcr_enums.py` — `DcrChangeType` (`:52-55`), `DcrReasonClass` (`:58-69`, 9 members incl. `mgmt_review`), `DcrSourceLinkType` (`:72-79`, `capa`/`finding`/`mgmt_review` live, `risk` reserved). `ChangeSignificance` is the **reused vault enum** `_vault_enums.py:48-50` (`MAJOR`/`MINOR`), not a DCR-specific one.
  - **Stage-event order:** `services/dcr/repository.py:65-78` — `occurred_at.asc()` (genesis-first).
  - **Contract (zero drift on schema/enums confirmed):** `packages/contracts/openapi.yaml` — `Dcr` (`:6797-6831`), `DcrReasonClass`/`DcrSourceLinkType` enums (`:6761-6775`).
  - **FE precedents:** `apps/web/src/features/capa/` (`CapaBoardPage.tsx` — the `?capa=<id>` URL-seeded drawer + calm-403 early-return; `CapaDrawer.tsx`; `CapaTimeline.tsx`; `hooks.ts` — the `retry:false`+`forbidden` contract + `useCapa` `enabled:id!==null`) · `features/document/StateBadge.tsx` (the badge precedent; DCR needs its own state map) · `features/management-review/` (the "no-capabilities-block, gate on `current_state`+`can`" cockpit shape; `hooks.ts` `forbiddenOf`) · `App.tsx` (the register/detail route pair; objectives `:144-147`) · `app/shell/LeftRail.tsx` (the `{can("mgmtReview.read") && <NavLink…>}` gated-rail precedent) · test infra `test/{render,setup}.tsx`, `test/msw/{handlers,server}.ts`, `features/management-review/ReviewOutputsSection.test.tsx` (the `import {expect,it} from "vitest"` + jest-axe smoke + `{open && <…/>}` conventions).

> **The thesis.** The DCR backend is the largest built-but-UI-less gap in v1. This slice surfaces it the way S-web-7a surfaced the CAPA read spine: a **register table** (filterable) + a **`?dcr=<id>` read-only drawer** (state badge + the append-only `DcrStageEvent` timeline + the 7-dimension impact panel + resolved target/source references + a resulting-version deep-link), against the three existing `changeRequest.read` reads. Pure front-end — the only "enrichment" is resolving the target document's name client-side via the existing `GET /documents/{id}` (calm-degrading when the viewer lacks `document.read`). It deliberately ships **no writes**, so it carries no signatures, no SoD, no `/tasks` leg, and no worker-async diff.

---

## s0 · Owner decisions (this session, 2026-06-13)

The F-numbers are the brainstorm's decision frame; each was preceded by the source sweep.

1. **F1 — read spine only.** `S-dcr-ui-1` = the `/dcrs` register + the `?dcr=<id>` read-only drawer + a `changeRequest.read`-gated nav entry. The lifecycle writes (raise/edit/cancel/assess/route/implement/close + the `/tasks` DCR-approval leg + the implement double-gate + the close 409) defer to **S-dcr-ui-2**; the page-image visual diff + redline defer to **S-dcr-ui-3**. Rationale: lowest-risk first cut, mirrors the CAPA epic's S-web-7a/b/c/d cadence, and it is the prerequisite landing page that unblocks all three spawn seams.
2. **F2 — raise flow deferred.** No raise modal, no CAPA-drawer / MR-output "Raise DCR" wiring this slice — those would currently land in a black hole and are S-dcr-ui-2 work. (The seams are backend-ready; the only blocker is this landing page.)
3. **F5 — strictly front-end.** The `_dcr` serializer returns bare UUIDs for `target_document_id`/`source_link_id` (no nested labels). Rather than add a backend enrichment, resolve the **target** doc identifier/title **in the drawer** via the existing `GET /documents/{id}` (one read on open, not N-per-row), **calm-degrading** when the viewer lacks `document.read`. The **source** is navigation-only (deep-link where one cleanly exists client-side; a labelled reference otherwise). This keeps the slice's gate web-only.
4. **F3 (read-only slice) — `DcrStateBadge` + the stage-event timeline.** No progress stepper this slice — the 9-state machine branches (Rejected/Cancelled are off-happy-path terminals) and the append-only `DcrStageEvent` timeline already encodes the full lineage. The stepper/`AdvancePanel` defers to ui-2 where a "next action" makes it meaningful.
5. **F4 (read-only slice) — resulting-version deep-link.** When `resulting_version_id`/`target_document_id` is set (Implemented REVISE/CREATE), show a read-only deep-link to the resulting document in the existing documents UI (navigation only, no diff computation). The page-image visual diff + redline defer to ui-3.
6. **F6 — gating/smoke.** Per-key calm-403; `demo` (System Administrator) holds **no `changeRequest.*`** content keys → SYSTEM overrides on the **live** demo `app_user` row (org AHT) for the smoke, granted **before login** so the nav appears on first load.

**Accepted reconciliations (code is authoritative; NO doc/contract change in this slice):**
- **doc-15 §8.7 prose is stale (pre-rename).** It uses `reason_for_change` + a nested `source_link:{type,id}`; the **as-built serializer + contract** use `reason_text` + **flat** `source_link_type`/`source_link_id`, and the list filter is `reason_class`. The FE pins to the serializer/contract names, not doc-15 prose. (No doc edit this slice — strictly front-end; a doc-15 refresh is a separate housekeeping task.)
- **Cosmetic OpenAPI drift (left untouched):** the `dcr` tag description and `Dcr.resulting_version_id` ("NULL in v1") descriptions lag the shipped state. No schema bug — and touching `openapi.yaml` pulls in `/check-contracts`, so it is deliberately **out of scope** to keep the slice front-end-only.

---

## s1 · What the code already pins (settled — restated, not re-decided)

**`_dcr` serializer (`api/dcr.py:118-137`) — pin every list/drawer fixture to this exact shape via `satisfies Dcr`. NO decimals anywhere in this API.** 16 flat fields:

| Field | TS type | Nullable | Notes |
|---|---|---|---|
| `id` | `string` | no | UUID |
| `identifier` | `string` | no | `DCR-{YYYY}-{NNNN}` (4-digit seq) |
| `target_document_id` | `string \| null` | **yes** | null for `CREATE` |
| `change_type` | `DcrChangeType` | no | `REVISE`/`CREATE`/`RETIRE` |
| `change_significance` | `ChangeSignificance` | no | `MAJOR`/`MINOR` |
| `reason_class` | `DcrReasonClass` | no | 9 members |
| `reason_text` | `string` | no | free text |
| `source_link_type` | `DcrSourceLinkType \| null` | **yes** | `capa`/`finding`/`mgmt_review`/`risk` |
| `source_link_id` | `string \| null` | **yes** | polymorphic, **no FK** |
| `proposed_effective_from` | `string \| null` | **yes** | ISO datetime |
| `resulting_version_id` | `string \| null` | **yes** | set at implement (REVISE/CREATE); null for RETIRE / pre-implement |
| `state` | `DcrState` | no | 9 members |
| `decision` | `string \| null` | **yes** | free text, null until approval/rejection |
| `created_by` | `string` | no | an **`app_user.id`** (NOT the Keycloak `sub`) |
| `created_at` | `string` | no | ISO datetime |

⚠ **Reconcile the count against the file.** The verification sweep's header said "16 flat fields" but enumerated the 15 above — the discrepancy is unresolved in narrative. **`api/dcr.py:118-137` is authoritative:** the implementer pins the fixture to the live serializer exactly. If the file carries a key not in this table, pin it too; **do not invent fields and do not omit any**.

**Per-endpoint augmentation (NOT on every `_dcr`):**
- `GET /dcrs` → wraps in `{"data": [_dcr…]}`.
- `GET /dcrs/{id}` → the bare `_dcr` **plus** `stage_events: _stage_event[]` (chronological, genesis-first).
- `GET /dcrs/{id}/impact` → `{"data": [_impact…]}`.

**`_stage_event` (`dcr.py:151-160`):** `{id, from_state: DcrState|null` (null on genesis), `to_state: DcrState, actor_id: string|null` (null for system/Beat), `comment: string|null, payload: object|null` (free JSONB), `occurred_at: string}`.

**`_impact` (`dcr.py:140-148`):** `{id, dimension: string` (7 values), `auto_populated: object|null` (system facts, e.g. `{"applicable":true,"processes":[…]}` or `{"applicable":false}`), `requester_annotation: string|null, created_at: string, updated_at: string|null}`.

**Enums (exact members — typed in `lib/types.ts`):**
- `DcrChangeType`: `"REVISE" | "CREATE" | "RETIRE"`.
- `ChangeSignificance`: `"MAJOR" | "MINOR"`.
- `DcrReasonClass`: `"regulatory" | "audit_finding" | "capa" | "process_improvement" | "error_correction" | "periodic_review" | "customer_requirement" | "mgmt_review" | "other"`.
- `DcrSourceLinkType`: `"capa" | "finding" | "mgmt_review" | "risk"`.
- `DcrState`: `"Open" | "Assessed" | "Routed" | "InApproval" | "Approved" | "Implemented" | "Closed" | "Cancelled" | "Rejected"`.

**Endpoints consumed (all read; all `changeRequest.read`):**
- `GET /dcrs` — filters `state`, `change_type`, `reason_class`, `target_document_id`, `created_by`. Org-scoped server-side. `{"data":[…]}`.
- `GET /dcrs/{id}` — `_dcr + stage_events[]`. Cross-org → 404.
- `GET /dcrs/{id}/impact` — `{"data":[…]}`.
- `GET /documents/{id}` (existing, `document.read`-gated) — drawer-only, to resolve the target doc identifier/title; **calm-degrade** on 403/404.

**Routing reality (no str-convertor shadow trap):** `{dcr_id}` is the FastAPI `uuid.UUID` convertor and every sub-path segments *after* the id, so there is no `/dcrs/<literal>` shadow (unlike S-pack-2). No special route-ordering needed; this is a backend fact and needs no FE work, but it means a `/dcrs/:id`-style deep link is safe. **This slice uses `?dcr=<id>` query-seeding (the CAPA precedent), not a nested route.**

---

## s2 · Module — `apps/web/src/features/dcr/`

### `DcrRegisterPage.tsx` (route `/dcrs`)
- A **register table** (not a kanban — the branching 9-state machine doesn't column cleanly). Columns: **Identifier** (clickable → opens the drawer via `?dcr=<id>`) · **Change type** (chip) · **Significance** (subtle chip) · **Reason** (`reason_class` label) · **Target** (deep-link chip to `/documents/{target_document_id}`, or "—" for CREATE; **name resolved in the drawer, not per-row**) · **State** (`DcrStateBadge`) · **Created** (relative date).
- **Faceted filters:** `state` (select/segmented), `change_type` (select), `reason_class` (select); optional **"Mine"** toggle → `created_by = useMe().id` (the `app_user.id`, NOT `user.profile.sub`). Filters map to the list query params; default sort `created_at` desc client-side.
- **Drawer seeding:** `?dcr=<id>` URL param drives the drawer (mirrors `CapaBoardPage`'s `?capa=<id>`). Clicking a row sets the param; closing clears it.
- **Calm-403:** if `useDcrs(...)` is `forbidden`, early-return a calm no-access panel (the `CapaBoardPage:79` precedent). Empty state: "No change requests yet."
- **a11y:** one `h1` page title → `h2` section headings (jest-axe smoke must pass — the S-mr-2 heading-order catch). Decorative arrows `aria-hidden`.

### `DcrDrawer.tsx` (`{open && <DcrDrawer/>}` conditional-mount)
- Guards: `isLoading` / `isError` / `!dcr` (the `CapaDrawer` precedent); header gated on `!isError`.
- **Header:** identifier · `DcrStateBadge` · `change_type` / `change_significance` / `reason_class` chips.
- **Core fields:** `reason_text`; `proposed_effective_from` (org-tz display); `decision` (if set); `created_by` (a user-id — see the user-display plan investigation below) + `created_at`.
- **Target reference:** `useTargetDocument(target_document_id)` (existing `GET /documents/{id}`); render the resolved identifier+title as a deep-link to `/documents/{id}`. **Calm-degrade** (`retry:false` + forbidden flag): on 403/404, fall back to the bare `target_document_id` (no title), still a navigable link. For `CREATE` (`target_document_id` null), render "New document (no target)".
- **Source reference:** by `source_link_type` — `capa` → "Source: CAPA" + deep-link `/capa?capa=<source_link_id>`; `finding` → "Source: Audit finding" + id (no clean client link); `mgmt_review` → "Source: Management-review output" + id (no clean client link — `source_link_id` is the output id); null → omit. (Richer source linking defers to ui-2 alongside the spawn seams.)
- **Resulting version (F4):** when `target_document_id` is set (**REVISE** → the revised doc; **RETIRE** → the now-Obsolete target), a read-only deep-link "View document" → `/documents/{target_document_id}` (the documents page self-gates). ⚠ **CREATE edge:** a CREATE DCR has `target_document_id` null and `resulting_version_id` on a **new** document whose id `_dcr` does **not** expose — so a CREATE resulting-doc link is **deferred** (named, not faked) unless the implementer finds a cheap version→document resolution in `api/documents.py`; if so, resolve and link, else show no link for CREATE. Link absent when `resulting_version_id` is null (pre-implement).
- **Impact panel:** `<DcrImpactTable/>` from `useDcrImpact(id)`.
- **Timeline:** `<DcrStageTimeline/>` from the detail's `stage_events[]`.

### `DcrStageTimeline.tsx`
- The append-only `stage_events[]` thread (genesis-first), each row: `from_state → to_state` (genesis shows just `to_state`), actor (see the user-display plan investigation; "system" when `actor_id` null), `comment` (if any), `occurred_at` (org-tz). Mirrors `CapaTimeline`. `payload` JSONB rendered **generically as text** if surfaced at all — **never** `dangerouslySetInnerHTML` (XSS rule).

> **Plan investigation — user-id display.** `created_by` and stage-event `actor_id` are bare `app_user.id`s (no name in `_dcr`/`_stage_event`). The implementer checks for an existing client-side user-resolution path (e.g. how `CapaTimeline` shows its actor, or the `/tasks` inbox shows an assignee). If one exists and is read-accessible to a `changeRequest.read` holder, resolve to a name with **calm-degrade to the id**; otherwise display the short id and "system" for a null actor. **Do NOT add a backend user-name enrichment** (strictly-front-end); a richer name resolution can land in ui-2 (named, not faked).

### `DcrImpactTable.tsx`
- A plain table over the 7 `_impact` dimensions: **Dimension** · **System facts** (`auto_populated` rendered generically — e.g. "Applicable · N processes" / "Not applicable"; never raw HTML) · **Annotation** (`requester_annotation` or "—"). Read-only this slice (the PUT-annotate is ui-2). Empty/`auto_populated:null` → a calm "not yet assessed" row (impact rows exist only after `assess`, so a pre-assess DCR shows an empty panel).

### `DcrStateBadge.tsx`
- A dedicated badge with a color map over the 9 `DcrState` members (the document `StateBadge` covers doc states only). Suggested bands: in-flight (`Open`/`Assessed`/`Routed`/`InApproval`) neutral/blue; `Approved`/`Implemented` progressing; `Closed` success; `Cancelled` muted; `Rejected` warning/red. Exact tokens per the theme — a plan detail.

### `hooks.ts`
- `useDcrs(filters)` → `GET /dcrs` (the `{data}` unwrap), `retry:false`, `forbidden = error instanceof ApiError && error.status === 403`.
- `useDcr(id)` → `GET /dcrs/{id}`, `enabled: id !== null` (the `useCapa` precedent), `retry:false`, forbidden.
- `useDcrImpact(id)` → `GET /dcrs/{id}/impact` (`{data}` unwrap), `enabled: id !== null`, `retry:false`.
- `useTargetDocument(id)` → existing `GET /documents/{id}`, `enabled: id != null`, `retry:false`, **forbidden flag** (the degrade signal). Reuse an existing document hook if one fits rather than re-rolling.
- No mutations this slice (read-only).

### `types.ts` (or extend `lib/types.ts`)
- `Dcr`, `DcrStageEvent`, `DcrImpact`, and the 5 enums above — pinned byte-for-byte to `api/dcr.py`. (`lib/types.ts` already carries the `DCR_TRIAGE` task-type literal + the `"DCR"` `subject_type` comment — leave those; they are ui-2's concern.)

---

## s3 · Routing & navigation

- **`App.tsx`:** add `<Route path="dcrs" element={<DcrRegisterPage/>}/>` (a single flat route; the drawer is `?dcr=<id>`, not a nested route — the CAPA precedent).
- **`app/shell/LeftRail.tsx`:** `{can("changeRequest.read") && <NavLink to="/dcrs" …>Change requests</NavLink>}`, `active={pathname.startsWith("/dcrs")}`. **Placement:** the document-control group (DCR governs document change) — the implementer picks the exact slot by reading `LeftRail.tsx`. The nav entry is hidden entirely without the read key (the `/drift`·`/objectives` gated-entry precedent).

---

## s4 · Error handling & data flow

- **Page-level (`changeRequest.read`):** forbidden → calm no-access panel; the nav entry is already hidden by `can(...)`, so this guards a direct URL visit.
- **Drawer-level target resolution (`document.read`):** a *separate* key the viewer may lack → `useTargetDocument` calm-degrades to the bare id link; never crashes the drawer.
- **Loading:** skeletons; the **first content assertion in every test must `waitFor`** (the skeleton-frame false-PASS).
- **Distinct `aria-label`s** across repeated elements (the `getByLabelText` single-match trap); scope queries `within(...)` where a label repeats per row.
- **XSS:** `auto_populated` / `payload` / `comment` are free-form → render generically as text nodes, never raw HTML.

---

## s5 · Testing

- `DcrRegisterPage.test.tsx` — renders rows from a pinned `{data:[Dcr…]}` fixture; filter interactions; row-click opens the drawer (`?dcr` seeded); calm-403 panel on a 403 list; **jest-axe smoke**; empty state.
- `DcrDrawer.test.tsx` — fields, the timeline (from `stage_events[]`), the impact table (from the impact fixture), the **target-degrade path** (target-doc 403 → bare-id link, no crash), the **CREATE** no-target rendering, the source deep-links (capa link present; finding/mr labelled-no-link), the resulting-version link present-iff-set; `{open && …}` reset-on-close.
- `hooks.test.tsx` (or co-located) — the calm-403 contract under a **production-defaults QueryClient** (the global test wrapper's `retry:false` can mask a missing per-hook `retry:false` — pin it like S-web-8).
- **Conventions (mandatory):** `import { expect, it } from "vitest"` in every component test (the jest-dom×tsc trap); fixtures `satisfies Dcr`/`DcrStageEvent`/`DcrImpact`; MSW per-test overrides via `server.use(http.get(...))`; reuse `test/{render,setup}.tsx` (the `scrollIntoView`/`matchMedia`/`ResizeObserver` stubs).
- **Gate:** the **full `/check-web`** (eslint + strict `tsc --noEmit` + build + the whole vitest suite) before the PR — strict `noUncheckedIndexedAccess` + cross-file fixture drift are invisible to a per-file run.
- **Estimated delta:** ~25–35 web tests (761 → ~790).

---

## s6 · Live smoke (Chrome MCP, pre-merge)

1. **Rebuild the `web` image** (`… up -d --build web`) — `vite preview` serves a baked build, no source mount; hard-refresh / Incognito to drop the cached bundle.
2. **Grant SYSTEM overrides on the LIVE `demo` `app_user` row** (org **AHT**), **before login** so the nav appears first load: `changeRequest.read` (the page) + `document.read` (full target resolution; omit it to demo the calm-degrade). Check the override lands on the row matching the **live login's Keycloak subject** (the re-created-JIT-row trap — verify against the live `app_user`, not the old bootstrap row).
3. **Seed a few DCRs across states** via the worker heredoc (`exec -T worker sh -c "cd /app; uv run python -"`, the backend live-smoke mechanics note): one `Open` REVISE, one `Implemented`-with-`resulting_version_id`, one `Cancelled` — so the register, the drawer, the timeline, the impact panel, and the resulting-version link are all exercised.
4. **Verify:** the nav entry renders; the register lists + filters; a row opens the drawer; the drawer shows the state badge, the resolved target (and the degrade path with `document.read` removed), the source reference, the impact table, the stage-event timeline, and the resulting-version deep-link where set.

---

## s7 · Out of scope (named, not faked) → S-dcr-ui-2 / -ui-3

- **Raise + the 3 spawn seams** (`POST /dcrs` modal with the CREATE⟺no-target conditional target picker; the CAPA-drawer + MR-output "Raise DCR" affordances surfacing the S-mr-3 deferral). All backend-ready; need only this landing page.
- **Lifecycle writes:** edit-while-Open (PATCH) · cancel · assess (+ PUT-annotate impact) · route (+ approval-instance display) · implement · close — the per-state/per-key affordances + a progress stepper + `AdvancePanel`.
- **The implement double-gate:** `changeRequest.implement` **+** the in-handler `document.release`/`.obsolete` (`sig_hook=True`, SoD-2) → needs a **detail-only `capabilities.implement`** read-enrichment (the S-mr-3 Codex #1 `capabilities.release` precedent) to avoid show-then-403; the RETIRE `force_retire` + `override_justification` confirm (409 `obsoletion_blocked`).
- **The close 409** (`dcr_effectivity_pending`) surfaced calmly (submit-and-show-the-409, no client gate — the CAPA `CloseAction` precedent).
- **The `/tasks` DCR-approval leg** (`DCR_TRIAGE`; candidate-pool authz, branch on `task.subject_type`, no `changeRequest.approve` grant).
- **The page-image visual diff + the text/metadata redline** against `resulting_version_id` (REVISE/CREATE only) — reuses `features/document/` `VisualDiffViewer`/`useVisualDiff` (the authed-binary objectURL + POST-compute/GET-poll contract) and the redline.
- **Doc/contract housekeeping:** the stale doc-15 §8.7 prose + the cosmetic OpenAPI descriptions (separate task; touching the contract pulls in `/check-contracts`).
