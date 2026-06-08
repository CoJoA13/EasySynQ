# S-ing-4b — Ingestion Review UI — Design

> **Status:** DRAFT for owner review (2026-06-08). · **Owner:** Colton Jones
> **Slice:** S-ing-4b (web track). **Closes UJ-2** — "Import an existing QMS" (the full operator
> journey: point at a source → scan/classify → human-in-the-loop review → commit to the vault).
> **Branch (on approval):** `feat/s-ing-4b-ingestion-review`. **Migration head:** `0044` →
> **no new migration** (front-end only).
> **Net shape:** front-end only — **no migration, no permission key, no `openapi.yaml` change**.
> Surfaces the already-shipped + already-contracted S-ing-1..5 backend (migs 0029–0033; the full
> `/admin/imports/*` surface in `packages/contracts/openapi.yaml` lines 2787–3135).
> Builds on the SPA shell (S-web-1), the faceted Library (S-web-2), the document detail + redline
> (S-web-4/4b), Review & Approve (S-web-5), and Search + Compliance (S-web-6).
> **Authoritative grounding:** doc 09 (ingestion engine; §3 pipeline, §6 classify, §7 dedup, §9
> review screen, §10–11 commit/resume, §12 report), doc 01 §7 (UJ-2), the decisions register
> (R2/R5/R10/R34/R35/R38), the owner-approved mockup `mockup/easysynq-mockup.html`
> `#screen-ingestion` (lines 4237–4704), and the live backend (`apps/api/src/easysynq_api/
> {api/ingestion.py, services/ingestion/*, db/models/import_*}`, cited inline by `file:line`).

---

## 1. Goal & user journey

**Goal.** Build the browser front-end that closes UJ-2 — the human-in-the-loop **Ingestion Review**
surface over the complete import pipeline. An admin points EasySynQ at an existing folder tree;
the engine scans, extracts, classifies, de-duplicates, and proposes; a reviewer triages thousands
of items (accept / correct / merge / split / exclude / defer, and the always-human **kind** confirm),
clears the pre-commit conflict gate, and commits the confirmed set into the vault as Effective Rev-A
controlled documents + immutable Records. Nothing becomes controlled until commit (N9: "the tool
organizes, humans decide").

**Personas.** Two-handed by separation of duties, *configurable not hard-coded* (doc 09 §9.4, IA4):
**Avery (System Administrator)** operates the run (`import.execute`, `import.commit`); **Mara
(Quality Manager)** decides the content (`import.review`). In this install the **`demo` System
Administrator holds all three import keys** (they are in `_SYSTEM_KEYS`, granted to the System
Administrator role bundle — `migrations/versions/0004_seed_authz.py:135-137,293`), so a single demo
operator can drive the whole journey; the SoD split is a grant-time posture, not a code gate.

**The journey, end to end (doc 01 §7; doc 09 §3 staged state machine):**

1. **Start.** The operator opens `/ingestion`, clicks **New import**, gives a `source_root` (a path
   within the configured import mount root) + an OCR toggle, and submits. The run is created
   (`POST /admin/imports` → 202, status `Created`) and the engine scans, extracts, classifies,
   de-dups, and proposes — auto-chaining `Created → … → Proposed`. Nothing touches the vault.
2. **Watch.** The run page shows a calm **scan-progress** view while the run is pre-`Proposed`,
   polling the run until it rests at `Proposed` (or `Failed`).
3. **Review.** At `Proposed`/`Reviewing` the page becomes the **review cockpit**: a run summary, five
   confidence/decision queues, a paged triage table with multi-select bulk actions, an item detail
   drawer (classification evidence, dedup membership, proposal/conflicts, decision history), merge/
   split, and the always-human **kind** confirm. The first review write flips the run to `Reviewing`.
4. **Gate.** A **pre-commit checklist** surfaces blocking conflicts (duplicate identifier, vault
   collision, singleton-type-already-effective, ambiguous-over-threshold) and the advisory mandatory-★
   coverage projection. Commit is enabled when the run is `ready` (zero blocking conflicts) and ≥1
   item is `commit_ready`.
5. **Commit.** Commit (`POST /admin/imports/{id}/commit` → 202, status `Committing`) drives the
   confirmed subset item-by-item into the vault (Effective Rev A + WORM blob + `import_baseline`
   signature + provenance; Records as immutable Record entities). The page shows a **commit-progress**
   view, then a terminal summary (`Completed` / `PartiallyCommitted` + `counts.commit`, a link to the
   Import Report record). A partial run resumes; idempotent re-commit is a no-op.

This slice builds **all four faces** (new/scan-progress, review cockpit, commit-progress, terminal)
of the run lifecycle plus the runs landing — the complete UJ-2 close in one slice (owner decision,
2026-06-08).

---

## 2. Decisions baked into this design (owner-approved 2026-06-08)

| # | Decision | Choice |
|---|---|---|
| D-1 | **Scope** | **Full UJ-2 in one slice** — runs landing + New-Import form + scan-progress + review cockpit + dimensional decisions + R10 kind-confirm + merge/split + pre-commit checklist + commit + resume + terminal summary. |
| D-2 | **Table scaling** | **Server-side `offset`/`limit` pagination (100/page)**, plain Mantine `<Table>` — **no new SPA dependency**, no virtualization. The DOM never holds more than ~100 rows, so it scales to any run size; whole-bucket bulk actions use the server-side `selector` and never load all rows. (Reframes the "virtualize to 5k+" risk: pagination already bounds the DOM.) |
| D-3 | **Commit gate** | Modeled the backend's way: **commit enabled when checklist `ready` (zero `blocking[]`) AND `review.commit_ready ≥ 1`**. Unconfirmed `kind` is an **advisory completeness warning**, NOT a hard block — an unconfirmed item is silently skipped at commit; the button reads **"Commit N confirmed"** (the `commit_ready` count). |
| D-4 | **Merge/split** | **Server-authoritative + structural**: submit a merge/split intent, then **invalidate + refetch** (no optimistic client reshape). Merge via multi-select → "Merge into one family" + the inline row `Merge ▾` for the dup-of case; split from the detail drawer's group view. The backend owns preserve-other-groups + canonical/effective recompute + proposal re-derive. |
| D-5 | **Kind-confirm (R10)** | A **separate human act**, never threshold-implicit. The engine guess renders dimmed with `?`; only a per-row `Confirm` or the bulk `Confirm kind` sets `after.kind ∈ {DOCUMENT, RECORD}`. **Bulk-accept-all-High does NOT confirm kind.** |
| D-6 | **"Change plan" banner** | Informational in v1 (the drift-safe import-default explainer). The real per-family revision-chain opt-in lives in the merge flow (`reconstruct_revision_chain`), not a global toggle. |

**Net shape of the slice:** front-end only — **no migration, no new permission key (R38/R5), no
`openapi.yaml` change**. New SPA feature module `apps/web/src/features/ingestion/` + routing/nav +
a `types.ts`/`handlers.ts` block.

---

## 3. What already exists (the delta is small — there is no backend gap)

Every endpoint the journey needs is shipped (S-ing-1..5) and contracted. All gates are **prose-only**
in the contract (no per-op `security:` block) — wire them from the route docstrings.
`apps/api/src/easysynq_api/api/ingestion.py:50-52` defines `require("import.execute"|"import.review"
|"import.commit")`.

**Reads (consume as-is):**

| Endpoint | Gate | Returns | Cite |
|---|---|---|---|
| `GET /admin/imports` | `import.review` | `ImportRun[]` (optional `?run_status=`) | openapi 2788; `ingestion.py:308` |
| `POST /admin/imports` | `import.execute` | 202 `ImportRun` (`Created`; 409 carries `active_run_id`) | openapi 2802; `ingestion.py:289` |
| `GET /admin/imports/{id}` | `import.review` | `ImportRun` (`status` + `counts`) | openapi 2828; `ingestion.py:319` |
| `GET /admin/imports/{id}/files` | `import.review` | `ImportFileList` `{run_id, files[]}` — `?disposition&kind&band&review_status&limit(≤200,d100)&offset` | openapi 2845; `ingestion.py:340` |
| `GET /admin/imports/{id}/files/{fid}` | `import.review` | `ImportFileDetail` (+ `extract`, `dedup`, `proposal`, evidence) | openapi 2873; `ingestion.py:367` |
| `GET /admin/imports/{id}/dupe-clusters` | `import.review` | `ImportDupeClusterList` | openapi 2893; `ingestion.py:391` |
| `GET /admin/imports/{id}/version-families` | `import.review` | `ImportVersionFamilyList` | openapi 2913; `ingestion.py:402` |
| `GET /admin/imports/{id}/checklist` | `import.review` | the pre-commit checklist (shape below) | openapi 2933; `review.py:983-994` |
| `GET /admin/imports/{id}/decisions` | `import.review` | the append-only decision log, newest-first | openapi 2954; `ingestion.py:426` |

**Writes (consume as-is — all honour an optional `Idempotency-Key`):**

| Endpoint | Gate | Body | Cite |
|---|---|---|---|
| `POST …/files/{fid}/decision` | `import.review` | `ImportFileDecisionRequest` `{action: accept\|correct\|exclude\|defer, after?, reason?}` (merge/split → 422) | openapi 2999; `ingestion.py:442`; `review.py:300-305` |
| `POST …/decisions` | `import.review` | `ImportBulkDecisionRequest` `{action, file_ids[]\|selector, after?, reason?}` | openapi 2968; `ingestion.py:465` |
| `POST …/merge` | `import.review` | `ImportMergeRequest` `{file_ids[≥2], effective_file_id?, reconstruct_revision_chain?, reason?}` | openapi 3029; `ingestion.py:489`; `review.py:507-644` |
| `POST …/split` | `import.review` | `ImportSplitRequest` `{target_kind: dupe_cluster\|version_family, target_id, separate_file_ids[≥1], reason?}` | openapi 3059; `ingestion.py:512`; `review.py:647-751` |
| `POST …/cancel` | `import.execute` | none → `ImportRun` | openapi 3089; `ingestion.py:548` |
| `POST …/commit` | `import.commit` | none → 202 `ImportRun` (`Committing`); 422 `commit_blocked` carries `members.blocking` | openapi 3109; `ingestion.py:536`; `service.py:395-464` |

**The one gap:** none. This is the first **web spec** for the import surface, but the API + contract
are complete. The only FE-side work is typing the **untyped-in-OpenAPI** response bodies from the
backend's real shapes (the checklist, the per-file `review` fold block, `run.counts`, the decision
log, the merge/split results — all `additionalProperties: true` in the contract). Those concrete
shapes are pinned in §5.1 from the backend code; the contract is **not** changed (no `openapi.yaml`
edit), consistent with R38/R5.

**Backend shapes the FE depends on (confirmed against code, not the loose contract):**

- **The folded per-file `review` block** (`ImportFile.review`, set from `EffectiveFileState.as_dict()`
  — `review.py:79-128,767-814`): `{disposition (included|excluded|deferred|undecided), kind
  (DOCUMENT|RECORD|UNCONFIRMED), identifier, identifier_source, type_code, clause_numbers[],
  process_names[], owner, decided, last_action, commit_ready, identifier_collidable}`. `kind =
  latest("kind") or "UNCONFIRMED"` (`review.py:148`); `commit_ready = disposition=="included" AND
  kind ∈ {DOCUMENT,RECORD}` (`review.py:100-103`). **The R10 confirmed kind lives only here, never on
  the immutable classification** (`import_classification.py:75-76`).
- **The checklist** (`review.py:983-994`): `{run_id, status, ready, blocking[], advisory:
  {star_coverage, unknown_low, kind_unconfirmed}, review:{keep_items, decided, accepted, corrected,
  excluded, deferred, undecided, kind_confirmed, commit_ready}}`. `ready = not blocking` (4 blocking
  types: `duplicate_identifier_within_import`, `collides_with_vault_doc`,
  `singleton_type_already_effective`, `ambiguous_unresolved` — `review.py:871-952`); `advisory` never
  affects `ready`.
- **`run.counts`** is stage-namespaced (`build_summary` + classify/dedup/propose blocks; `repository.py
  :273-280`, `classify.py:143`, `propose.py:201`). The exact band/queue count keys that feed the 4
  tiles + 5 tab badges are confirmed in the plan against `build_summary`/the classify+propose counts.

---

## 4. Backend — no change (the surface is complete)

This slice adds **no route, no migration, no permission key, no `openapi.yaml` edit**. The S-ing-1..5
backend is feature-complete and contracted; S-ing-4b is purely the front-end that surfaces it. The FE
types several response bodies the contract leaves as bare `object` (§5.1) without modifying the
contract — the same posture as prior front-end-only web slices (S-web-4/4b/6).

What the backend does **not** change: the append-only `import_decision` model, the newest-wins fold,
the lock-free `Reviewing` rest-state, the per-item ledger-claim commit single-flight, and the
WORM/`import_baseline` invariants are all untouched and out of this slice's reach.

---

## 5. Frontend architecture

New feature module **`apps/web/src/features/ingestion/`**. Dependency direction is acyclic:
`types.ts` ← `filters.ts` ← `hooks.ts` ← components ← `pages`. Components depend on shell primitives
(`useApi`, `useMe`, `usePermissions`, `DetailDrawer`, reference-data hooks, theme tokens) and on the
Library idioms (`FacetBar`, `Pagination`, the `useDocuments` query-grammar) by **copying the pattern**,
not importing Library internals.

### 5.1 Types — `apps/web/src/lib/types.ts` (one `// ---- S-ing-4b` block)

Mirror the OpenAPI component schemas, and pin the untyped bodies from §3:
`ImportRunStatus` (incl. additive `Committing|Completed|PartiallyCommitted` beyond the enum — the UI
**must tolerate `status` strings outside the enumerated set**), `ImportKind`, `ImportConfidenceBand`,
`ImportRun` (`counts` typed loosely as `Record<string, unknown>` with narrow accessors),
`ImportFile` (incl. the folded `review` block typed concretely per §3), `ImportFileDetail`
(`classification` w/ `evidence`, `extract`, `dedup`, `proposal.conflict_flags`), `ImportDupeCluster`,
`ImportVersionFamily`, `ImportChecklist` (the concrete `{ready, blocking[], advisory, review}` shape),
`ImportDecision`, and the request bodies (`ImportFileDecisionRequest`, `ImportBulkDecisionRequest`,
`ImportMergeRequest`, `ImportSplitRequest`, `ImportRunCreate`). Strict `noUncheckedIndexedAccess`
applies — every array index + map lookup degrades to a defined fallback.

### 5.2 Hooks — `apps/web/src/features/ingestion/hooks.ts`

Reads (React Query, `useApi().get`):
- `useImportRuns(runStatus?)` — the landing list.
- `useImportRun(runId)` — **`refetchInterval` while the run is non-terminal-and-not-Proposed/Reviewing**
  (scan in flight) **and while `Committing`**; halts at any rest/terminal state (the `useVisualDiff`
  poll-while-Pending idiom — `features/document/useVisualDiff.ts`).
- `useImportFiles(runId, {queue, facets, offset, limit})` — the paged triage rows; query key includes
  the queue + facets + page so React Query caches per view. `has_more` derived as `files.length === limit`
  (the contract returns no total; the bucket total comes from `run.counts`).
- `useImportFile(runId, fileId)` — the detail drawer (enabled only when a drawer id is set).
- `useDupeClusters(runId)` / `useVersionFamilies(runId)` — fetched once per run; joined client-side to
  rows to render "Duplicate of X" / "N versions in family" (the row list carries no membership).
- `useChecklist(runId)` — the pre-commit gate (invalidated by every decision/merge/split mutation).
- `useDecisions(runId)` — the decision-history feed (drawer + an audit-trail affordance).

Writes (`useMutation`, `useApi().send` with an `Idempotency-Key` header per the S-ing-4 "stamp the key
on a SINGLE row" rule — **one key per bulk op**; the `useDecideTask` template — `features/review/hooks.ts`):
- `useCreateImportRun()` — POST → on success navigate to `/ingestion/:runId` (the page then polls).
- `useFileDecision(runId)` / `useBulkDecision(runId)` — accept/correct/exclude/defer (+ `after` dims).
- `useMerge(runId)` / `useSplit(runId)` — structural; `onSuccess` invalidates files + clusters +
  families + checklist + run.
- `useCancelRun(runId)` / `useCommitRun(runId)` — `import.execute` / `import.commit`.

Every write's `onSuccess` invalidates the affected query keys (files, checklist, run, and — for
structural ops — clusters/families). No optimistic reshape for merge/split (D-4).

### 5.3 Routing & nav

- **Routes** (`apps/web/src/App.tsx`, as children of the operational `<AppShell>` `path="/"`):
  `ingestion` → `IngestionRunsPage`; `ingestion/:runId` → `IngestionRunPage`. Route-level gating is
  `operational ? … : <Navigate to="/setup">`; per-view permission gating is **inside** the component
  (403-calm), never at the route.
- **Left nav** (`apps/web/src/app/shell/LeftRail.tsx`): an **Import** `<NavLink>` wrapped in
  `can("import.review")` (the Compliance-entry pattern, `LeftRail.tsx:28-36`). Admin-only system key.

### 5.4 Components (one bolded `path.tsx` per component; behavior + states)

**Pages**
- **`features/ingestion/IngestionRunsPage.tsx`** — the landing: a list of runs (status badge, source
  root, created-by/at, counts summary) + a **New import** button (gated `import.execute`) opening
  `NewImportModal`. Empty → `es-empty` ("No imports yet"). 403 → calm no-access panel.
- **`features/ingestion/IngestionRunPage.tsx`** — the **four-faces** controller: reads `useImportRun`,
  switches on `run.status` → `ScanProgress` (pre-Proposed) · `ReviewCockpit` (Proposed/Reviewing) ·
  `CommitProgress` (Committing) · `RunTerminalSummary` (Completed/PartiallyCommitted/Failed/Cancelled).
  404 → calm not-found; 403 → calm no-access.

**Run-lifecycle faces**
- **`NewImportModal.tsx`** — Mantine `Modal`: `source_root` (required), OCR toggle, optional `profile`
  → `useCreateImportRun`. 409 (`active_run_id`) and 422 (bad source root) render calm inline messages.
- **`ScanProgress.tsx`** — a calm stepper of the pipeline stages with the current stage + live counts
  (polled), a Cancel button (`import.execute`). `Failed` → calm error w/ `run.error`.
- **`CommitProgress.tsx`** — progress + `counts.commit {committed, failed}` (polled to terminal).
- **`RunTerminalSummary.tsx`** — committed/failed counts, the `PartiallyCommitted` resume affordance
  (re-`commit`), and a link to the Import Report record (`run.report_record_id`) + "view in Library".

**Review cockpit**
- **`ReviewCockpit.tsx`** — composes the cockpit; owns the selection `Set<fileId>` (component state,
  not URL) and the active drawer id; queue + facets + offset live in the URL (`useSearchParams`).
- **`RunSummaryTiles.tsx`** — 4 metric tiles from `run.counts` (High auto-classified · Medium ·
  Needs-decision/Low · Kind-confirmed N/total). DP-7 glyph + label, never color-only.
- **`ImportPlanBanner.tsx`** — the drift-safe import-default explainer (informational, D-6).
- **`QueueTabs.tsx`** — Mantine `Tabs` (or segmented), 5 tabs (Needs-decision / Medium / High /
  Quarantine / Already-in-vault) with counts; writes `?queue=`. Queue→filter mapping in `filters.ts`.
- **`IngestionFacetBar.tsx`** — confidence segmented (All/High/Medium/Low) + removable facet chips
  (clause / process / type / kind), the `FacetBar` pattern; partial-patch onChange resets offset.
- **`TriageTable.tsx`** — the paged Mantine `<Table>`: header select-all-**page** checkbox + per-row
  checkbox; 9 columns (select · source file · proposed identifier · **kind** · type · clause ·
  process · confidence · actions). Cell renderers: `KindCell`, `ConfidenceCell` (bars/chip per band +
  `⚖ ambiguous`), `IdentifierCell` (effective id / "Duplicate of X" / "— suggest needed" / record
  capture-date), `TypeCell` (+ alt/ambiguous caption). Hover-revealed per-row actions vary by state
  (Accept/Correct ▾ · Merge ▾ · Classify… · Confirm). Quarantine rows render simplified (reason cell,
  no classification). Loading → skeleton rows; empty queue → `es-empty`.
- **`KindCell.tsx`** — renders the engine guess **dimmed with `?`** (`Document?` / `🔒 Record?` /
  `Unknown`) when `review.kind === "UNCONFIRMED"`, with an inline `Confirm` (sets `after.kind`); a
  confirmed kind renders a solid badge. The column header carries a `confirm required` badge. R10.
- **`BulkActionBar.tsx`** — appears when ≥1 row selected: `Confirm kind` / `Correct to type` /
  `Reassign owner` / `Set clause` / `Exclude`, each a bulk decision over the selected `file_ids`.
  **Bulk-accept-all-High** is a distinct affordance that posts a `selector: {band: HIGH}` accept (no
  row loading; does **not** confirm kind). Microcopy: "setting kind here counts as your confirmation".
- **`TriagePagination.tsx`** — offset/limit pager (100/page, the Library `Pagination` pattern driven by
  derived `has_more`) + a density (Comfortable/Compact) toggle + "Showing X–Y of N in this queue"
  (N from `run.counts`).
- **`ItemDetailDrawer.tsx`** — reuses `app/shell/DetailDrawer`; `useImportFile` → classification
  evidence (per-dimension signals + explanations), extraction status, **group members** (dedup
  membership joined to the cluster/family), proposal (identifier/IA-path/`conflict_flags`), and the
  decision history. Per-item actions: Accept / Correct (any dimension) / Exclude / Defer / Confirm-kind
  / Edit-identifier / Reassign-owner, plus **Split** (separate this file out of its group) when the
  file is in a cluster/family. (The detail read is `import.review`-gated like the rest of the page, so
  it never 403s separately once the cockpit is visible.)
- **`MergeMenu.tsx`** — the inline row `Merge ▾` (dup-of case) + the multi-select "Merge into one
  family" bulk path: pick the effective member + the `reconstruct_revision_chain` opt-in, `POST /merge`.
- **`PreCommitChecklist.tsx`** — the checklist card: each `blocking[]` entry as a danger RAG row with a
  "show items" affordance that filters the table to the offenders; advisory `star_coverage`
  (reuse `CoverageBadge`), `unknown_low`, `kind_unconfirmed` as non-blocking neutral/warning rows; the
  `review` stats. Copy: "advisory, never an auto-compliance judgment … Commit can proceed with gaps."
- **`CommitCard.tsx`** — the "On commit" card: the `review.commit_ready` ready count + progress, the
  baseline/signature(`import_baseline`)/storage/provenance definition list, and the dynamic
  **"Commit N confirmed"** button — **enabled iff `checklist.ready && review.commit_ready ≥ 1`**;
  on click `useCommitRun` → 202 → the page swaps to `CommitProgress`. A 422 `commit_blocked` renders
  the returned blockers calmly (no red error).

### 5.5 Filters — `apps/web/src/features/ingestion/filters.ts`

URL parse/serialize (the Library `filters.ts` idiom) for `queue`, the confidence segmented value, the
facet chips, and `offset`/`limit`. **Queue → API filter mapping** (the `/files` query params): Needs-
decision → `review_status=undecided` (+ Low band / conflict surfacing); Medium → `band=MEDIUM`; High →
`band=HIGH`; Quarantine → `disposition=quarantine`. **"Already in vault"** has no direct disposition in
the v1 contract — its exact mapping is resolved in the plan (§9-1).

---

## 6. SoD / authz gating in the UI (authoritative sources)

`apps/web/src/app/shell/usePermissions.ts` → `can(key)` (SYSTEM-scoped keys → no scope arg). The
server is always the backstop (full deny-or-allow; there is **no** `hidden_by_scope` partial visibility
for import — `ingestion.py:50-52`; org-wrong run → 404).

| Affordance | Shown when | Server backstop |
|---|---|---|
| Import nav entry + read of any run/cockpit | `can("import.review")` | `require("import.review")` → 403 |
| New import · Cancel run | `can("import.execute")` | `require("import.execute")` → 403; 409 if a scan is active |
| All decision / merge / split writes | `can("import.review")` | `require("import.review")` → 403; 409 if not Proposed/Reviewing |
| Commit | `can("import.commit")` | `require("import.commit")` → 403; 422 `commit_blocked` if not `ready` |

**Known, intended asymmetry (do not paper over):** a deployment may grant a reviewer `import.review`
**without** `import.execute`/`import.commit` (Mara reviews, Avery runs). When `can("import.commit")` is
false, the CommitCard renders a calm "commit is held by another role" note rather than a disabled
button with no explanation. In this install the demo admin holds all three, so the default smoke
exercises the full set.

---

## 7. States, errors, and calm-by-default (DP-6)

| Condition | UI |
|---|---|
| 403 (lacks `import.review`) | Calm no-access panel (the S-web-6 compliance pattern: `Alert`, no red error). |
| 404 (missing/foreign run) | Calm "import run not found" with a link back to `/ingestion`. |
| 409 (run not reviewable / scan active) | Calm inline message; refetch the run (it may have advanced). |
| 422 (`commit_blocked` / bad source root / bad decision) | The returned `Problem.detail`/blockers rendered inline, never a stack/red toast. |
| 423 (app not operational / latched) | The shell's existing not-operational handling. |
| Empty queue / empty runs list | `es-empty` with calm copy + the relevant primary action. |
| Loading | Skeleton rows / a calm progress affordance; never a spinner-only blank. |
| Quarantine rows | Simplified (reason + no classification); not an error state. |
| Scan/commit in flight | The dedicated progress face, polled to rest/terminal. |

Per-dimension confidence, badges, and conflict markers are **glyph + label**, never color-only (DP-7).

---

## 8. Accessibility (WCAG 2.2 AA — a ship gate; jest-axe enforced)

- The triage table uses semantic `<table>` markup (`scope="col"`, `getByRole("cell")`-friendly);
  checkboxes carry distinct `aria-label`s (header "Select all on page" vs row "Select <filename>")
  — **no duplicate `aria-label` across the bulk bar + row badges** (the S-web-6 `getByLabelText`
  single-match lesson).
- Bulk actions are keyboard-reachable (the mockup's "fully keyboard-driven"); the drawer reuses the
  focus-trap + Esc of `DetailDrawer`; queue tabs are a real tablist; the segmented/density controls
  use the in-repo `SegmentedControl` (the `FloatingIndicator` needs the `ResizeObserver` jsdom stub —
  already present in `test/setup.ts`).
- Status/confidence/coverage badges are glyph + label + `aria-label` (reuse `TaskStateBadge`/
  `CoverageBadge`). Every page test asserts `expect(await axe(container)).toHaveNoViolations()`.

---

## 9. Open implementation details (resolved in the plan, flagged here)

1. **"Already in vault" queue mapping** — no direct disposition exists in the v1 contract. Resolve in
   the plan against the backend: likely surfaced via the proposal `collides_with_vault_doc` conflict
   flag and/or an exclude-with-reason; if no clean server filter exists, ship the tab as a documented
   v1 partial (count from `run.counts`, body deferred) rather than faking a filter.
2. **Exact `run.counts` keys** for the 4 tiles + 5 tab badges — confirm against `build_summary`
   (`repository.py:273-280`) + the classify/dedup/propose count blocks; map each tile/tab to a real key.
3. **Conflict→items affordance** — how a checklist `blocking[]` entry maps to a table filter that
   surfaces the offending file ids (the blockers carry member references in `members.blocking` and the
   per-file `proposal.conflict_flags`).
4. **Merge effective-member + reconstruct opt-in UX** — the precise control set in `MergeMenu`
   (effective pick from the selected files; the per-family `reconstruct_revision_chain` checkbox).
5. **MSW fixtures** — a small multi-file run fixture (a handful of rows spanning High/Medium/Low/
   conflict/quarantine + one dupe-cluster + one version-family + a checklist with one blocking + one
   advisory) so the paged + grouping-join + gate tests stay realistic but tiny.

---

## 10. Out of scope (explicit deferrals)

- **No new backend / contract / migration / key** — the surface is complete; we only build the FE.
- **Source-root file browser** — the New-Import form takes a typed path within the configured mount;
  a directory-picker is deferred (the backend validates + 422s a bad/escaping root).
- **Live source preview / PDF.js rendition in the drawer** — the detail drawer shows extracted-text +
  evidence + proposal; a rendered page-image preview (the worker-async render path) is **deferred**
  (it would need the S-dcr-3b POST→poll→PNG pattern; not required to close UJ-2). *Why:* preview is a
  convenience, not a decision input — the evidence + extracted text suffice to triage.
- **Process-scoped reviewer ABAC** (doc 09 §9.4) — reserved-not-built in the backend; the UI treats
  import review as org-wide SYSTEM-gated. *Why:* no row-level scope filter exists server-side yet.
- **Saved facet presets / custom column config** — the facet chips + segmented confidence cover v1;
  saved presets are a follow-on. *Why:* YAGNI for the first surface.

---

## 11. Live smoke (single-user, the `demo` System Administrator)

The demo admin holds `import.review/execute/commit`, so one operator drives the whole loop:

1. Seed a small source folder under the import mount (a few docs + a dup pair + a 2-version family).
2. `/ingestion` → **New import** → the source root → submit → watch `ScanProgress` reach `Proposed`.
3. Cockpit: **Bulk-accept-all-High**; **Correct** one Medium item's type; **Confirm kind** on a few
   (per-row + bulk) — verify the Kind-confirmed tile + checklist `kind_confirmed` rise and that
   bulk-accept did **not** confirm kind; **Exclude** one dup or **Merge** the version family (pick the
   effective member); resolve the duplicate-identifier blocker.
4. The checklist flips `ready: true`; **Commit N confirmed** → `CommitProgress` → `Completed`.
5. Confirm the Rev-A Effective documents + Records appear in the **Library**, and the Import Report
   record is linked from the terminal summary.

(SoD variant, optional: with `just seed-personas`, grant Mara `import.review` only and Avery
`import.execute`/`import.commit` to exercise the held-by-another-role CommitCard copy.)

---

## 12. Verification & rhythm

- **`/check-web`** (eslint + tsc-strict + build + vitest) — the primary local gate; run the full gate
  (not just per-file vitest) before the PR (`noUncheckedIndexedAccess` catches index nits).
- **`/check-contracts`** — redocly lint (no contract change, but keep it green).
- **`/check-api`**, **`/check-migrations`** — unaffected (no backend/migration change), confirmed green.
- **`diff-critic`** agent on the branch diff before the PR — fold only confirmed findings (hunt the
  false-PASS direction: the `useMe().id` vs `sub` trap, the Idempotency-Key-per-bulk-op rule, the
  no-optimistic-merge-reshape rule, the commit-enable predicate).
- **`/pr`** → 5 green CI jobs (contracts / api / migrations / web / integration) → address any Codex
  review (reply + resolve threads) → squash-merge.

---

## 13. File inventory

**Backend:** none.

**Frontend (new) — `apps/web/src/features/ingestion/`:**
`IngestionRunsPage.tsx` · `IngestionRunPage.tsx` · `NewImportModal.tsx` · `ScanProgress.tsx` ·
`CommitProgress.tsx` · `RunTerminalSummary.tsx` · `ReviewCockpit.tsx` · `RunSummaryTiles.tsx` ·
`ImportPlanBanner.tsx` · `QueueTabs.tsx` · `IngestionFacetBar.tsx` · `TriageTable.tsx` · `KindCell.tsx`
· `BulkActionBar.tsx` · `TriagePagination.tsx` · `ItemDetailDrawer.tsx` · `MergeMenu.tsx` ·
`PreCommitChecklist.tsx` · `CommitCard.tsx` · `hooks.ts` · `filters.ts` + their `*.test.tsx`.

**Frontend (modify):** `apps/web/src/lib/types.ts` (the `// ---- S-ing-4b` block) ·
`apps/web/src/App.tsx` (routes) · `apps/web/src/app/shell/LeftRail.tsx` (the Import nav entry) ·
`apps/web/src/test/msw/handlers.ts` (import fixtures + filter-aware list handler).

**Docs (update on merge):** `docs/slice-history.md` (the S-ing-4b entry) · `CLAUDE.md` (Recent
learnings + Current status) · `docs/15-api-design.md` (note the surface is now UI-backed; no endpoint
change).

---

## 14. Risks / load-bearing invariants

- **R10 kind-confirm is a separate human act.** Bulk-accept must NOT set kind; the confirmed kind rides
  `decision.after.kind` and is never written to the classification. An unconfirmed item is silently
  skipped at commit — surface it as a prominent completeness warning, never let the UI imply it will
  commit. *(A false-PASS here would silently drop items from the baseline.)*
- **`useMe().id`, never `user.profile.sub`** for any actor/owner comparison (the S-web-5 diff-critic
  CRITICAL) — though this slice has no candidate-pool check, decided-by/owner displays must use the
  app_user id.
- **One `Idempotency-Key` per bulk op** (the S-ing-4 "stamp the key on a SINGLE row" rule) — not one
  per file. A retried bulk decision must replay as a no-op, not re-apply.
- **Merge/split is server-authoritative.** Never optimistically reshape groupings client-side; submit
  the intent, then invalidate + refetch. The backend preserves other groups' `reconstruct_revision_chain`
  flags and re-derives proposals — the UI must not assume otherwise.
- **Commit-enable predicate** = `checklist.ready && review.commit_ready ≥ 1`. Do **not** gate commit on
  zero-unconfirmed-kind (advisory) or on a clean advisory projection — that would block a legitimately
  partial-but-valid baseline.
- **Tolerate `status` strings beyond the `ImportRunStatus` enum** (`Committing|Completed|
  PartiallyCommitted` are additive) — a missing case must degrade calmly, not crash.
- **Pagination + grouping-join correctness** — the row list carries the folded `review` block but not
  cluster/family membership; the client join (clusters/families fetched once per run) must degrade to
  "—" on a missing map entry under `noUncheckedIndexedAccess`.
- **jsdom + the paged table** — keep MSW fixtures tiny so all rows of a page mount; assert by role/label.
