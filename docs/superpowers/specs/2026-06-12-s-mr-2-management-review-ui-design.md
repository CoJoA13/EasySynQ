# S-mr-2 — Management Review UI (ISO 9001 clause 9.3) — design

- **Date:** 2026-06-12
- **Slice:** `S-mr-2` — the trailing web UI that **CLOSES the Management Review family** (`S-mr-1` shipped the backend, #120, squash `7e0bc97`, migration head `0050`).
- **Status:** owner-approved design (brainstorm 2026-06-12, after two source-verification sweeps — every claim below is checked against code, not slice-history narrative).
- **Significance:** makes the last-built ★ family **usable in the SPA**. With S-mr-1 the ISO 9001:2015 ★ spine went feature-complete; S-mr-2 surfaces it: the `/management-reviews` register + detail/lifecycle cockpit, the `/tasks` MR_INPUT/MR_ACTION legs, and the Home "next review in N days" widget (the named S-home-1 / doc-13 CHECK deferral).
- **Scope decision — NOT pure-front-end.** The owner chose the maximal path on every fork: **full UI in one slice** (F1) · **include the Home widget + its one thin backend read** (F2) · **auto-resolve MR_INPUT in the backend** (F5) · **fold the `compute_scorecard` extraction in** (carry). So S-mr-2 = a new web module + **three migration-free backend touches**. Gate: `/check-web` + `/check-api` + `/check-contracts`. **No migration** (head stays `0050`), **no new permission key** (catalog stays 100), **no new enum**.
- **Doc grounding:** R45 (the MR family register entry — the DOCUMENT-head recipe, the deliberate doc-14 §9 RECORD deviation, the cadence sweep) · R43 (attestation-shaped acts are never a `signature_event` — the MR_ACTION complete writes no signature, the MR_INPUT resolve writes no signature) · R2 (signature-meaning enum closed) · R8 (dates display org-tz, UTC authoritative) · N6 (no SPC/charts — doc-13 §5.2's gauge/donut vocabulary is **restated to calm tables/RAG**) · N9 (status against a rule, never an auto-compliance verdict — the server RAG read verbatim) · doc-11 (calm, progressively-disclosed UI; the "nav of labeled regions" / header-band vocabulary) · doc-13 §5.2 (the MR dashboard — chart vocabulary stale, restated). Web SPA testing rules: `.claude/rules/engineering-patterns.md` "Web SPA testing".
- **As-built anchors (verified this session):**
  - **API serializers (pin MSW here, NOT the mockup):** `api/mgmt_review.py:106-177` — `_mgmt_review` / `_review_input` / `_review_output` / `_approval_instance` / `_approval_task`. Contract: `packages/contracts/openapi.yaml:7882-8027` (zero drift confirmed).
  - **Cadence (the next-due read):** `services/mgmt_review/cadence.py:63-71` (`next_mr_due`), `:84-113` (`_last_released_effective_from` anchor), `:135-182` (the sweep), `db/models/system_config.py:104-118` (`mgmt_review_cadence_months` server_default 12 + nullable `mgmt_review_owner_user_id`). The documents precedent: `services/vault/review.py:50-85` (`today_org` / `review_state` / `REVIEW_LEAD_DAYS=30`), `api/documents.py:185-189` (the derive-at-read shape).
  - **MR_INPUT auto-resolve:** `services/mgmt_review/service.py:167-221` (`submit_review_for_review`, commits internally, the seam is before `session.commit` at `:219`), `services/mgmt_review/cadence.py:216-244` (MR_INPUT minted on a `MGMT_REVIEW` instance whose `subject_id == review_id`, `action_expected='prepare'`, `stage_key='prepare'`, PENDING), `services/mgmt_review/repository.py:101-124` (`find_nonterminal_instance`), `services/workflow/engine.py:460` (Task state is a plain mutation — not WORM, `0008` has no REVOKE).
  - **compute_scorecard extraction:** `api/objectives.py:452-474` (`scorecard_endpoint`, caller-gated) vs `services/mgmt_review/compile.py:110-136` (`_objectives_scorecard`, owner-gated) — behaviourally identical, authz-scope is the only difference and lives *outside* both loops; `services/objectives/queries.py:22-53` (`list_objectives` 5-tuples); `domain/objectives/rules.py:29-45` (`rag_status`); `domain/mgmt_review/inputs.py:24-38` (`summarize_scorecard`). Behaviour-preservation tests: `tests/integration/test_quality_objectives.py:295-299`, `tests/integration/test_mgmt_review.py:267-270`.
  - **/tasks legs:** `api/workflow.py:167` (LIST `_task` — **omits** subject_type/subject_id), `:178-182` (detail INCLUDES them), `:243-251` (the `MGMT_REVIEW` dispatch leg → `decide_mr_task`, before DOC_ACK + the DOCUMENT fallthrough), `services/mgmt_review/decide.py:32,48-73` (outcome=`complete` only, 404-collapse non-membership, **no signature**, does **not** gate on `task.type`), `services/mgmt_review/spawn.py:90-111` (per-action `stage_key='action:<output_id>'`).
  - **FE precedents:** `apps/web/src/features/objectives/` (register/detail/Lifecycle-card/create-modal — the closest copy) · `features/document/ApprovalStepper.tsx` (lifts directly — MR approval is `subject_type:DOCUMENT`) · `features/review/ReviewApprovePage.tsx`,`DecisionCard.tsx`,`hooks.ts`,`TasksInbox.tsx` (the /tasks decide page) · `features/home/CheckCard.tsx`,`MyTasksRail.tsx`,`QuadrantCard.tsx` (the Home wheel; the rail already labels MR_INPUT/MR_ACTION) · `app/shell/LeftRail.tsx:58-77` (the gated-NavLink pattern) · `app/shell/usePermissions.ts` · `App.tsx:142-143` (flat routes).

> **The thesis.** S-mr-1 made one released MR flip the 9.3 ★ with zero checklist code; S-mr-2 puts a human in front of that machinery. The MR is a `kind=DOCUMENT` subtype, so its **register + detail + lifecycle are the Objectives module re-skinned** and the shared `ApprovalStepper` lifts verbatim. The genuinely MR-specific surface is small: the **9.3.2 inputs as calm RAG tables** (doc-13's charts restated to tables — N6/N9), the **9.3.3 outputs** grouped DECISION/ACTION/IMPROVEMENT, the **close gate** surfaced calmly, the **two /tasks legs** (MR_INPUT a nav chip, MR_ACTION a complete-only no-signature card), and the **Home next-review widget**. The three backend touches are the minimum the UI needs that S-mr-1 left unexposed: a cadence read, an honest MR_INPUT lifecycle, and a de-duplicated scorecard.

---

## s0 · Owner decisions (this session, 2026-06-12)

The F-numbers are the brainstorm's decision frame; each was preceded by a source sweep.

1. **F2 — include the Home widget + its one thin backend read.** *Verified DEFINITIVE:* the widget is **not** pure-front-end-possible. The cadence (`mgmt_review_cadence_months`) and owner (`mgmt_review_owner_user_id`) live on `system_config` and are returned by **no** GET — not even `/admin/config` (it's `config.update`-gated *and* `_config_view` omits both fields, `api/config.py:35,60-66`). `next_mr_due` is computed only inside the daily Beat sweep. The list rows carry no `effective_from`, so the client can't reconstruct the cadence anchor either. **Client-compute is impossible.** → add exactly **one** thin read, `GET /management-reviews/next-due`, mirroring the per-document `next_review_due`+`review_state` precedent (s2).
2. **F1 — full UI, one slice** (the S-obj-2 family-closing precedent): register + detail (inputs tables + outputs) + the Compile/outputs/Submit/Release/Close lifecycle + the /tasks legs + the Home widget. Every piece has a copy-ready precedent; rejected the split (two review cycles for a largely precedent-driven surface).
3. **F5 — auto-resolve MR_INPUT in the backend** (s3): MR_INPUT has no decide leg and stays PENDING forever, so it would linger in `MyTasksRail`. Rather than a client-side filter, transition the task to DONE in the backend when its review is submitted. Rejected the FE-filter-only and the named-deferral options.
4. **Carry — fold the `compute_scorecard` extraction in** (s4): the MR compiler reproduces `api/objectives.py`'s scorecard inline; extract a shared service fn so the two can't desync. (Behaviourally identical today, so this is pure dedup, not a latent-bug fix.)
5. **F3 — detail UX:** a commitment-hero-style header + the 9.3.2 inputs as calm RAG tables + the 9.3.3 outputs grouped by type + a reused Lifecycle card (`ApprovalStepper` + the objectives lifecycle shape). The input tables use a **generic `source_ref` renderer with per-type labels** (RAG where the server provides it; gap rows as "not available — <reason>"); the plan pins each type's exact summary columns by reading `compile.py`'s per-type builders.
6. **F4 — create flow:** a small modal (title + period_label) → the Draft detail with an inline re-runnable "Compile inputs" + a Draft-only outputs editor.
7. **F6 — gating/smoke:** per-key calm-403; the demo System Administrator holds none of the `mgmtReview.*`/`document.release` content keys → SYSTEM overrides on the **live** demo `app_user` row (org AHT) for the smoke.

**Accepted reconciliations (in-PR doc fixes, code is authoritative):** build against `/management-reviews` (the S-mr-1 spec §s7 says the stale `/mgmt-reviews`); the FE surfaces the as-built close-gate codes `review_close_blocked` / `review_not_open_to_close` (R45/spec say `mgmt_review_close_blocked`).

---

## s1 · What the code already pins (settled — restated, not re-decided)

- **Serializers (pin MSW with `satisfies <Type>` to these — NO decimals anywhere in this API):**
  - `_mgmt_review` (`api/mgmt_review.py:106-122`): `{id, identifier, title, current_state` (7-state doc enum `.value`: `Draft|InReview|Approved|Effective|UnderRevision|Superseded|Obsolete`), `period_label?, review_date?` (YYYY-MM-DD), `attendees?` (JSONB `[{name, role?, user_id?}]`), `close_state?` (`ActionsTracked|Closed` — **PascalCase**, null pre-release), `closed_at?` (ISO datetime), `created_at}`. Used bare for list rows, create, patch, close, submit, release; **detail + compile-inputs mutate this same dict in place** to add `inputs[]` + `outputs[]`.
  - `_review_input` (`:125-133`): `{id, management_review_id, input_type` (12-enum), `available` (bool — `false`=gap row), `source_ref` (JSONB object, NOT-NULL: `{available, generated_at, summary?|reason?}`), `position` (int)}.
  - `_review_output` (`:136-145`): `{id, management_review_id, output_type` (`DECISION|ACTION|IMPROVEMENT`), `description`, `owner_user_id?`, `due_date?` (YYYY-MM-DD), `spawned_task_id?}` (set at release, ACTION outputs only; `spawned_capa_id`/`spawned_initiative_id` reserved cols **not serialized**).
  - `_approval_instance` (`:164-177`): `{id, definition_id, definition_version` (int), `subject_type` (**always** `'DOCUMENT'`), `subject_id` (==review_id), `current_state` (raw workflow string, **not** an enum `.value`), `started_at?, revision` (int), `tasks: [_approval_task]}`. `GET …/approval` returns top-level **`null`** pre-submit.
  - `_approval_task` (`:148-161`): `{id, instance_id, stage_key, type, state, assignee_user_id?, candidate_pool` (raw UUID list), `action_expected, due_at?}` — deliberately **omits** subject_type/subject_id.
- **Endpoints (registration order — literal-before-`{review_id}`):** `POST` create (`mgmtReview.create`) · `GET` list `{data:[…]}` (`mgmtReview.read`) · `GET /{id}` detail · `POST /{id}/compile-inputs` (`mgmtReview.record_outputs`, Draft-only) · `POST /{id}/close` (409 `review_close_blocked`/`review_not_open_to_close`) · `POST /{id}/outputs` · `PATCH /{id}/outputs/{oid}` · `DELETE /{id}/outputs/{oid}` (204) · `PATCH /{id}` (meta, Draft-only) · `GET /{id}/approval` (null pre-submit) · `POST /{id}/submit-review` (`mgmtReview.record_outputs`) · `POST /{id}/release` (`document.release` + SoD-2). List wrapper is `{data:[…]}`; **every other array is bare** (`inputs[]`, `outputs[]`, `tasks[]`). All `mgmtReview.*` keys are SYSTEM finest-scope; bound to no role in v1 (the family rides SYSTEM overrides).
- **The MR approval instance is `subject_type:DOCUMENT`** — so `features/document/ApprovalStepper` (pure `buildApprovalNodes`) and the objectives Lifecycle card lift directly.
- **MyTasksRail already labels** MR_INPUT ("Management-review input") and MR_ACTION ("Management-review action") via an exhaustive `TaskType` record (`features/home/MyTasksRail.tsx:8-13`) — zero change.
- **`decide_mr_task` does not gate on `task.type`** (`decide.py:48-61`) — it dispatches purely on `subject_type==MGMT_REVIEW`, so it would accept an MR_INPUT with `outcome:complete`. **MR_INPUT non-decidability is a FE contract:** the FE must branch on `task.type` and render no decision affordance for MR_INPUT.

---

## s2 · Backend touch 1 — `GET /management-reviews/next-due`

**Goal:** one thin read so the Home widget (and any later consumer) can show "next review in N days" without re-deriving the cadence rule.

**Shared cadence read (so the widget and the Beat sweep can't desync).** Refactor `services/mgmt_review/cadence.py` to expose a public `read_cadence(session, org_id) -> dict` that the existing sweep and the new endpoint both call:
```
read_cadence(session, org_id) -> {
  cadence_months: int,                       # SystemConfig.mgmt_review_cadence_months (server_default 12)
  last_review_effective_from: date | None,   # _last_released_effective_from(session, org_id) — newest Effective MR's version effective_from, org-tz; None if none released
  next_review_due: date | None,              # next_mr_due(anchor, cadence_months) — None when anchor is None
  owner_configured: bool,                    # SystemConfig.mgmt_review_owner_user_id is not None
}
```
Reuse `_last_released_effective_from` (`:84-113`) and `next_mr_due` (`:63-71`) **verbatim** — do not re-implement the month-add or the anchor query. `_last_released_effective_from` is private; **promote it** (drop the underscore) or expose `read_cadence` as the public seam the API imports. Refactor the sweep's inline anchor+due derivation (`:170-176`) to call `read_cadence` so there is one cadence rule.

**The currency projection.** Add an MR-specific `mr_review_state` beside `next_mr_due` in `cadence.py`, mirroring the documents `review_state` three-bucket shape (`services/vault/review.py:77-85`) but with a **separate constant** so it never silently tracks a document's lead:
```
MR_REVIEW_LEAD_DAYS = 30   # NOT review.REVIEW_LEAD_DAYS — annual cadence, independently tunable; org-config is a v1.x deferral
def mr_review_state(next_due: date | None, today: date) -> str | None:
    if next_due is None:        return None         # never-released MR — matches review_state(None,…)=None
    if today >= next_due:       return "overdue"
    if today >= next_due - timedelta(days=MR_REVIEW_LEAD_DAYS): return "due_soon"
    return "current"
```
`today` is org-local (`today_org()`, already imported into cadence.py) — **never** `datetime.now().date()` (UTC), or the widget and the sweep's `due_at` disagree by the offset around the boundary day (R8).

**The endpoint** (`api/mgmt_review.py`): `GET /management-reviews/next-due`, gated `Depends(_mr_read)` (`mgmtReview.read`). **Declared BEFORE `GET /management-reviews/{review_id}`** (`:262`) or FastAPI's str-convertor resolves "next-due" into the `{review_id}` route → a 422 (S-pack-2 lesson; "next-due" never parses as a UUID so the reverse order is safe). Add a **route-resolution unit test** (`app.router.routes` + `route.matches`).
```
cad = read_cadence(session, caller.org_id)
return {
  "cadence_months": cad["cadence_months"],
  "last_review_effective_from": iso_or_none(cad["last_review_effective_from"]),
  "next_review_due": iso_or_none(cad["next_review_due"]),
  "review_state": mr_review_state(cad["next_review_due"], today_org()),   # current|due_soon|overdue|null
  "owner_configured": cad["owner_configured"],
}
```
**Never 500** (it backs a Home dashboard tile): if `SystemConfig` is somehow absent, degrade to a neutral payload (`owner_configured:false`, coded-default cadence, `review_state:null`) — the sweep's logged-no-op posture (`cadence.py:138-140`).

**Contract:** document the endpoint + a `ManagementReviewNextDue` response schema in `packages/contracts/openapi.yaml` in-PR (additive read; `/check-contracts`). No migration, no new key.

---

## s3 · Backend touch 2 — MR_INPUT auto-resolve at submit

**Goal (F5):** MR_INPUT is a non-decidable "prepare this review" to-do minted by the cadence sweep; with no decide leg it stays PENDING and would linger in `MyTasksRail` forever. Resolve it honestly when the review is submitted (minutes frozen = prep done).

**Mechanics** (`services/mgmt_review/service.py::submit_review_for_review`): at the seam **before `session.commit`** (`:219`), within the same submit txn (which already holds the doc row FOR UPDATE + populate_existing):
1. `inst = await find_nonterminal_instance(session, WorkflowSubjectType.MGMT_REVIEW, review_id)` (`repository.py:101-124`) — the **MGMT_REVIEW container** instance whose `subject_id == review_id`, **not** the DOCUMENT approval instance the `/approval` endpoint queries. `None` (a manually-created MR has no container until release) → no-op.
2. For each `Task` on that instance with `type == MR_INPUT and state == PENDING`: set `task.state = TaskState.DONE` **directly** (a plain mutation — task state is not WORM, `engine.py:460`). **No `TaskOutcome`, no signature** (R43); do **not** route through `decide_mr_task` (that's the MR_ACTION decide path and would demand membership/outcome).
3. Audit: **migration-free — no new enum.** The submit already emits its own audit event (the trail); the plan settles whether a dedicated row is worth reusing an existing `MGMT_REVIEW_*` event type. Default: no separate row (the task-state flip is a mechanical consequence of submit, like the freeze).

**Idempotent & safe:** the transition only touches PENDING tasks, so a re-submit (or a manual MR with an empty PENDING set) no-ops. Atomic with the freeze (same txn) — no partial state. No FE change required for this beyond the rail no longer showing the stranded row; the FE still renders MR_INPUT as a nav chip while the review is in prep (s6).

---

## s4 · Backend touch 3 — shared `compute_scorecard`

**Goal (carry):** `api/objectives.py::scorecard_endpoint` (`:452-474`, caller-gated) and `services/mgmt_review/compile.py::_objectives_scorecard` (`:110-136`, owner-gated) grade objectives **identically** today (same `list_objectives` 5-tuples → `resolve_commitment` → `rag_status` → tally; `sum(by_rag.values()) == len(rows)` because `rag_status` always returns one of `{green,amber,red,unmeasured}`). Extract a shared fn so they can't drift.

**The shared fn** — new `services/objectives/scorecard.py`, **authz-AGNOSTIC** (grading only; the caller owns the gate):
```
async def compute_scorecard(session, org_id, *, process_id=None) -> dict:
    rows = await list_objectives(session, org_id, process_id=process_id)   # 5-tuples
    by_rag = {"green":0,"amber":0,"red":0,"unmeasured":0}
    graded = []
    for qo, ident, title, state, governing in rows:
        c   = resolve_commitment(governing, target_value=qo.target_value, unit=qo.unit, direction=qo.direction,
                                 due_date=qo.due_date, at_risk_threshold=qo.at_risk_threshold,
                                 baseline_value=qo.baseline_value, policy_id=qo.policy_id)
        rag = rag_status(current=qo.current_value, target=c.target_value, direction=c.direction,
                         at_risk_threshold=c.at_risk_threshold)
        by_rag[rag] += 1                              # KeyError loudly if a new rag escapes the 4-key set (don't swallow)
        graded.append(((qo, ident, title, state, governing), rag))
    return {"total": len(rows), "on_target": by_rag["green"], "by_rag": by_rag, "rows": graded}
```
Register it in `services/objectives/__init__.py` `__all__` (the `list_objectives` import-from-package-root convention).

**#1 RISK — keep authz OUT of the shared fn.** The objectives endpoint gates via the PEP (`require('objective.read')` → an authz audit row + 403-on-deny); the MR compiler gates via the **owner's** grants through the **non-auditing DIRECT PDP path** (`gather_grants`+`authorize` at `ResourceContext.system()`, `compile.py:83-94`) and **fail-closes to a gap row, never a 403**. If `compute_scorecard` called `require`/`enforce` internally it would (a) emit a spurious authz audit row inside the MR compile, (b) raise 403 instead of producing the F3 gap row, (c) gate on the wrong principal. Both gates stay exactly where they are at the call sites; the shared fn only reads + grades.

**Rewire two call sites (byte-preserve both responses):**
- `scorecard_endpoint` — keep `Depends(_objective_read)`; `sc = await compute_scorecard(session, caller.org_id, process_id=process_id)`; serialize each `sc["rows"]` row via the existing `_objective(...)` (so the `objectives` list — incl. pct/attainment — stays identical; `test_quality_objectives.py:295-299` asserts rows with `id`+`rag`); return `{total: sc["total"], on_target: sc["on_target"], by_rag: sc["by_rag"], objectives:[…]}`. Trust `sc["by_rag"]` (same values; removes the second loop). Returning the graded `rows` avoids a second `list_objectives` query.
- `_objectives_scorecard` — replace the body with `sc = await compute_scorecard(session, org_id)`; return `{total, on_target, by_rag}` (drop `rows` — `summarize_scorecard` ignores it). The owner-grant gate (`_owner_holds`, `compile.py:166-167`) stays **before** the call.

**Behaviour-preservation backstops (must stay green):** `test_quality_objectives.py::test_scorecard_rollup_counts_by_rag` (endpoint shape) **and** `test_mgmt_review.py:267-270` (OBJECTIVES_STATUS summary: `set(by_rag)=={green,amber,red,unmeasured}`, `on_target==by_rag["green"]`). No migration, no contract change, no new key.

---

## s5 · FE — the `features/management-review/` module

Greenfield (no `mgmtReview` token on web). Flat folder mirroring `features/objectives/`: `hooks.ts` (reads), `mutations.ts` (writes), `labels.ts` (pure helpers), one page/component each with a colocated `*.test.tsx`.

**Data layer.** `hooks.ts` — every read is `useQuery({queryKey, queryFn: () => api.get<T>(url), enabled: id!==null for :id, retry:false})` then `return {...query, forbidden: forbiddenOf(query.error)}` (`forbiddenOf = e instanceof ApiError && e.status===403`):
- `useMgmtReviews()` → `GET /management-reviews` (`{data:[…]}`)
- `useMgmtReview(id)` → `GET /management-reviews/{id}` (detail = bare + `inputs[]` + `outputs[]`)
- `useMgmtReviewApproval(id)` → `GET /management-reviews/{id}/approval` (null pre-submit)
- `useMgmtReviewNextDue()` → `GET /management-reviews/next-due` (s7)

`mutations.ts` — `useMutation({mutationFn: api.send(METHOD,url,body), onSuccess: invalidate})` for: create, compileInputs, addOutput, patchOutput, deleteOutput, patchMeta, submit, release, close. A shared `useInvalidateMgmtReview()` invalidates `['management-review',id]`, `['management-review-approval',id]`, `['management-reviews']`, and `['my-tasks']` (so the Home rail refreshes after a lifecycle write).

**Register** (`ManagementReviewsRegisterPage.tsx`, route `/management-reviews`, `mgmtReview.read`-gated): `forbidden`→calm gray "No access" Alert; `isError`→red; `isLoading`→Loader. A Mantine `Table` of reviews — `identifier` (Anchor→detail) · `title` · a `StateBadge` for `current_state` · `period_label` · `review_date` · a `close_state` chip. An empty-state Alert branches on `can('mgmtReview.create')` (offer the create modal). **No scorecard band** (the MR list has no aggregate — the scorecard lives inside a review's compiled inputs).

**Detail** (`ManagementReviewDetailPage.tsx`, `/management-reviews/:id`):
- **Header** (commitment-hero style): `title`/`identifier`, `period_label`, `review_date`, the `attendees` roster (names + optional role), the `current_state` + `close_state` badges.
- **9.3.2 inputs — calm RAG tables** (N6/N9; doc-13 §5.2 charts restated): one calm card per `review_input` ordered by `position`. A **generic `source_ref` renderer with per-type labels**: for a live row (`available:true`) render its `summary` payload as a definition-list/table, surfacing server-provided RAG verbatim as Mantine Badge chips (e.g. OBJECTIVES_STATUS → a `{green,amber,red,unmeasured}` band + `on_target`); for a gap row (`available:false`) render a calm "not available — `{reason}`" line. **No charts.** Render `source_ref` values as React text nodes (never `dangerouslySetInnerHTML`). The plan pins each input_type's exact summary columns by reading `compile.py`'s per-type builders; the renderer degrades gracefully for shapes it doesn't have a bespoke label for.
- **9.3.3 outputs** grouped DECISION / ACTION / IMPROVEMENT: each shows `description`; an ACTION additionally shows `owner_user_id` (→ display name via `useUserDirectory`), `due_date`, and the **spawned MR_ACTION task state** (resolved from the approval/instance read or a best-effort task read — the plan settles the cheapest source).
- **Lifecycle card** (reuse the objectives shape + the shared `ApprovalStepper`): rendered when any affordance or an approval instance exists. Composes `<ApprovalStepper instance docState={current_state} effectiveFrom nameOf/>` + an `actionError` Alert + the contextual actions:
  - **Compile inputs** (`mgmtReview.record_outputs`, Draft-only, re-runnable) — refreshes the detail.
  - **Outputs editor** (Draft-only): add (`POST /outputs`), edit (`PATCH`), remove (`DELETE`). An ACTION requires `owner_user_id` (owner picker via `useUserDirectory`) — enforce client-side (the backend also requires it).
  - **Submit** (`mgmtReview.record_outputs`) → the stepper advances; **Release** (`document.release` + SoD-2) → the 9.3 ★ flips, MR_ACTION tasks spawn, `close_state→ActionsTracked`; **Close** (`mgmtReview.record_outputs`).
- **Close gate, surfaced calmly:** map the as-built 409 codes `review_close_blocked` (an ACTION output's MR_ACTION task not yet DONE) and `review_not_open_to_close` (not yet `ActionsTracked`) to calm copy; render a **client close-readiness note** mirroring the audit `finding_blocks_close` pattern (count of ACTION outputs whose spawned task isn't DONE) so the operator sees *why* before clicking.

**Create** (`NewManagementReviewModal.tsx`): `{open && <Modal opened …/>}` (conditional-render so close unmounts/resets — the S-web-7d trap). Fields: `title` (1–300) + `period_label`. `mutateAsync` in try/catch (`ApiError.message`→red Alert) → navigate to the new Draft detail.

**Nav + routes:** `LeftRail.tsx` — `{can('mgmtReview.read') && <NavLink to='/management-reviews' label='Management reviews' active={pathname.startsWith('/management-reviews')}/>}` in the **CHECK** cluster (clause 9 — near audits/compliance, **not** under PLAN where Objectives sits). Routes flat in `App.tsx`: `management-reviews`, `management-reviews/:id`.

**Test/fixture conventions:** MSW fixtures pinned to the s1 serializers via `satisfies <Type>` (never the mockup); the literal `next-due` handler registered before `:id`; every component test `import { expect, it } from "vitest"` (the jest-dom×tsc trap); the global jsdom `scrollIntoView` stub for any Mantine Combobox/Select; distinct `aria-label`s.

---

## s6 · FE — the `/tasks` legs

`ReviewApprovePage.tsx` gains a **`MGMT_REVIEW` arm** (today an MR task wrongly falls through to the DOCUMENT branch → the wrong `document.read` gate + a meaningless redline over the JSON minutes):
1. **Detection + disable the doc branch:** `isMr = task.subject_type === 'MGMT_REVIEW'`; add `!isMr` to the document-branch disablers (mirror `:29-31` as done for isCapa/isPeriodic/isDocAck) so `useWorkflowInstance`/`useDocument`/`useDocumentVersions` do **not** fire (the subject_id is a review id).
2. **Branch on `task.type` inside the arm** (server dispatch is subject-type-only, so the FE enforces non-decidability):
   - **MR_INPUT** → nav-only: an `MgmtReviewContext` panel (via `useMgmtReview(task.subject_id)`) + a `Link` to the review's prep page; **no decision card** (else the owner could `complete` the prepare task and strand the review).
   - **MR_ACTION** → `MgmtReviewContext` + a dedicated **`MrActionCard`** when `task.state==='PENDING'` (else the existing decided alert). The card is the AttestationCard precedent: one-click `outcome:'complete'`, a per-mount `Idempotency-Key`, **no sign UI** — keeps DOCUMENT/CAPA/PERIODIC byte-identical. (Rejected widening `DecisionCard`: it would need a `MGMT_REVIEW` `SIGN_OUTCOME` deliberately ≠ `complete` to suppress the checkbox — a footgun the dedicated card avoids.)
3. **`useMgmtReview(id)`** is best-effort context: `forbidden` (403 + retry:false) → a calm panel; the MR_ACTION `complete` is self-scoped and works even when context is forbidden (the DocAckContext precedent).
4. **`useDecideTask` onSuccess** (`review/hooks.ts:57-73`): add a `MGMT_REVIEW` arm invalidating `['management-review',subjectId]`, `['management-reviews']`, `['management-review-approval',subjectId]`; **and add `['my-tasks']` to the shared invalidations** (a pre-existing gap MR_ACTION surfaces — the Home rail keys on `my-tasks`, not `tasks`).
5. `TasksInbox.tsx` already routes every pending row to `/tasks/:id`, so an MR_INPUT row already lands on the nav-only view. Types: `Task.subject_type` is already optional; `TaskType` already has MR_INPUT/MR_ACTION; the dedicated card means no `DecisionSubjectType` change.

*Backend defense-in-depth (named, out of FE scope):* `decide_mr_task` could 422 an MR_INPUT — a reasonable follow-up, not this slice.

---

## s7 · FE — the Home next-review widget

In `features/home/CheckCard.tsx` (clause 9 houses 9.3) as a calm StatLine off `useMgmtReviewNextDue()`:
- `forbidden`/`isError`/`isLoading` → the existing per-signal degrade (never drags the tile red — informational).
- `owner_configured:false` → "Review cadence not configured" (a setup prompt, **not** a misleading countdown).
- else → "Next review due `{next_review_due}`" with a RAG-ish treatment from `review_state` (`overdue`→red, `due_soon`→amber, `current`→green). N9 status-against-a-rule (the server's `review_state` verbatim) — no auto-verdict.
- `review_state:null` (never-released MR) → a neutral "no review released yet" line.

The widget contributes RAG to the CHECK tile only when it carries a RAG-bearing `review_state` (the S-home-1 `worstRag` rule — a forbidden/errored/unarmed read contributes none).

---

## s8 · Gating · smoke · nav

- Per-key calm-403 via `usePermissions().can(...)` (SYSTEM scope, v1). Write affordances gate on `mgmtReview.create` / `mgmtReview.record_outputs` / `document.release`; reads degrade via the `forbidden` flag, never crash.
- **Demo smoke:** the System Administrator (`demo`) holds **none** of the `mgmtReview.*`/`document.release` content keys → grant SYSTEM overrides for `mgmtReview.read`, `mgmtReview.create`, `mgmtReview.record_outputs`, `document.release` on the **live** demo `app_user` row (org **AHT**) — the override must land on the row matching the LIVE Keycloak login's subject (re-created Keycloak users mint new JIT rows). The `MgmtReviewContext` read 403s without `mgmtReview.read` but degrades calmly; an MR_ACTION `complete` (self-scoped) still works.
- **Rebuild the web image before the smoke** (`docker compose … up -d --build web`) + hard-refresh/Incognito — `vite preview` serves a baked build.

---

## s9 · Reconciliations (in-PR)

- **Route name:** S-mr-2 uses `/management-reviews` everywhere; fix the stale `/mgmt-reviews` reference in the S-mr-1 spec §s7 note.
- **Close-gate codes:** the FE surfaces `review_close_blocked` / `review_not_open_to_close`; correct R45 + the S-mr-1 spec text (which say `mgmt_review_close_blocked`) — the code is authoritative.

---

## s10 · Testing & gates

- **`/check-web`** (eslint + strict `tsc --noEmit` + build + the full vitest suite — `noUncheckedIndexedAccess` + the jest-dom×tsc trap only surface in the full run): the register/detail/lifecycle/create components, the outputs editor, the close-readiness note, the `/tasks` MR_INPUT nav + MR_ACTION complete legs, the Home widget, the hooks (forbidden degrade, invalidation), and a route-test that `/management-reviews/:id` resolves. Pin every fixture to the s1 serializers.
- **`/check-api`** (ruff + mypy-strict + pytest unit; `-m integration` needs Docker): the `next-due` endpoint (incl. the route-ordering unit test + the never-500 degrade), `read_cadence`/`mr_review_state` (incl. null-history + the org-tz boundary), the MR_INPUT auto-resolve (resolves a PENDING MR_INPUT at submit; no-op on a manual MR / re-submit; writes no signature), and `compute_scorecard` (the two behaviour-preservation backstops + a unit test that it performs **no** authz).
- **`/check-contracts`** (redocly): the new `GET /management-reviews/next-due` + `ManagementReviewNextDue` schema.
- **diff-critic** on the branch diff pre-PR (the false-PASS hunt; pre-loaded with the WORM/authz invariants).
- **Codex triage** post-PR: disregard D1-moot multi-tenant framing; verify each claim against code before fixing (Codex caught a real byte-path-guard gap on S-mr-1 the diff-critic missed).
- **Live smoke via Chrome MCP** pre-merge (owner does the Keycloak login): create → compile-inputs → author outputs → submit (MR_INPUT leaves the rail) → release (★ flips, MR_ACTION spawns) → complete the MR_ACTION → close; plus the Home next-review widget and the inherited authed-200 leg of `next-due`.

---

## s11 · Named deferrals (not faked)

- **MR commitment *revision*** (first-release-only today; the S-obj-3 posture) → a possible **S-mr-3** with the CAPA `review_output` un-reserve + the DCR `mgmt_review` link (the `spawned_capa_id`/`spawned_initiative_id` columns ship reserved-null).
- **The four sourceless 9.3.2 inputs** (context, customer-satisfaction, supplier-performance, resource-adequacy) + **risk/opportunity** + **improvement_initiative** stay honest gap rows (each is a real backend family).
- **The rendered Management-Review-Pack PDF** → v1.1.
- **Dedicated Top-Management approval routing** (sign-off rides standard `document.approve`/`document.release` today).
- **Org-tunable cadence + `MR_REVIEW_LEAD_DAYS`** (coded defaults ship; org-config is v1.x).
- **`decide_mr_task` type-gate** (422 an MR_INPUT) — backend defense-in-depth, the FE contract suffices for this slice.

---

## s12 · Build sequence (subagent-driven TDD)

The plan (writing-plans next) decomposes into per-task TDD dispatches with per-task spec+quality review. Indicative order — backend first (the FE pins fixtures to it), each phase its own red→green→review:

1. **`compute_scorecard` extraction** (s4) — lowest-risk, behaviour-preserving; unblocks nothing but de-risks the refactor early. Backstops: the two integration tests stay green.
2. **`GET /management-reviews/next-due`** (s2) — `read_cadence` refactor + `mr_review_state` + the endpoint + the route-ordering test + the contract addition.
3. **MR_INPUT auto-resolve** (s3) — the submit-txn seam + the find-and-flip + idempotency tests.
4. **FE module foundation** — `hooks.ts`/`mutations.ts`/`labels.ts`/types + MSW fixtures pinned to s1; the register page + nav entry + routes.
5. **FE detail + lifecycle** — header + the 9.3.2 input tables + the 9.3.3 outputs + the Lifecycle card (ApprovalStepper) + the outputs editor + the close-readiness note + the create modal.
6. **FE /tasks legs** (s6) — the MGMT_REVIEW arm (MR_INPUT nav, MR_ACTION `MrActionCard`) + the `useDecideTask` invalidation arm + `my-tasks`.
7. **FE Home widget** (s7) — the CheckCard next-review StatLine.
8. **Reconciliations + slice-history + CLAUDE.md learnings** (s9) + the full-gate + diff-critic pass.
