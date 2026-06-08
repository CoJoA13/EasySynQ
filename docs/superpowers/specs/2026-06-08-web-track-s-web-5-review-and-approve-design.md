# S-web-5 — Review & Approve (closes UJ-3) — Design

> **Status:** DRAFT for owner review (2026-06-08).
> **Slice:** S-web-5 (web track). **Closes UJ-3:** author → review → approve → release.
> **Branch (on approval):** `feat/s-web-5-review-and-approve`. **Migration head:** `0044` → **no new migration**.
> Builds on S-web-3 (authoring + DP-6 capability gating) and S-web-4/4b (the document detail page +
> redline/visual-diff). Authoritative grounding: doc 07 (authz/SoD), doc 11 (review UX + stepper),
> doc 15 §8.8 (tasks/decision), the owner-approved `mockup/easysynq-mockup.html` (Approvals stepper +
> Review-&-Approve nav), and the live code (`api/workflow.py`, `services/workflow/`, `api/documents.py`).

---

## 1. Goal & user journey

S-web-5 ships the **reviewer/approver half of the document lifecycle** — the surface S-web-3/4 deliberately
deferred. After this slice an org can drive the full controlled-document loop **in the browser, end to end**:

1. **Priya (Author)** authors a draft, maps a clause, and **submits for review** (S-web-3, shipped).
2. **Ken (Approver)** sees the document in his **My-Tasks inbox**, opens the **Review & Approve** page
   (the redline of what changed + a decision card), and **approves** or **requests changes / rejects**.
3. **Mara (Quality Manager)** **releases** the Approved version → the document goes **Effective** and the
   read-only mirror is regenerated.

Every actor is **distinct** (SoD-1: an author cannot approve their own version; SoD-2: an author — and,
unless configured otherwise, the sole prior approver — cannot release). These blocks are **non-overridable
HARD_DENY** at the server PEP; the UI's job is only to **quiet-absent** the affordances the actor may not use
(DP-6 — no dead buttons), not to enforce.

## 2. Decisions baked into this design (owner-approved 2026-06-08)

| # | Decision | Choice |
|---|----------|--------|
| 1 | **Surfaces** | **Both** — a `/tasks` inbox route + a per-task Review & Approve page, **and** an Approvals tab/stepper card on the `/documents/:id` detail page. |
| 2 | **Release in scope** | **Yes** — full UJ-3. Release gates on the already-SoD-2-enriched `capabilities['release']`. |
| 3 | **Discovery endpoint gate** | **`document.read` on the subject** (matches the existing `GET /workflow-instances/{id}` impl). Migration-free, **no new permission key**. |
| 4 | **SoD button gating** | **Approve/Reject** ⇐ task **candidate-pool membership** (task visible via `GET /tasks`); **Release** ⇐ `capabilities['release']`. The server 403 (`sod_violation`) is the real enforcement. |

**Net shape of the slice:** front-end + **one** thin, migration-free read endpoint and **one** additive
OpenAPI path. No new permission key (R38/R5 untouched), no new ORM model, no migration.

## 3. What already exists (the delta is small)

All of the **writes** and most of the **reads** UJ-3 needs are already contracted **and implemented**:

**Reads (consume as-is):**
- `GET /tasks?assignee=me&state=&type=&instance_id=` → `Task[]` — the **self-scoped inbox** (caller is
  assignee *or* in `candidate_pool`; **no permission key** — self-scoped). `api/workflow.py:132`.
- `GET /tasks/{id}` → `Task` (404-collapses if not assignee/candidate). `api/workflow.py:161`.
- `GET /workflow-instances/{id}?expand=tasks` → `WorkflowInstance` + `tasks[]`; gated `document.read`
  **on the subject doc**. `api/workflow.py:242`.
- `GET /documents/{id}` → carries the per-doc **`capabilities`** block; `capabilities['release']` is
  **already SoD-2-enriched** (`api/documents.py:381`). `GET …/versions/{vid}/diff?from=` carries
  `DiffProvenance.signatures[]` (`{meaning, signer_user_id, signed_at}`) — the completed-cycle signature
  timeline. `GET /directory/users` → `{id, display_name}` (name resolution for pool/decider UUIDs).

**Writes (consume as-is):**
- `POST /documents/{id}/submit-review` (gate `document.submit`) — instantiates the approval instance + the
  one APPROVE task. (S-web-3 already calls this.)
- `POST /tasks/{id}/decision` — **the canonical approve/reject trigger** (there is intentionally **no**
  `/documents/{id}/approve`). Body `Decision{outcome: approve|changes_requested|reject, comment,
  effective_from}`; `Idempotency-Key` header. `approve` → `document.approve` (sig-hook) + emits
  `signature_event(meaning=approval)` + instance `APPROVED`; `changes_requested|reject` → `document.review`
  + instance `REJECTED_TO_DRAFT` (comment **required**, no signature). SoD-1 at PEP → 403 `sod_violation`;
  409 if already decided. Returns `DecisionResult`.
- `POST /documents/{id}/release` (gate `document.release`, **SoD-2 at PEP**, sig-hook) — separate endpoint,
  SERIALIZABLE cutover, emits `signature_event(meaning=release)`, regenerates the mirror.

**The one gap:** there is **no `document → its approval instance` lookup**. The resolver
(`repository.find_nonterminal_instance` / `list_instance_tasks`) exists but is **unexposed** for documents
(`GET /workflow-instances/{id}` needs the *instance* id, not the *document* id). The stepper needs it.

## 4. Backend — the single addition: the approval-discovery endpoint

### 4.1 Route
```
GET /api/v1/documents/{document_id}/approval        →  WorkflowInstance (tasks always expanded) | null
```
- **Gate:** `document.read` on the subject document (same scope-resolution as
  `api/workflow.py::get_instance_endpoint` — folder_path + document_level → `ResourceContext` → `enforce`).
  Reuses the **existing** key; no catalog change. (The catalog has **no `task.*`/`workflow.*` keys** —
  `api/workflow.py` docstring line 8 — so `document.read` is the correct, real-world gate.)
- **Retrieval:** the **latest** `workflow_instance` for `(org, subject_type=DOCUMENT, subject_id=document_id)`
  ordered by `started_at desc, limit 1`, **with its tasks**. *Not* the engine-"non-terminal"-filtered query:
  `lifecycle.release` does **not** close the instance (it lingers as `current_state="APPROVED"` for an
  Effective doc — which we *want* to show), and a `NEEDS_ATTENTION` instance (empty pool) must still be
  surfaced. → add **one** repo helper `latest_instance_for_subject(session, org_id, subject_type, subject_id)`.
- **Calm empty:** a document with no approval cycle (never submitted, or a fresh Draft) returns **`null`**
  with **200** — `null` (not 204) so the React-Query `queryFn` gets `null`, never `undefined` (TanStack v5
  throws on `undefined`). `404` only for a missing/cross-org document; `403` for no `document.read`.
- **Placement:** add to `api/workflow.py` (it already owns the `_instance`/`_task` serializers and the exact
  `document.read`-on-subject gate pattern), tagged `documents` for contract grouping. Returns
  `_instance(instance, tasks)` (the **identical** shape as `GET /workflow-instances/{id}?expand=tasks`).

### 4.2 OpenAPI (additive)
Add the path under `/documents/{document_id}/approval` reusing the **existing** `WorkflowInstance` schema:
```yaml
/documents/{document_id}/approval:
  get:
    tags: [documents]
    operationId: getDocumentApproval
    summary: "The document's current approval cycle (instance + tasks), or null if none. Gated document.read."
    parameters:
      - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
    responses:
      "200":
        description: "The latest workflow instance for the document (tasks expanded), or null."
        content:
          application/json:
            schema:
              oneOf:
                - { $ref: "#/components/schemas/WorkflowInstance" }
                - { type: "null" }
      "403": { $ref: "#/components/responses/ProblemResponse" }
      "404": { $ref: "#/components/responses/ProblemResponse" }
```

### 4.3 What the backend does NOT change
No migration, no new ORM model, no new permission key, no change to `submit-review`/`decision`/`release`/the
welded S5 single-stage `decide()` path. The discovery endpoint is a pure read over existing rows.

## 5. Frontend architecture

New feature module `features/review/` for the reviewer surfaces; the doc-centric stepper + Approvals card
live in `features/document/` (reused by the doc page). Dependency direction stays one-way and acyclic:
`features/review → features/document → lib`; release mutation rides `features/authoring` (doc-lifecycle home).

### 5.1 Types (`lib/types.ts`, additive)
```ts
export type TaskState = "PENDING" | "CLAIMED" | "DONE" | "SKIPPED" | "ESCALATED" | "EXPIRED";
export type TaskType  = "APPROVE" | "REVIEW" | /* …contract enum… */ "DCR_TRIAGE";
export interface Task {
  id: string; instance_id: string; stage_key: string;
  type: TaskType; state: TaskState;
  assignee_user_id: string | null; candidate_pool: string[] | null;
  action_expected: string | null; due_at: string | null;
}
export type WorkflowInstanceState =       // free-form Text server-side; treat as open string, NOT an enum
  | "IN_APPROVAL" | "APPROVED" | "REJECTED_TO_DRAFT" | "NEEDS_ATTENTION" | (string & {});
export interface WorkflowInstance {
  id: string; definition_id: string; definition_version: number;
  subject_type: string; subject_id: string;
  current_state: WorkflowInstanceState;
  started_at: string | null; revision: number;
  tasks?: Task[];
}
export type DecisionOutcome = "approve" | "changes_requested" | "reject";
export interface DecisionBody { outcome: DecisionOutcome; comment?: string; effective_from?: string; }
export interface SignatureEventSummary {
  id: string; meaning: string; method: string;
  content_digest: string | null; auth_context: Record<string, unknown> | null;
  reauth_at: string | null; crypto_signature: string | null;
}
export interface DecisionResult {
  task_id: string; instance_id: string; stage_key: string;
  outcome: DecisionOutcome; decided_at: string | null; decided_by: string;
  signature_event: SignatureEventSummary | null; comment: string | null;
}
```

### 5.2 Hooks
- `features/document/useDocumentApproval.ts` — `useDocumentApproval(documentId, enabled)` →
  `useQuery(["document-approval", documentId])` → `GET /documents/{id}/approval`. Returns
  `WorkflowInstance | null`. A **403** (no `document.read`) renders quiet (DP-6), like `useVersionDiff`.
- `features/review/hooks.ts`:
  - `useTasks(filters)` → `useQuery(["tasks", filters])` → `GET /tasks?...` (default `state=PENDING`).
  - `useTask(taskId)` → `useQuery(["task", taskId])` → `GET /tasks/{id}` (404 → "not found / not yours").
  - `useDecideTask()` → `useMutation` → `POST /tasks/{id}/decision` (sends `Idempotency-Key`: a per-mount
    UUID so a double-submit replays rather than 409s). `onSuccess` invalidates
    `["task", id]`, `["tasks"]`, `["document", documentId]`, `["document-approval", documentId]`,
    `["document-versions", documentId]`.
- `features/authoring/hooks.ts` — add `useReleaseDocument()` next to `useSubmitReview` (same template):
  `POST /documents/{id}/release` (body `{}` — sole Approved version), `onSuccess` → `invalidateDocument(id)`
  **plus** `["document-approval", id]`.

### 5.3 Routing & nav
- `App.tsx`: **swap** the `tasks/:id` `Reserved` stub → `<ReviewApprovePage />`; **add**
  `<Route path="tasks" element={<TasksInbox />} />` (both inside the `AppShell` route group).
- `app/shell/LeftRail.tsx`: add a **"Review & Approve"** `NavLink` to `/tasks`
  (`active={pathname.startsWith("/tasks")}`), placed above the PDCA clause sections, with an optional
  pending-count badge from `useTasks({ state: "PENDING" })` (count is a nice-to-have, not load-bearing).

### 5.4 Components

**`features/review/TasksInbox.tsx`** — the `/tasks` work queue (doc 11 §5.2; mockup My-Tasks card).
- A table: **Task** (type glyph + `action_expected`/stage), **Document** (identifier + title — resolved via
  a light `GET /documents/{id}` per row *or* a join; see §9 "name/title resolution"), **State**
  (non-color `TaskStateBadge`), **Due** (may be blank — the single-stage DOCUMENT path never sets `due_at`).
- Row → navigates to `/tasks/{task.id}`. URL-driven filter (`?state=`) via `useSearchParams` (LibraryPage
  pattern). Calm states: loading (Loader), empty ("No tasks in your queue."), 403 → quiet.

**`features/review/ReviewApprovePage.tsx`** — the per-task focus page (doc 11 §5.5; mockup §SoD bar).
- `useParams().id` → `useTask(id)`. From the task, derive `instance_id`; resolve the **subject document**
  (the task carries `instance_id`, not the doc id → fetch `GET /workflow-instances/{instance_id}` *or*,
  simpler, the decision response/redline path is keyed by the document; see §9). Layout = **two panes**:
  - **Left — "What changed":** the `VersionCompare`/`RedlineViewer` redline of the version under review vs
    its predecessor (reuse verbatim; `read_draft` 403 → quiet), the change reason, and the
    `ApprovalStepper`.
  - **Right — the `DecisionCard`** (sticky).
- The decision controls render **only when the task is visible & decidable** (PENDING and the caller is in
  `candidate_pool`/assignee — which, by construction of `GET /tasks`/`GET /tasks/{id}`, is *always* true if
  the task loaded). A DONE/terminal task shows a read-only "Decided" summary, not the form.

**`features/review/DecisionCard.tsx`** — the decision form (doc 11 §5.5; WCAG-critical).
- A **radiogroup** (`role="radiogroup"`, labeled): **Approve** / **Request changes** / **Reject**.
- A **comment** `Textarea` that is **conditionally required** (non-empty) for *Request changes* / *Reject*
  (server 422s otherwise); `aria-required`/`aria-describedby` wired to the rule text; optional for Approve.
- A **DP-10 signature slot**: "Signing as **{display_name}** — {role}", a "meaning = **approval**" confirm
  checkbox, and the honest "v1 — single-factor logged confirmation" caption (no real e-sig in v1; the
  `signature_event` is the audit record). Approve only.
- An **SoD reassurance** badge when the caller is a non-author candidate: "⚖ SoD OK — you are not the author"
  (mockup line 2761). (We never need to show a *blocked* state here: SoD-1 excludes the author from the pool,
  so the author never reaches a decidable task.)
- Footer: **Submit decision** (loading state) + **Cancel / back to inbox**. Error handling: 403
  `sod_violation` → "You can't approve this version (separation of duties)."; 409 → "This task was already
  decided." (then refetch); 403 `step_up_required` → "Re-authentication required." (tolerated — v1 won't
  emit it, but the branch exists). On success → toast + navigate back to inbox (or show the read-only
  Decided summary).

**`features/document/ApprovalStepper.tsx`** — presentational vertical stepper (doc 11 §4.4; mockup
`.es-stepper`). Renders an **ordered node list derived from the instance + tasks + the document's own
state** — *not* a hardcoded 5-node spine (the single-stage path has one approval node today; the mockup's
multi-stage variant is aspirational). Node model:
| Node | Source | `is-done` | `is-current` | `is-rejected` |
|------|--------|-----------|--------------|---------------|
| **Submitted for review** | instance exists | always (instance present) | — | — |
| **Quality approval** (per APPROVE task) | the task + instance state | task `DONE` & instance `APPROVED` | task `PENDING` (label "Awaiting {pool names}") | instance `REJECTED_TO_DRAFT` (label "Changes requested by {decider}") |
| **Released → Effective** | the **document** state | doc `Effective` (sub: `effective_from`) | doc `Approved` (awaiting release) | — |
- Each node: a marker (✓ done / number current / · pending / ✕ rejected — **glyph + label, never
  color-only**, DP-7), a title, and a sub line ("Actor (Role) · date" where resolvable; actor =
  `assignee_user_id` for the decided approval, resolved via `useUserDirectory`). `aria-current="step"` on the
  current node; the stepper is a labeled `nav`/`ol`. Dates: `started_at` (submitted) + `effective_from`
  (effective); the exact approval/release signer+date is **best-effort folded from `DiffProvenance.signatures`**
  when the page already has the redline data (optional — actor+status from the instance is the load-bearing part).

**`features/document/ApprovalsTab.tsx`** — the doc-page card (doc 11 §5.3; mockup lines 2601-2653). Wraps the
`ApprovalStepper` + the **contextual actions**:
- **Release** button (Mantine, gated `doc.capabilities.release === true` **and** `doc.current_state ===
  "Approved"`) → `useReleaseDocument`. Quiet-absent otherwise (DP-6 — the version author / sole approver get
  no button because `capabilities['release']` is already SoD-2-false for them).
- **"Review & approve →"** link to `/tasks/{taskId}` when the caller has the open APPROVE task (the instance's
  PENDING task whose `candidate_pool`/assignee includes the caller — derived from the discovery response +
  the caller's `sub`).
- Mounted as a new section card in `DocumentDetailPage.tsx` (it's section-card-based, not a literal Tabs
  strip — the "tab" naming follows `HistoryTab`/`WhereUsedTab`). Renders nothing/"No approval activity yet."
  for a Draft with no instance.

**`features/document/TaskStateBadge.tsx`** — the DP-7 (glyph+label, never color-only) badge for `TaskState`
and `WorkflowInstanceState`, mirroring `StateBadge`. (`current_state` is free-form Text — render unknown
values verbatim, do not validate against an enum.)

### 5.5 The author-side notice stays
`AuthorActions.tsx`'s `InReview` "Awaiting review" Alert is unchanged (it's the *author's* view). The
approver's decision surface is entirely separate (the `/tasks` page + the doc-page `ApprovalsTab`), so the
two never collide. No change to `AuthorActions`.

## 6. SoD & DP-6 gating in the UI (authoritative sources)

| Affordance | Shown when | Server backstop |
|------------|-----------|-----------------|
| **Approve / Reject** (DecisionCard) | the task loaded via `GET /tasks`/`GET /tasks/{id}` (⇒ caller ∈ candidate_pool/assignee) **and** task is `PENDING` | `POST /tasks/{id}/decision` → SoD-1 HARD_DENY at PEP (403 `sod_violation`) |
| **Release** (ApprovalsTab) | `doc.capabilities.release === true` **and** state `Approved` | `POST /documents/{id}/release` → SoD-2 HARD_DENY at PEP (403) |
| **Tasks nav / inbox** | always (the inbox is self-scoped & empties to "no tasks") | n/a (self-scoped read) |

**Known, intended asymmetry (do not paper over):** an approver who holds `document.approve` only via a
**SYSTEM override** (not the Approver/QMS-Owner *role*) is **not** in the role-resolved `candidate_pool` →
never sees the task in `/tasks` and 404s on `GET /tasks/{id}`. Role membership is the canonical approver
definition (doc 07 / `0009` seed). The UI correctly shows them nothing; the override path is a server-only
back door, not a UI affordance.

## 7. States, errors, and calm-by-default

- **403** anywhere (read_draft on the redline, document.read on discovery) → quiet "no access" text (DP-6),
  never a red error.
- **404** on `GET /tasks/{id}` → "This task doesn't exist or isn't assigned to you." (sensitive collapse).
- **409** on decision → "Already decided" + refetch (idempotent replay handled by the per-mount key).
- **`null`** discovery (no cycle) / Draft → "No approval activity yet." (calm, not empty-error).
- **`NEEDS_ATTENTION`** instance (submitted, empty approver pool) → the stepper shows the approval node as
  "Awaiting an approver — none assigned" (honest; an admin must grant the Approver role).

## 8. Accessibility (WCAG 2.2 AA — a ship gate; `jest-axe` enforced)

- DecisionCard radiogroup: `role="radiogroup"` + accessible name; each option focusable; the
  conditionally-required comment uses `aria-required` + `aria-describedby` pointing at the live rule text;
  validation errors are programmatically associated and focus-managed.
- ApprovalStepper: a labeled `nav` (or `ol`); `aria-current="step"` on the current node; status conveyed by
  **glyph + text**, never color alone; `prefers-reduced-motion` collapses any advance animation.
- All interactive targets ≥ 24×24 CSS px; visible focus rings (theme default); the inbox table has proper
  `<th scope="col">` headers and a caption/aria-label.
- Every new component carries a `jest-axe` `toHaveNoViolations` assertion (the suite runs MSW with
  `onUnhandledRequest: "error"`).

## 9. Open implementation details (resolved in the plan, flagged here)

- **Task → document identity for the inbox/review page.** A `Task` carries `instance_id`, not the document
  id/identifier. Resolution path: `GET /workflow-instances/{instance_id}` → `subject_id` (the document id) →
  `GET /documents/{id}` for identifier/title/redline. The inbox resolves a small set; we'll batch/memoize
  (and may add a thin `subject` hint later if it proves chatty — out of scope now).
- **Name resolution.** `assignee_user_id`/`candidate_pool` UUIDs → `display_name` via the existing
  `useUserDirectory` (`GET /directory/users`). The stepper's "submitted by" actor = the version author
  (`useDocumentVersions` latest `author_user_id`) on the doc page; on the standalone review page the
  submitted node may show date-only.
- **Idempotency-Key.** A `crypto.randomUUID()` generated once per DecisionCard mount, resent on retry, so a
  network-retry replays the recorded outcome instead of 409-ing.

## 10. Out of scope (explicit deferrals)

- **Acknowledgements** tab/coverage (mockup §Acknowledgements), the **Audit** tab — separate slices.
- **Multi-stage / quorum / routing / periodic-review / SLA-escalation / claim / reassign** — the engine
  (`engine.py`) supports them for CAPA/DCR, but the DOCUMENT path is welded single-stage (S5); we render
  whatever stages the instance has (today: one) and do **not** migrate documents onto `engine.py`.
- **A generic My-Tasks inbox** spanning CAPA/AUDIT/DCR task types — the inbox is built document-first
  (filters to APPROVE/REVIEW document tasks); a generic-enough table shell is kept, but cross-domain task
  rendering is a later slice.
- **A manager/QM cross-user "all open approvals" oversight view** — would need a **new permission key**
  (R38 owner-level call); explicitly **not** in S-web-5.
- **Real Part-11 e-signatures / MFA step-up (`acr=mfa`)** — the signature slot is the v1 logged-confirmation;
  the `step_up_required` 403 branch is *tolerated* in the UI but the backend won't emit it in v1.

## 11. Live smoke (the 3-distinct-user loop)

SoD-1/2 are non-overridable → the loop needs three users. Fixture: `just demo-user` + `just seed-personas`
(re-run after any `just down` — Keycloak is ephemeral) → `priya` (author), `ken` (Approver role → in the
pool), `mara` (QMS Owner → releaser), all `Demo-Password-1`, org `AHT`.
1. **priya**: create a doc → check-in → map a clause → submit for review (or reuse the persisted
   `SOP-PUR-001` 2-version demo doc; if owned by `demo`, author a fresh one as priya).
2. **ken**: `/tasks` shows the APPROVE task → open → review the redline → **Approve**.
3. **mara**: open the doc → ApprovalsTab → **Release** → doc goes **Effective**.
Verify the stepper advances and the state badges flip. **CDP screenshots choke on the doc detail page in
this Chrome** → verify Ready/Effective states via the **DOM/network** (read the JSON of
`GET /documents/{id}` + `…/approval`).

## 12. Verification & rhythm

- `/check-web` (eslint + tsc + build + **vitest incl. jest-axe**), `/check-api` (ruff + mypy-strict +
  pytest unit + the new discovery-endpoint integration test), `/check-contracts` (redocly lint on the new
  path), `/check-migrations` (no-op — no migration, but the round-trip must still be green).
- New **API tests**: unit/integration for `GET /documents/{id}/approval` — 200-with-instance (+tasks),
  200-`null` (no cycle), 403 (no `document.read`), 404 (missing/cross-org); the latest-instance ordering
  (two cycles → returns the newer); a `NEEDS_ATTENTION` instance is returned (not skipped). Integration
  assertions are **run-scoped / delta-based** (shared session DB).
- New **web tests**: TasksInbox (rows/empty/403), ReviewApprovePage (decidable vs read-only), DecisionCard
  (conditional-required comment, 403/409 branches, a11y), ApprovalStepper (node derivation per state),
  ApprovalsTab (release gated by capabilities, "Review & approve" link presence), routing swap.
- Run the **`diff-critic`** agent on the branch diff before the PR. PR → green CI (5 jobs) → squash-merge.

## 13. File inventory

**Backend (modify):**
- `apps/api/src/easysynq_api/services/workflow/repository.py` — add `latest_instance_for_subject(...)`.
- `apps/api/src/easysynq_api/api/workflow.py` — add `GET /documents/{document_id}/approval` (tag `documents`).
- `packages/contracts/openapi.yaml` — add the `/documents/{document_id}/approval` path (reuses
  `WorkflowInstance`).
- `apps/api/tests/{unit,integration}/…` — discovery-endpoint tests.

**Frontend (new):**
- `features/review/{TasksInbox,ReviewApprovePage,DecisionCard,hooks}.{tsx,ts}` (+ `.test.tsx`).
- `features/document/{ApprovalStepper,ApprovalsTab,TaskStateBadge,useDocumentApproval}.{tsx,ts}` (+ tests).

**Frontend (modify):**
- `lib/types.ts` — Task/WorkflowInstance/Decision/DecisionResult/SignatureEventSummary.
- `App.tsx` — swap `tasks/:id`, add `tasks`. `app/shell/LeftRail.tsx` — the nav entry.
- `features/authoring/hooks.ts` — `useReleaseDocument`. `features/document/DocumentDetailPage.tsx` — mount
  `ApprovalsTab`.

**Docs (update on merge):** `docs/slice-history.md` (the S-web-5 entry), `CLAUDE.md` (Recent learnings +
Current status), `docs/15-api-design.md` (note the discovery read is implemented, gate `document.read`).

## 14. Risks / load-bearing invariants

- **SoD-1/2 are HARD_DENY; SoD-1 is non-overridable** — the UI is advisory only; the PEP is the gate.
  Never present a self-approve/self-release affordance as enabled.
- **R38 additive-only** — no new permission key in this slice. The discovery read rides `document.read`.
- **Candidate pool = role membership**, not overrides — the inbox/approve gate keys off task visibility; the
  override-only asymmetry is intended.
- **Two state vocabularies / free-form `current_state`** — render as open strings; no enum validation.
- **The DOCUMENT path is the welded single-stage S5 service** — do not route it through `engine.py`; the
  stepper renders the actual (single) stage.
- **`null` not `undefined`** from the discovery `queryFn` (React-Query v5 throws on `undefined`).
- **WCAG 2.2 AA is a ship gate** — budget the `jest-axe` assertions up front.
