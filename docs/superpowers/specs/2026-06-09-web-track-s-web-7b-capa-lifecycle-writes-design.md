# S-web-7b — CAPA lifecycle writes (design)

> **Status:** approved (owner, 2026-06-09 — "full honest layer" + "one cohesive slice"). **Track:** web-UI.
> **Parent epic:** `docs/superpowers/specs/2026-06-08-web-track-s-web-7-nc-capa-design.md` (PR 7b of 7a–7d).
> **Builds on:** S-web-7a (the CAPA read spine — board + read-only drawer + `_capa`/`_stage` enrichment, #101).
> **Closes:** the ACT-phase **write** loop (Clause 10.2) in the SPA — raise → containment → root-cause →
> action-plan[approved] → implement → verify[signed] → close, with the Verify→RootCause effectiveness loop
> and the M4 real-evidence close gate. Owner mockup: `mockup/easysynq-mockup.html#screen-capa`.

## 1. Why / what

7a surfaced the CAPA domain read-only (a kanban board + a detail drawer that *shows* the closed-loop thread
and an informational close-gate stepper). Every CAPA **write** already exists and is contracted on the
backend (the S-capa-1..3 family, R39). 7b makes the drawer **drive** the lifecycle: each stage form, in the
drawer, permission-gated; the action-plan approval (decided in the existing `/tasks` inbox); the light
"link an existing record as evidence" affordance; and the M4 close.

**Not pure front-end.** The epic labeled 7b "front-end only", but investigation shows the action-plan
**approval loop is broken/blind** without a thin backend read-enrichment — the same shape as 7a's one
serializer change. Concretely:

- The `/tasks` per-task page (`ReviewApprovePage`, S-web-5) **assumes the task subject is a document**: it
  reads `instance.subject_id`, calls `GET /documents/{capa_id}` (404), and renders a blank, context-blind
  approval page (`ReviewApprovePage.tsx:17-18`). A CAPA approver clicking their task today lands on a broken
  page.
- Worse, a **Critical** CAPA's second-tier approver (the seeded **Top Management** role) holds **only
  `capa.read`**, *not* `document.read` (`migrations/versions/0038…:118-137`). The instance-read it would need
  (`GET /workflow-instances/{id}`) is **`document.read`-gated** (`api/workflow.py:254-266`) → **403**. So that
  approver literally cannot approve via the UI.
- The proposed action plan being approved lives in `workflow_instance.context.action_plan` and is **exposed by
  no read** today (the `_instance` serializer omits `context`). So even when the page loads, the approver can't
  see *what* they're signing.

So 7b adds a **thin backend read-enrichment layer** (§2.2) — three additive reads, all reusing `capa.read`
or self-scoped, **no migration, no new permission key** — exactly the 7a pattern.

## 2. Verified backend surface

All citations `apps/api/src/easysynq_api/...`. Every MSW fixture in 7b is pinned to **these** shapes
(verified against the serializer/route/service code, never the mockup — the #1 lesson; it bit 7a twice in
Codex review).

### 2.1 The writes (already built + contracted — `api/capa.py`, `packages/contracts/openapi.yaml`)

| Action | Endpoint | Body | Gate (PROCESS-scoped) | Notes |
|--------|----------|------|------|-------|
| Raise | `POST /capas` (201) | `CapaRaise {title*, severity*, source?, process_id?, problem?}` | `capa.create` | board-level; `source=review_output` is rejected (422, reserved) |
| Containment | `POST /capas/{id}/containment` | `ContainmentCreate {content_block*}` | `capa.update` | Raised→Containment |
| Root cause | `POST /capas/{id}/root-cause` | `StageBlockCreate {content_block*}` | `capa.record_rca` | Containment→RootCause; **unsigned** |
| Action plan | `POST /capas/{id}/action-plan` | `StageBlockCreate {content_block*}` | `capa.plan_action` | returns `Capa + approval_instance {id, current_state, definition_version}`; **`close_state` stays RootCause** until the approving `/tasks` decision |
| Implement | `POST /capas/{id}/implement` | `StageBlockCreate {content_block*}` | `capa.capture_effectiveness` | ActionPlan→Implement; **unsigned** |
| Verify | `POST /capas/{id}/verify` | `CapaVerifyCreate {decision* (effective\|not_effective), content_block*}` | `capa.verify` | Implement→Verify; **SIGNED** (`signature_event meaning=verify`); **SoD-4** enforced server-side → `409 sod_self_verify` |
| Close | `POST /capas/{id}/close` (no body) | — | `capa.close` | M4 gate: `effective`+root_cause+impl-with-evidence+effectiveness-evidence → `Closed`; `not_effective` → loop to RootCause (cycle++); `effective` missing evidence → `409 capa_close_incomplete`; no recorded verify for the cycle → `409 capa_not_verified` |
| Link evidence | `POST /records/{rid}/evidence-links` (201) | `EvidenceLinkCreate {target_type:"capa_stage", target_id*, link_reason?}` | `record.create` (per-record scope) | the M4 gate needs ≥1 link on the current-cycle Implement **and** Verify stages |

Approval **decision** (existing): `POST /tasks/{taskId}/decision` `Decision {outcome, comment?}` +
`Idempotency-Key`. The endpoint **dispatches on subject type** (`api/workflow.py:185-200`): a CAPA instance
routes to `decide_capa_action_plan`, which owns its own authorization (the role-resolved candidate pool **is**
the authority — **no `document.*` key gates it**), signs the ActionPlan stage, and flips
`close_state` RootCause→ActionPlan on completion. Outcome vocabulary (`engine.py:60-67`): `approve` (positive)
/ `reject` / `changes_requested` (both negative). **For a CAPA, `changes_requested ≡ reject`** — both leave
the CAPA at RootCause, re-proposable (the propose guard treats a REJECTED/NEEDS_ATTENTION instance as terminal).

### 2.2 The thin backend read-enrichment (the 7b backend change — mirrors 7a)

Three additive reads. **No migration. No new permission key.** Gated `capa.read` (which **both** QMS-Owner
**and** Top-Management hold) or self-scoped — so the whole CAPA approval path never depends on `document.read`.

1. **`_task` detail carries the subject discriminator.** Enrich the **single-task** serializer
   (`api/workflow.py:_task`, used by `GET /tasks/{id}`) with `subject_type` + `subject_id`, loaded from the
   task's instance. The list serializer stays as-is (the inbox table doesn't need it). This lets
   `ReviewApprovePage` route a CAPA task to the CAPA approval view **without** the `document.read`-gated
   instance fetch. Exposing a caller's own task's subject is not a leak (the caller already owns the task).
   _Add `subject_type`/`subject_id` (nullable) to the `Task` schema in `openapi.yaml`._

2. **`GET /capas/{id}/approval`** (new; gated `capa.read` — the direct mirror of S-web-5's
   `GET /documents/{id}/approval`). Returns the **latest** CAPA `workflow_instance` for the CAPA + its tasks +
   the **proposed action plan** from `instance.context.action_plan`, or **`null`** when no approval cycle has
   ever opened (calm; React-Query needs non-`undefined`). Shape:

   ```jsonc
   // null, OR:
   {
     "instance": { "id", "current_state", "definition_version", "subject_type", "subject_id",
                   "tasks": [ { "id", "stage_key", "type", "state", "assignee_user_id",
                               "candidate_pool", "action_expected", "due_at" } ] },
     "proposed_action_plan": { /* the free-form content_block proposed at POST /action-plan */ } | null
   }
   ```
   Powers **both** the drawer's "awaiting approval" panel **and** the approver's `/tasks` decision page (so a
   Top-Management approver, who holds only `capa.read`, can see what they're signing). _Add the path +
   response schema to `openapi.yaml`._

3. **`_stage` carries its evidence links.** Enrich the CAPA detail's stage serializer
   (`api/capa.py:_stage`) with `evidence_links: [{ id, record_id, record_identifier, link_reason, created_at }]`
   (one query over `evidence_for_link` joined to `documented_information` for the linking record's identifier,
   filtered `target_type='capa_stage' AND target_id = ANY(stage_ids)`). Lets the drawer **show** what's linked
   per stage **and** makes the close-gate stepper *honest* (real evidence presence, not 7a's stage-presence
   guess). List rows are unchanged (no stages). _Add `evidence_links` to the `CapaStage` schema in
   `openapi.yaml`._

> The 7a `_capa` fields (`title`/`created_at`/`raised_by`) already ship; 7b adds nothing to `_capa`. Write
> responses keep returning the CAPA via `_capa_full` — the UI **invalidates + refetches** after a write
> (`["capa", id]`, `["capas"]`, and `["capa-approval", id]` for the action-plan), never reshapes optimistically.

### 2.3 The FSM, gating keys, and who holds them

FSM (`domain/capa/fsm.py`): `Raised→{Containment,Rejected}` · `Containment→{RootCause,Rejected}` ·
`RootCause→{ActionPlan,Rejected}` · `ActionPlan→{Implement,Rejected}` · `Implement→{Verify,Rejected}` ·
`Verify→{Closed,RootCause}` (the not_effective loop, `cycle_marker++`) · `Closed`/`Rejected` terminal. The loop
**bumps `cycle_marker` without a new RootCause stage** (there is no RootCause→RootCause edge) — post-loop, the
next act at `close_state==RootCause` is to **re-propose** a revised action plan.

Keys (all PROCESS-scoped except the sig-hook `capa.verify`/`capa.close`; resolved at the CAPA's `process_id`,
SYSTEM fallback). Holders (`0004`/`0038`): write keys → **Process-Owner** (`capa.update`/`record_rca`/
`plan_action`/`capture_effectiveness`); `capa.verify`/`capa.close` → **QMS-Owner**; the **`demo` System-Admin
holds NONE** (the S-web-6 calm-403 case). A full **Critical/Major** close needs **≥2 distinct users** (SoD-4
verifier ≠ implementer, non-overridable). The action-plan approver pool: **QMS-Owner** (Major/Minor), and
**QMS-Owner → Top-Management** sequential for **Critical**.

## 3. Front-end design

New feature work all under `apps/web/src/`, mirroring the 7a feature (`features/capa/`), the S-web-3 authoring
form patterns, `usePermissions`, `useUserDirectory`, the shell `DetailDrawer`, and the MSW + vitest + jest-axe
rig.

### 3.1 The drawer write model — a contextual "Advance" panel

Per the mockup's single "Move to ▾" action: below the close-gate stepper, the drawer renders **one** contextual
**Advance** panel showing the *single legal next-stage form* for the CAPA's `close_state`, **permission-gated**
at the CAPA's PROCESS scope (`usePermissions({ level: "PROCESS", id: process_id })`, SYSTEM fallback when
`process_id` is null). **Never render a form the caller can't exercise** (don't show a button that 403s — the
recurring Codex catch). Mapping (panel keys on the current `close_state` = the source of the next transition):

| `close_state` | Advance form/action | Endpoint | Gate |
|---------------|---------------------|----------|------|
| Raised | **Containment** — "Correction taken" + "Evidence note" | `/containment` | `capa.update` |
| Containment | **Root cause** — "Root cause" + method select (5-whys / fishbone / other) | `/root-cause` | `capa.record_rca` |
| RootCause | **Action plan** — repeatable action-items [description / owner / due] → opens approval | `/action-plan` | `capa.plan_action` |
| ActionPlan | **Implement** — "Actions completed" + the evidence linker (§3.4) | `/implement` | `capa.capture_effectiveness` |
| Implement | **Verify** — decision radio (effective / not_effective) + narrative + evidence linker, **signed** | `/verify` | `capa.verify` |
| Verify | **Close** — the M4 finalize action (§3.4) | `/close` | `capa.close` |
| Closed / Rejected | — (terminal; no panel) | — | — |

Structured per-stage forms (not a generic key/value editor) — their field **names produce the same
`content_block` keys** the 7a `ContentBlock` read-renderer already humanizes (`correction`, `root_cause`,
`method`, `action_items`, `actions_done`, the verify narrative), so writes and reads round-trip cleanly. The
verify "signed" UX reuses the S-web-5 pattern (a v1 single-factor logged confirmation checkbox; the
`signature_event` is written server-side — no step-up UI). On success → **invalidate + refetch** the drawer +
board (no optimistic reshape); the panel re-derives from the refreshed `close_state`.

If the caller **holds no write key** for the current stage, the panel shows a calm "You don't hold the
permission to advance this CAPA" line (read-only), not a hidden/blank area.

### 3.2 Raise CAPA (board-level)

A **"＋ Raise CAPA"** primary button in the board header (per the mockup), gated `usePermissions().can("capa.create")`
(SYSTEM scope; the create scope resolves from the body's `process_id`). Opens a modal:
`title*` · `severity*` (Critical/Major/Minor) · `source` (audit/process/complaint — **review_output omitted**,
it 422s) · optional `process_id` (a process picker; SYSTEM/none allowed) · optional `problem`. On 201 →
invalidate `["capas"]`, close, and open the new CAPA's drawer.

### 3.3 The action-plan approval integration

**Proposing (drawer).** At `close_state==RootCause` the Advance panel is the action-plan form. On submit it
opens the approval; the response carries `approval_instance`. The drawer then shows an **"Awaiting approval"**
panel (from `GET /capas/{id}/approval`) instead of the propose form:
- non-terminal instance → "Action plan awaiting approval" + the proposed plan + the pending stage/role;
- `current_state==NEEDS_ATTENTION` (no approver assigned) → "No approver assigned — assign a QMS Owner / Top
  Management, then re-propose" (calm; the propose form returns);
- a `REJECTED` instance (or none) → the propose form is shown (re-propose is allowed).
The drawer **never** decides the approval — it surfaces state and routes the approver to `/tasks`.

**Deciding (`/tasks`).** `ReviewApprovePage` branches on `task.subject_type` (now on the task detail):
- `DOCUMENT` → the existing flow, byte-identical (instance → document → redline → `DecisionCard`).
- `CAPA` → fetch `GET /capas/{id}` (capa.read) + `GET /capas/{id}/approval` (capa.read) and render a new
  **`CapaApprovalContext`** panel (identifier · title · severity · the **proposed action plan**, rendered with
  the 7a `ContentBlock`) + a **generalized `DecisionCard`**. Neither read touches `document.read`, so the
  Top-Management approver works.

`DecisionCard` is made **subject-agnostic**: its `documentId` (used only as a cache-invalidation key) becomes a
generic `{ subjectType, subjectId, onDecided }` — for a document it invalidates the document queries (unchanged
behaviour); for a CAPA it invalidates `["capa", id]` + `["capas"]` + `["capa-approval", id]`. The radio
(approve / request-changes / reject) and the `409 already-decided` / `403 sod_violation` calm handling stay.
`useTask`/`useDecideTask` in `features/review/hooks.ts` are updated to thread the subject (and the `Task` type
gains `subject_type`/`subject_id`).

### 3.4 Evidence linking + the honest close gate

**Evidence linker** (used inside the Implement and Verify forms, and shown read-only on those stages in the
timeline). A searchable record picker from `GET /records?limit=100` (filter-not-403 → returns what the caller
may `record.read`; show `identifier — title`), an optional `link_reason`, and a "Link" button →
`POST /records/{rid}/evidence-links { target_type:"capa_stage", target_id:<stage.id>, link_reason }`. On 201 →
invalidate `["capa", id]` so the stage's `evidence_links` (from §2.2.3) refresh and render as a "Linked
records" list on the stage. Net-new evidence **capture** (upload) stays out of scope (epic §7) — 7b links
*existing* records only.

> The linked stage's id is the **current-cycle** Implement / Verify stage id (read from the refreshed
> `capa.stages`). Evidence linked to a Verify stage is frozen server-side (unlink-blocked) once signed.

**Honest close-gate stepper.** `CloseGateStepper.deriveGate` (from 7a) is made **evidence-aware**, matching
`services/capa/service.py:close_capa` **exactly** (the close-gate semantics Codex hammered in 7a):
- **Root cause documented** = any `RootCause` stage exists (**cycle-agnostic** — the established RCA carries
  across loop iterations).
- **Corrective action with evidence** = a **current-cycle** (`cycle_marker == capa.cycle_marker`) `Implement`
  stage that has **≥1 `evidence_links`**.
- **Effectiveness evidence** = a **current-cycle** `Verify` stage with `content_block.decision === "effective"`
  **and ≥1 `evidence_links`**.

**Close action** (the Advance panel at `close_state==Verify`):
- current-cycle verify decision `effective` → a **"Close CAPA"** button, *enabled only when all three gate
  steps are met* (else disabled with the missing requirement spelled out); a `409 capa_close_incomplete` /
  `capa_not_verified` is still surfaced calmly as the server's authoritative word;
- current-cycle verify decision `not_effective` → a **"Return to root cause"** button (same `POST /close`,
  which loops the CAPA to RootCause, `cycle++`).

### 3.5 Gating + calm errors (cross-cutting)

- **Gate on `usePermissions` at PROCESS scope**, SYSTEM fallback (§3.1). The CAPA detail has **no
  `capabilities` block** (unlike documents) — gate on `/me/permissions`, never render a 403-ing button.
- **Server-only truths, surfaced calmly** (inline, never a crash): `409 sod_self_verify` (SoD-4),
  `409 capa_close_incomplete` / `capa_not_verified` (M4), `409 invalid_capa_transition`,
  `409 capa_approval_in_progress`, `403 sod_violation` on the approval decision.
- **Free-form `content_block` rendered generically** on read (7a `ContentBlock`, XSS-safe, no
  `dangerouslySetInnerHTML`); written via structured forms that emit the same keys.

## 4. Components / files

**Backend (one commit; gated locally by `/check-api` static + `/check-contracts`; the api **test** suites are
Linux-CI-only on this box):**
- `api/workflow.py` — `_task` gains optional `subject_type`/`subject_id`; `get_task_endpoint` loads the
  instance and passes them.
- `api/capa.py` — new `GET /capas/{id}/approval` (gated `_capa_read`); `_stage` gains `evidence_links`;
  `get_capa_endpoint` loads per-stage evidence.
- `services/capa/repository.py` — `list_stage_evidence(session, stage_ids)` (evidence_for_link ⨝
  documented_information).
- `packages/contracts/openapi.yaml` — `Task.subject_type`/`subject_id`; `CapaStage.evidence_links`;
  `GET /capas/{id}/approval` path + `CapaApproval` schema.
- `apps/api/tests/integration/test_capa.py` + `test_workflow.py` — the new fields/read (runs in CI).

**Front-end (new, `features/capa/`):** `mutations.ts` (the 8 write hooks) · `useCapaApproval` (in `hooks.ts`) ·
`StageForms.tsx` (the per-stage structured forms) · `AdvancePanel.tsx` (the contextual next-step panel +
awaiting-approval) · `RaiseCapaModal.tsx` · `EvidenceLinker.tsx` · (+ `.test.tsx` each).
**Front-end (modified):** `CapaDrawer.tsx` (mount AdvancePanel + per-stage evidence + awaiting-approval) ·
`CapaBoardPage.tsx` (Raise button + modal) · `CloseGateStepper.tsx` (evidence-aware `deriveGate`) ·
`lib/types.ts` (request bodies; `CapaApproval`; `CapaStage.evidence_links`; `Task.subject_type`/`subject_id`) ·
`features/review/{ReviewApprovePage,DecisionCard,hooks}.tsx` (subject-aware) · `features/review/`
**new** `CapaApprovalContext.tsx` · `test/msw/handlers.ts` (write handlers + approval read + evidence +
task subject_type).

## 5. Tests (vitest + MSW + jest-axe; fixtures pinned to §2)

- **AdvancePanel**: the correct form per `close_state`; permission-gated (no button when the key is absent);
  read-only line for a no-write caller; terminal states render no panel.
- **Each stage form**: submits the right body; invalidates + refetches; calm-409 (`invalid_capa_transition`,
  `sod_self_verify`).
- **Action-plan + approval**: propose → drawer shows "awaiting approval" (+ NEEDS_ATTENTION variant);
  `ReviewApprovePage` CAPA branch renders the proposed plan + a working `DecisionCard`; the DOCUMENT branch is
  unchanged (regression).
- **Evidence linker**: links a record → stage shows it; the close-gate stepper flips to met only with a
  current-cycle Implement/Verify **with evidence**; a `cycle_marker>0` fixture proves current-cycle scoping.
- **Close**: enabled only when the gate is met; `not_effective` → "Return to root cause"; calm
  `capa_close_incomplete`.
- **Raise modal**: creates → opens the new drawer; `review_output` is not offered.
- **a11y**: no axe violations on the drawer (with the panel) and the modal.

Run the full **`/check-web`** (eslint + strict `tsc` + build + the whole vitest suite) before the PR — the full
run catches the `noUncheckedIndexedAccess` / cross-file drift the per-file runs miss. Then the **`diff-critic`**
agent on the branch diff.

## 6. Out of scope (7b)
- Net-new evidence **capture/upload** in the drawer (link existing records only — epic §7).
- A dedicated CAPA-approval **route** (we branch the existing `/tasks` page in place).
- Drawer-native approval **decision** (the approver decides in `/tasks`).
- The board's unbacked tiles/filters (overdue / avg-cycle-time / Owner / Age — dropped in 7a, stay dropped).
- 7c (complaints/NCR intake) and 7d (audits/findings) — later PRs.

## 7. Risks / watch-items
- **Fixture drift** (the recurring false-PASS): pin every MSW fixture to §2 serializers (`_capa`/`_stage` +
  `evidence_links`, the `CapaApproval` shape, `Task.subject_type`), verified vs `apps/api` — never the mockup.
- **Close-gate semantics** must match `close_capa` byte-for-byte: root_cause cycle-agnostic; impl-action +
  effectiveness **current-cycle + with evidence**. (Codex's repeat catch.)
- **PROCESS-scoped gating**: query `usePermissions` at the CAPA's `process_id`, not SYSTEM, or a genuinely
  process-scoped grant mis-gates (the S-pack-1 R28 lesson, SPA side).
- **The DOCUMENT approval path must stay byte-identical** — the `DecisionCard`/`ReviewApprovePage`
  generalization is additive; keep the S-web-5 `TasksInbox`/`ReviewApprovePage` document tests green as the
  regression backstop.
- **`subject_type` on `_task` detail only** — the list serializer is untouched; don't accidentally make the
  inbox list query N instances.

## 8. Smoke (live stack)
`demo` (System Administrator) holds **no `capa.*`** → grant **SYSTEM overrides** of the capa keys (org
short_code **AHT**) for a board+drawer smoke. A full **Critical/Major** create→…→close needs **≥2 distinct
users** (SoD-4) — use `priya`/`ken`/`mara` (`just seed-personas`); **or** a **Minor** CAPA with
`allow_capa_self_verify` for a 1-user smoke. The Critical two-tier approval needs a Top-Management user in
addition to a QMS-Owner. (Smoke is verification, not a build blocker — the MSW tests simulate every response.)
