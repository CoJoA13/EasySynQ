# S-web-8 — the trailing drift-family UI: Drift status surface + D5 periodic-review surfaces (slice design)

> Status: designed 2026-06-10 (autonomous session; the owner's brief pre-authorized settling the
> non-register-level design calls in brainstorming). **Front-end-only** — no migration, no permission
> key, no endpoint, no `openapi.yaml` change. Consumes exactly what S-drift-1 and S-drift-3 shipped.

## 0. Design calls (settled — none register-level)

1. **Where the drift surface lives.** Considered: (A) main-`AppShell` route `/drift` + a
   `can("drift.read")`-gated LeftRail entry — the S-ing-4b `/ingestion` precedent (a SYSTEM-key-gated
   operational surface in the main shell); (B) extending the token-threaded `AdminShell` (`/admin/drift`)
   — rejected: that shell is the setup-era Users/Roles surface (token-prop-threaded, no LeftRail, no
   `useApi`), and drift is an operational read that belongs beside Import; (C) a single page with
   superseded-copies in a drawer — rejected: the D4 report is a paginated operator *recall list*
   (server `limit`/`offset`, up to 500/page), which a drawer smothers. **Chosen: (A)**, with a
   `DriftLayout` tabbed sub-route pair (the CapaLayout/AuditsLayout precedent): `/drift` = Status,
   `/drift/superseded-copies` = the D4 table page.
2. **PERIODIC_REVIEW task detail enrichment.** **None needed — front-end-only holds.** The task
   detail already carries `subject_type: "PERIODIC_REVIEW"` + `subject_id` = the document id
   (`api/workflow.py:227` dispatch; `WorkflowSubjectType.PERIODIC_REVIEW`). Doc identifier/title come
   from the existing `GET /documents/{id}` (`document.read`), loaded **best-effort**: a 403 degrades
   to a calm identity-less panel and the decision card still renders — ownership is the server-side
   decision authority (the CAPA-path lesson generalized: never make a gated read load-bearing for a
   decision the server authorizes by other means).
3. **Review-period editing.** Rows live in the existing **Control metadata** card; the edit affordance
   is a small modal (conditionally rendered — the S-web-7d reopen trap) gated by
   **`doc.capabilities.manage_metadata`** — the server-computed per-document authz answer already on
   detail reads (S-web-3), so the UI never guesses personas. Clearing the period sends an **explicit
   `null`** (the PATCH consumes `model_fields_set`; an omitted key inherits — the S-web-7d trap).
4. Corollaries found in the code: the `DecisionCard` needs a `PERIODIC_REVIEW` variant — the legal
   outcomes are **`complete` | `changes_requested`** (`approve` 422s, `services/vault/review.py:280`),
   and its 409 copy must NOT say "already decided": the periodic 409 means *no Effective version to
   confirm* (obsoleted/under-revision mid-review, `review.py:321`). "Obsolete it" is NOT a task
   outcome — the task page offers the link to the document page (S-drift-1 spec §5). No DCR deep-link
   prefill: the SPA has no DCR UI to deep-link into (re-evaluate when one lands).

## 1. Why / what

The drift family (D1–D5) is backend-complete (S-drift-1…3, head `0047`); this slice surfaces it:

| Leg | Surface | Backend (exists) |
|---|---|---|
| (a) Drift status | `/drift` Status tab + Superseded-copies tab | `GET /admin/drift/status`, `GET /admin/drift/superseded-copies` — both `drift.read` (R41; **demo holds it natively**, seeded 0047) |
| (b1) D5 task leg | `PERIODIC_REVIEW` branch on `/tasks/:id` | task detail `subject_type`/`subject_id`; `POST /tasks/{id}/decision` dispatch |
| (b2) Doc review fields | detail tiles/metadata rows + period edit modal | document serializer review fields; `PATCH /documents/{id}` (`document.manage_metadata`, `ge=1 le=120`) |
| (b3) Checklist overdue leg | `/compliance` rollup + per-row badge | checklist rows carry `overdue_review` + rollup count since S-drift-1 |

## 2. Pinned response shapes (fixtures MUST copy these — the #1 web-track false-PASS)

- **`GET /admin/drift/status`** (`services/vault/drift_report.py:120-165`, openapi `getDriftStatus`):
  `{scans: {MIRROR: <summary>|null, BLOB_REHASH: <summary>|null}, blob_coverage: {total, never_verified,
  failing, oldest_verified_at|null}, superseded_copies: {versions, copies}}`. Scan summary =
  `{status: CLEAN|DIVERGENT|FAILED, started_at, finished_at|null, counts: <OPEN bag>, triggered_by:
  beat|sync|cli}`. ⚠ Keys are **UPPERCASE enum values**; each kind is **null until its first run**
  (fresh install). ⚠ `counts` is an **OPEN bag** (§10a amendment): MIRROR carries
  `{scanned, ok, stale, tampered, …, rebuild_triggered}`, BLOB_REHASH carries `{scanned, ok,
  mismatched, missing, read_errors, stamped, full, sample_size, total_blobs}` — render generically,
  treat unknown keys as additive, never destructure a closed set. `blob_coverage.failing` = the live
  unresolved-pin count (the §10a alarm signal).
- **`GET /admin/drift/superseded-copies?limit=&offset=`** (`drift_report.py:88-117`): limit 1–500
  default 50, offset ≥0. `{total: {versions, copies}, items: [{document_id, identifier, version_id,
  revision_label, version_state: Superseded|Obsolete, current_revision_label|null, exported, printed,
  last_copy_at}]}` ordered `last_copy_at DESC`. Totals cover the FULL set, not the page.
- **Task** (`api/workflow.py:55-72`): list rows have NO `subject_type` (detail-only). A sweep-minted
  task: `stage_key="review"`, `type="PERIODIC_REVIEW"`, `action_expected="periodic_review"`,
  `assignee_user_id=null`, `candidate_pool=[<owner app_user.id>]`, `due_at` = org-midnight of
  `next_review_due` (mig 0045 definition + `review.py:165-185`).
- **Decision** `POST /tasks/{id}/decision` + `Idempotency-Key`: body `{outcome, comment?}`; outcomes
  `complete|changes_requested` else 422; non-membership/non-owner **404-collapses**; `complete` on a
  doc with no Effective version → **409** `"Document no longer has an Effective version to confirm"`.
  Success (`complete`): `{current_state: "COMPLETED", replayed, document_id, next_review_due|null,
  signature_event_id|null}` (`review.py:245-380`).
- **Document serializer** (`api/documents.py:168-171`): always emits `review_period_months: int|null`,
  `next_review_due: "YYYY-MM-DD"|null` (DATE), `last_reviewed_at: <datetime>|null`, `review_state:
  "current"|"due_soon"|"overdue"|null` (derived server-side, 30-day `due_soon` window, org tz). ⚠ The
  PATCH/create response's `effective_from` is null (read-paths only) — **invalidate + refetch after
  PATCH, never `setQueryData` from the PATCH response.**
- **`PATCH /documents/{id}`** (`document.manage_metadata`): body field `review_period_months: int|null`
  (1–120); explicit `null` clears + recomputes `next_review_due` server-side.
- **Checklist** (`services/reports/checklist.py`): rollup `{total, covered, partial, gap,
  overdue_review}`; rows gain `overdue_review: bool`. Orthogonal flag — row `status` unchanged.
- **`/me`.id** = `app_user.id` — the ONLY identity for any membership comparison (never
  `user.profile.sub`).

## 3. The drift surface (`features/drift/`)

- **Routing/nav:** `App.tsx` gains `/drift` (`DriftLayout`: index `DriftStatusPage`,
  `superseded-copies` → `SupersededCopiesPage`); `LeftRail` gains `{can("drift.read") && <NavLink
  to="/drift" label="Drift" …/>}` after Import.
- **Hooks:** `useDriftStatus()` (key `["drift-status"]`), `useSupersededCopies(limit, offset)` (key
  `["drift-superseded", limit, offset]`) — both `retry: false` + the `forbidden` flag
  (`ApiError.status === 403`) → calm no-access Alert (the compliance/audits pattern).
- **`DriftStatusPage`:** three sections —
  1. Two **scan cards** (Mirror scan · Blob integrity): status Badge (CLEAN green / DIVERGENT red /
     FAILED orange), started/finished, `triggered_by`, and the counts bag rendered **generically**
     (sorted key→value rows; zero-suppression optional but unknown keys always shown). A `null` kind
     renders an honest "Never run yet" card, not a crash.
  2. **Blob coverage** card: total / never-verified / **failing** (highlighted red when > 0 — "unresolved
     integrity findings, re-alarming until restored") / oldest stamp.
  3. **Superseded copies headline** `{versions, copies}` + a link to the tab.
- **`SupersededCopiesPage`:** table — Identifier (→ `/documents/:id`), Superseded rev, State, Current
  rev (`—` when null/obsoleted), Exported, Printed, Last copy (date-time). Mantine `Pagination` over
  `total.versions` with server `limit`/`offset` (NO virtualization — the S-ing-4b rule). Calm empty
  state ("No outstanding copies of superseded versions.").

## 4. The PERIODIC_REVIEW task leg (`features/review/`)

- **`ReviewApprovePage`:** add `isPeriodic = task?.subject_type === "PERIODIC_REVIEW"` as a third
  branch. ⚠ The periodic branch **must not call `useWorkflowInstance`** (document-path-only; the
  subject id is already on the task detail) — `useWorkflowInstance(!isCapa && !isPeriodic && task ?
  task.instance_id : null)`; the DOCUMENT and CAPA branches stay **byte-identical**.
- **`PeriodicReviewContext`** (new, left column): loads `useDocument(task.subject_id)` best-effort —
  on success: identifier/title, governing revision + effective date, review period, last reviewed,
  next due + `ReviewStateBadge`, link to the document page, and the dimmed "Obsolete it" note linking
  to the document page (where the obsolete action lives). On 403: a calm "document details not
  visible to you" panel — the decision card still renders (the server re-checks ownership).
- **`DecisionCard`:** `subjectType` union gains `"PERIODIC_REVIEW"`; that variant swaps the radio set
  to `complete` ("Confirm — no change needed") and `changes_requested` ("Changes needed — a revision
  is required"); signature checkbox required for `complete` (label "… meaning: review confirmed");
  comment required client-side for `changes_requested` (sibling UX consistency); 409 copy for this
  subject: "The document no longer has an Effective version to confirm — it may have been obsoleted
  or be under revision." DOCUMENT/CAPA rendering and the decide POST stay byte-identical.
- **`hooks.ts`:** `DecideInput.subjectType` union extended; `onSuccess` gains the PERIODIC_REVIEW
  branch → invalidate `["document", subjectId]` + the tasks list (clock reset must show on the doc
  page). The inbox row is untouched (it renders `action_expected ?? type` generically —
  `"periodic_review"` is consistent with sibling raw tokens).

## 5. Doc-detail review fields (`features/document/`)

- **`lib/types.ts`:** `DocumentSummary` gains the four always-emitted fields (`review_period_months:
  number | null`, `next_review_due: string | null`, `last_reviewed_at: string | null`, `review_state:
  "current" | "due_soon" | "overdue" | null`); `ChecklistRow.overdue_review: boolean`;
  `ChecklistRollup.overdue_review: number`.
- **`ReviewStateBadge`** (new, shared): current → green "Current", due_soon → yellow "Due soon",
  overdue → red "Overdue"; null → not rendered.
- **The deferred "Days to review" tile:** 4th Tile "Next review" — value = days-to ("42 days" /
  "Overdue 3 days" / "—" when unscheduled; client-computed from the DATE, honest ±1-day tz
  approximation), sub = the date + `ReviewStateBadge` (server-derived = authoritative). Grid becomes
  `cols={{ base: 1, sm: 2, md: 4 }}`.
- **`ControlMetadata`:** three new rows — Review period ("24 months" / "—"), Next review (date +
  badge), Last reviewed (date). Stays presentational: an optional `onEditReviewPeriod?: () => void`
  prop renders a small "Edit" affordance on the Review-period row; the detail page passes it iff
  `doc.capabilities?.manage_metadata`; the S-web-2 drawer passes nothing (capabilities are
  detail-only) and just gains the read rows.
- **`ReviewPeriodModal`** (new): NumberInput 1–120 months + a "No scheduled review" clear toggle;
  PATCH always includes the key explicitly (`{review_period_months: N}` or `{… : null}`); on success
  invalidate `["document", id]` and close. **Conditionally rendered** (`{open && <Modal …>}`).

## 6. Checklist overdue leg (`features/compliance/`)

- Rollup line gains a plain-text counter: `⏰ Review overdue: {rollup.overdue_review}` (plain legend —
  no aria-label that could collide with row badges, the S-web-6 trap).
- New "Review" column after Status: `overdue_review ? <Badge color="red">Overdue</Badge> : "—"`.
- Update the checklist MSW fixture to the REAL serializer shape (it has carried these fields since
  S-drift-1; the current fixture predates them).

## 7. Error handling / edge cases

- All three gated reads (`drift.read` ×2, checklist) → calm-403 via the `forbidden` flag; direct-URL
  access without the key is the test case, not just nav hiding.
- Status page: each scan kind nullable; counts open-bag (unknown-key fixture test); `failing > 0`
  highlight; FAILED scan renders as honestly failed, not divergent.
- Periodic decide: 404 → the existing "task unavailable" panel; 409 → the subject-specific copy (§4);
  422 unreachable from the UI (outcome set is constrained).
- PATCH clear: explicit null; modal closes only on success; server recompute means **no client-side
  next_review_due math is ever persisted or trusted**.
- The periodic task page never blocks the decision card on the doc read (403 degradation, §4).

## 8. Testing (TDD per task; baseline 499 web tests)

- Fixtures pinned to §2 (copy the openapi `getDriftStatus` example + serializer dicts; `satisfies`
  the TS types). MSW defaults for both drift endpoints added to `test/msw/handlers.ts`; `docFixture`
  gains the four review fields; checklist fixture gains the overdue fields.
- Key adversarial cases: scans-null empty state; unknown counts key renders; failing>0 highlight;
  pagination drives `offset`; periodic branch never hits `/workflow-instances/*` (a sentinel MSW
  handler that fails the test if called); doc-403 still renders the decision card; decide posts
  `{outcome: "complete"}` + Idempotency-Key; 409 copy; modal clear sends explicit null; edit
  affordance absent without `capabilities.manage_metadata`; reopened modal is pristine (the S-web-7d
  reopen test); LeftRail gating for `drift.read`; jest-axe on the new pages.
- Full `/check-web` before the PR; full vitest with `--pool=forks` + `singleFork` for a clean signal.

## 9. Live smoke (Chrome MCP, http://localhost — pre-merge)

1. Rebuild web (`docker compose … up -d --build web`) + hard refresh.
2. **The inherited S-drift-3 obligation:** as logged-in `demo` (holds `drift.read` natively), open
   `/drift` → real `MIRROR`/`BLOB_REHASH` rows render (Beat has been ticking) = the authed-200 leg of
   `GET /admin/drift/status`; open the Superseded-copies tab = the authed-200 leg of
   `GET /admin/drift/superseded-copies`. If the D4 set is empty, mint one via the worker heredoc
   (create→release→`render_dynamic_copy` export→revise+release) and re-load.
3. **D5 loop:** grant demo the SYSTEM document overrides (LIVE login's app_user row, org AHT) + make
   demo's app_user the owner of a test doc; set the review period via the modal (PATCH leg); backdate
   `next_review_due` via heredoc; run the sweep; `/tasks` shows the periodic task; checklist (granted
   `report.compliance_checklist.read` override) shows the overdue rollup + row badge; decide
   `complete` with signature; doc page shows the reset `next_review_due`/`last_reviewed_at`; checklist
   clears.

## 10. Non-goals

- The PDCA dashboard (deferred until acks/objectives land — owner's standing call).
- A Library "Next review" column (mockup line 2196 shows one — a residual for a later slice).
- DCR deep-link prefill on `changes_requested` (no SPA DCR UI exists; the doc-page link suffices).
- Inbox label prettification; any backend/key/contract change; review-period editing anywhere but the
  doc detail page.

## 11. Docs in-PR

`docs/slice-history.md` entry · CLAUDE.md Current-status pointer (+ Recent-learnings line). No
`docs/15`/`openapi.yaml` change (front-end-only; both drift GETs were documented in-PR by S-drift-3).
