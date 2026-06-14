# S-dcr-ui-2b — DCR lifecycle cockpit + the `/tasks` DCR-approval leg + the capabilities enrichment

> **Status:** owner-approved design (F1–F6 settled 2026-06-13). Spec → plan → subagent-driven TDD.
> **Track:** DCR-UI (clause §10 → §7.5 change control). Follows S-dcr-ui-1 (read spine, #123) +
> S-dcr-ui-2a (intake & early writes, #124). **Closes the operable DCR cockpit.**
> **Migration head stays `0051`.** This slice adds **one backend touch** (a detail-only `capabilities`
> block on `GET /dcrs/{id}`) + contract/doc fixes; everything else is front-end.

## 1. Goal

Make a routed DCR drivable end-to-end **in the SPA**: a DCR can be **assessed → routed → approved
(in `/tasks`) → implemented → closed**, with every write affordance gated on a server-computed,
PROCESS-scoped capability so nothing show-then-403s. This is the lifecycle counterpart to ui-2a's intake
writes — it grows `DcrAdvancePanel` from `{Edit, Cancel}` into the full lifecycle panel and adds the
fourth subject leg to the `/tasks` decision surface.

## 2. Decisions baked in (F1–F6)

- **F1 — one slice.** Cockpit + the `/tasks` DCR-approval leg + the capabilities enrichment + the
  contract/doc-prose fixes ship together (the cockpit and the approval leg are FSM-coupled — a routed
  DCR sits in `InApproval` with no SPA way to advance until both land).
- **F2 — honest `implement` capability.** The `implement` flag ANDs `changeRequest.implement` with the
  underlying `document.release` (REVISE) / `document.obsolete` (RETIRE) SoD-2 authz answer, so a
  version-author who can advance the FSM but cannot self-release never sees a 403-bound button. The
  capability block is **PROCESS-scoped** (server resolves scope from the target doc) and **detail-only**.
- **F3 — implement form.** REVISE = a confirm (no extra input; backend resolves the latest-Approved
  version). RETIRE = a force-retire escalation surfaced on a `409 obsoletion_blocked`. **CREATE-implement
  is deferred** (no client `version_id → document_id` resolution; `resulting_version_id` is required).
  Close = submit-and-show the `409 dcr_effectivity_pending`.
- **F4 — `/tasks` DCR-approval leg = a SIGNING `DecisionCard`.** Outcomes `approve`/`reject`/
  `changes_requested`; server-derived `meaning=approval`; candidate-pool membership (NOT a
  `changeRequest.approve` `can()` check).
- **F5 — fold the prose drift.** Fix the one stale `openapi.yaml` `DcrSourceLinkType` description + the
  three stale `docs/15 §8.7` lines in this slice (the capabilities enrichment already pulls
  `/check-contracts`).
- **F6 — live smoke = MINOR + MAJOR two-approver** (see §9).

## 3. Verified as-built (corrections to the narrative — these shape the design)

Read against code 2026-06-13. CLAUDE.md / the memory note are narrative; these are pinned.

1. **Capabilities CAN be PROCESS-scoped server-side.** [`_dcr_doc_scope`](../../apps/api/src/easysynq_api/api/dcr.py)
   (`api/dcr.py:166`) resolves the full `ResourceContext` (incl. `process_ids` from the target doc's
   process-links) — every write dependency (`_dcr_assess`/`_dcr_route`/`_dcr_implement`/`_dcr_close`)
   already authorizes against it. The "SYSTEM-scoped" claim in ui-2a learning #7 is a *client-side*
   limitation; the server answer is PROCESS-correct. The enrichment is the proper fix.

2. **`Routed` is never an observable resting state.** `route_dcr` (`services/dcr/service.py:434`) hops
   `Assessed → Routed → InApproval` in one call. The FE never sees `Routed`; the cockpit shows a single
   **Route** action from `Assessed` that lands in `InApproval`. The FSM `Routed → Cancelled` edge is
   unreachable via the UI.

3. **No `GET /dcrs/{id}/approval` endpoint** (CAPA has one; DCR does not). The cockpit does **not** need
   it — `InApproval` is a static "decided in My Tasks" banner; the `/tasks` leg reads context via
   `useDcr(subject_id)`. **The capabilities block is the only backend touch.**

4. **`implement` is a true double-gate.** `_enforce_underlying_document_control` (`api/dcr.py:218`) runs
   an in-handler `document.release`/`document.obsolete` enforce *after* the `changeRequest.implement`
   dependency. A single-probe capability would be dishonest (F2).

5. **FSM (9 states), edge map** (`domain/dcr/fsm.py:32`): `Open→{Assessed,Cancelled}`;
   `Assessed→{Routed,Cancelled}`; `Routed→{InApproval,Cancelled}`; `InApproval→{Approved,Rejected,Open}`;
   `Approved→{Implemented}`; `Implemented→{Closed}`; `Closed`/`Cancelled`/`Rejected` terminal.

6. **Signature minting** (`services/dcr/service.py`): ONLY `decide_dcr_approval(outcome=approve)` mints
   `signature_event(meaning='approval', signed_object_type='dcr')` — one per stage approver (MAJOR =
   two). `reject`/`changes_requested` mint nothing; `changes_requested → Open` (R40 loop). The
   REVISE/CREATE release signature is minted later by the `release_due` Beat sweep (system-attributed);
   the RETIRE obsolete signature is minted inline by `lifecycle.obsolete` (actor-attributed). **The
   `/dcrs/{id}/implement` endpoint does not sign inline.** The only signing FE surface is the `/tasks` leg.

7. **`/tasks` plumbing.** DCR-approval tasks are `type=APPROVE`, `action_expected='approve_dcr'`;
   `GET /tasks` (LIST) omits `subject_type`/`subject_id` (detail-only). So the **inbox list needs no
   change** — the subject branch is in `ReviewApprovePage` (detail). Two booby-traps:
   - `ReviewApprovePage`'s `isDocumentSubject` is a *negation fallthrough* (`ReviewApprovePage.tsx:34`)
     — a DCR task falls into the DOCUMENT branch unless `&& !isDcr` is added.
   - `useDecideTask` `onSuccess` has a CAPA *catch-all `else`* (`features/review/hooks.ts:53`) — a DCR
     decision would invalidate capa caches and leave a stale DCR drawer. Needs an explicit DCR leg.

8. **Contract drift is narrow.** `openapi.yaml:6777` `DcrSourceLinkType` description still calls
   `mgmt_review` "reserved" (live since S-mr-3) — the only stale item *in the contract*. The
   `reason_for_change` / nested `source_link:{type,id}` drift is **only** in `docs/15:406,407,416`.

9. **`api.send` method union is `POST|PATCH|DELETE`** — no `PUT`. Impact-dimension annotation
   (`PUT /dcrs/{id}/impact`) is therefore **out of scope** (would need widening the union;
   `DcrImpactTable` stays read-only).

## 4. Backend touch — `_dcr_capabilities` (detail-only, PROCESS-scoped, honest `implement`)

### 4.1 The block

Mirror `_mr_capabilities` (`api/mgmt_review.py:229`) and `_objective_capabilities` (`api/objectives.py:338`):
a per-key PDP probe folded into **`GET /dcrs/{id}` only**. The serializer `_dcr(...)` is unchanged for
list/create/raise; `get_dcr_endpoint` sets `out["capabilities"] = caps` after the existing
`out["stage_events"] = [...]` mutation (the same shape used for `stage_events`).

```python
async def _dcr_capabilities(session, caller: AppUser, dcr: Dcr) -> dict[str, bool]:
    now = datetime.now(UTC)
    scope = await _dcr_doc_scope(session, dcr.target_document_id)   # PROCESS scope from target; SYSTEM if CREATE/None
    ctx = RequestContext(now=now, actor_user_id=str(caller.id))     # non-SoD keys
    async def probe(key: str) -> bool:
        grants = await gather_grants(session, caller.id, caller.org_id, key)
        return authorize(grants, key, scope, ctx).allow
    assess = await probe("changeRequest.assess")
    route = await probe("changeRequest.route")
    close = await probe("changeRequest.close")
    implement_cr = await probe("changeRequest.implement")
    implement = implement_cr and await _underlying_control_allowed(session, caller, dcr, now)
    return {"assess": assess, "route": route, "implement": implement, "close": close}
```

- **Keys emitted: `{assess, route, implement, close}`.** The FE derives `edit ← assess` and
  `cancel ← close` (the same server answer at the write endpoints; the `_objective` precedent emits
  distinct keys but DCR `edit`/`cancel` ARE `changeRequest.assess`/`.close`, so aliasing on the FE is
  honest and keeps the block minimal). **One scope, four `changeRequest.*` probes + one underlying-control
  probe for `implement`** (5 `authorize()` calls total) — cheaper than MR's per-key scope.

### 4.2 Honest `implement` — `_underlying_control_allowed`

The `implement` flag must reflect the in-handler second gate. Extract the **key + scope derivation**
shared with `_enforce_underlying_document_control` so the capability and the enforcement cannot drift:

- **RETIRE** → `authorize(grants("document.obsolete"), "document.obsolete", scope, ctx_sig, sig_hook=True)`
  over `scope = _dcr_doc_scope(target)`.
- **REVISE** → `authorize(grants("document.release"), "document.release", enrich_release_sod_scope(scope),
  rel_ctx, sig_hook=True, sod=gather_sod_constraints(...))` — the SoD-2 overlay
  (`rel_ctx` carries `allow_approver_release=get_allow_approver_release(...)`), exactly the
  `_mr_capabilities` release branch.
- **CREATE** → the release scope depends on the not-yet-existing `resulting_version_id`; **CREATE-implement
  is deferred (no button)** so the value is moot — report `implement_cr` (the `changeRequest.implement`
  answer) without the underlying AND. (The FE won't render an Implement button for CREATE.)

The probe reads `authorize(...).allow` (PDP, no audit/raise) — distinct from `enforce(...)` (PEP) used by
the handler. To prevent drift, factor `_underlying_control_target(dcr) -> (key, scope)` and call it from
both `_underlying_control_allowed` (capability) and `_enforce_underlying_document_control` (enforcement).
Validation errors (CREATE-needs-version 422, version-not-found 404) are NOT authz and stay submit-and-show.

### 4.3 Contract + prose

- `openapi.yaml`: add an optional `capabilities` object to the `Dcr` schema
  (`{assess: boolean, route: boolean, implement: boolean, close: boolean}`, `additionalProperties:false`,
  not in `required` — detail-only), mirroring the `MgmtReviewDetail`/objective precedent. Document it as a
  detail-only field on `GET /dcrs/{id}`.
- `openapi.yaml:6777`: fix the `DcrSourceLinkType` description — drop the `mgmt_review`-is-reserved half
  (`risk` stays the only reserved seam).
- `docs/15:406` `reason_for_change` → `reason_class` (the GET filter list);
  `docs/15:407` body → flat `reason_class` + `reason_text`, `source_link:{type,id}` → flat
  `source_link_type` / `source_link_id`; `docs/15:416` raise-dcr note → flat `source_link_type=capa`.

### 4.4 Backend tests (CI-run; locally ruff+mypy+targeted unit only — Windows integration is CI-only)

- Unit: `_dcr_capabilities` returns all-false for a no-grant caller; true per-key for a holder; `implement`
  is false when `changeRequest.implement` holds but `document.release` is denied (the honesty case) and
  for the version-author SoD-2 case; PROCESS-scoped grant on the target's process reaches the keys; CREATE
  DCR (no target) falls back to SYSTEM scope.
- Integration: `GET /dcrs/{id}` carries `capabilities`; `GET /dcrs` (list) does NOT; create/raise do NOT.
- A drift-pin: assert `_underlying_control_allowed` and `_enforce_underlying_document_control` agree
  (same caller/dcr → flag true ⟺ enforce does not raise 403) for REVISE + RETIRE.

## 5. FE cockpit — `DcrAdvancePanel` grows into the lifecycle panel

Mirror `features/capa/AdvancePanel.tsx` (the switch-on-state + `gated()` helper) but simpler — no
propose-vs-awaiting on a shared state. The panel takes `{dcr: DcrDetail}` and switches on `dcr.state`,
gating each affordance on `dcr.capabilities?.<key> === true && <legal state>` (the
`ManagementReviewDetailPage` no-show-then-403 pattern). Mounts at `DcrDrawer.tsx:83` (unchanged).

| `dcr.state` | Affordance(s) | Gate |
|---|---|---|
| `Open` | **Assess** · **Edit** · **Cancel** | `capabilities.assess` / `.assess` (edit) / `.close` (cancel) |
| `Assessed` | **Route** · **Cancel** | `capabilities.route` / `.close` |
| `InApproval` | "Awaiting approval — decided in **My Tasks**" banner (blue `Alert`, no advance) | — |
| `Approved` | **Implement** | `capabilities.implement` (REVISE/RETIRE only; CREATE → a calm "implemented from the document workspace" note, no button) |
| `Implemented` | **Close** | `capabilities.close` (submit-and-show the 409) |
| `Rejected`/`Closed`/`Cancelled` | none (terminal) | — |

- **Capabilities gating replaces the ui-2a SYSTEM-scoped `can()`.** `EditDcrModal`/`CancelDcrModal`
  triggers move from `can('changeRequest.assess')`/`can('changeRequest.close')` to
  `dcr.capabilities?.assess`/`.close`. While `useDcr` is loading or the detail lacks a `capabilities`
  block (shouldn't happen on the detail read, but defensively), affordances stay hidden.
- **New mutations** in `features/dcr/mutations.ts` (mirror the ui-2a hooks; **never optimistic**; invalidate
  `['dcr',id]` + `['dcrs']`, and `['dcr-impact',id]` for assess since it populates impact rows):
  - `useAssessDcr(id)` → `POST /dcrs/{id}/assess` (no body) — `onSettled → invalidator(id)`
    (a concurrent advance can race → self-heal). Response carries `impact_assessment` inline.
  - `useRouteDcr(id)` → `POST /dcrs/{id}/route` (no body) — `onSettled → invalidator(id)`. The
    `409 dcr_no_approvers` / `dcr_approval_in_progress` / `dcr_not_routable` surface calmly.
  - `useImplementDcr(id)` → `POST /dcrs/{id}/implement` body `DcrImplement{resulting_version_id?,
    force_retire, override_justification?}` — `onSettled → invalidator(id)`.
  - `useCloseDcr(id)` → `POST /dcrs/{id}/close` (no body) — `onSettled → invalidator(id)`.
- **Forms** (each a `{open && <Modal/>}` conditional-mount in the panel so close unmounts+resets — the
  Mantine persistent-mount trap; submit labels distinct from the trigger to avoid the duplicate-accessible-
  name trap):
  - **Assess** = a direct panel button (low-stakes, reversible via Cancel; fires `useAssessDcr`, loading
    state + an error `Alert` on failure). **Route** = a confirm modal (`RouteDcrModal` — one sentence:
    "This spins up the approval workflow and notifies the assigned approver(s)"; Confirm submits no body),
    because routing is hard to undo (it instantiates the approval instance and lands `InApproval`).
    Both surface `ApiError.message` in an `Alert` on failure.
  - **Implement (`ImplementDcrModal`)** — branch on `dcr.change_type`:
    - REVISE → "Implement this revision. The approved version will be scheduled to take effect on
      `{proposed_effective_from or 'release'}`." Confirm; submit `{}`. Show-and-surface any 409
      (`no_approved_draft`, `version_already_linked`, `version_not_approved`, `dcr_not_implementable`)
      verbatim (CloseAction precedent).
    - RETIRE → Confirm; submit `{force_retire:false}`. On `409 obsoletion_blocked`, reveal the server's
      coverage-gap detail + a **force-retire** checkbox + a **required** `override_justification` textarea,
      then re-submit `{force_retire:true, override_justification}`. (`422` if the justification is empty —
      guarded client-side, surfaced if it slips.)
    - CREATE → **deferred**: no button (the panel shows the calm note instead).
  - **Close (`CloseDcrAction`)** — NO client effectivity gate (the FE can't compute it). Submit; on
    `409 dcr_effectivity_pending` surface `e.message` verbatim (one of the three server strings:
    "the retirement has not yet taken effect" / "no resulting version is linked yet" / "the resulting
    version is not yet Effective (the scheduled cutover is pending)"). The CloseAction
    (`features/capa/StageForms.tsx:270`) submit-and-show pattern, no `CloseGateStepper` needed.

## 6. FE — the `/tasks` DCR-approval leg (signing)

1. **`DecisionSubjectType += 'DCR'`** (`lib/types.ts:361`). This tsc-forces an entry in all three Records
   in `DecisionCard.tsx` (`OUTCOMES`, `SIGN_OUTCOME`, `SIGN_MEANING`) — a built-in completeness guard.
   - `OUTCOMES.DCR = [approve, changes_requested, reject]` (same shape as DOCUMENT/CAPA).
   - `SIGN_OUTCOME.DCR = 'approve'`; `SIGN_MEANING.DCR = 'approval'`.
   - `NEEDS_COMMENT` (`['changes_requested','reject']`) already covers DCR — comment required there.
2. **`ReviewApprovePage`** (`features/review/ReviewApprovePage.tsx`): add `const isDcr =
   task?.subject_type === 'DCR'`; **add `&& !isDcr` to the `isDocumentSubject` negation** (line 34) so a
   DCR task never resolves a phantom document/redline; add the DCR early-return block:
   - Left column: **`DcrApprovalContext`** — reads `useDcr(task.subject_id)` (calm-403 → bare
     "Change request {id}" header), renders the identifier, change type/significance, reason, target doc,
     and the impact table (read-only, reuse `DcrImpactTable` via `useDcrImpact`).
   - Right column: `decidable = task.state === 'PENDING'` → `<DecisionCard taskId subjectType='DCR'
     subjectId={task.subject_id}/>` else the existing `decidedAlert`.
3. **`useDecideTask`** (`features/review/hooks.ts`): insert an explicit `else if (subjectType === 'DCR')`
   **before** the CAPA catch-all `else`, invalidating `['dcr', subjectId]`, `['dcrs']`,
   `['dcr-impact', subjectId]`, and `['my-tasks']` (the Home rail; `useDecideMrTask` precedent) in
   addition to the always-invalidated `['task',taskId]` + `['tasks']`.
4. **No inbox-list change** — DCR tasks route via the generic `/tasks/{id}` link; `action_expected`
   already reads "approve_dcr". No backend approval-read needed (context = `useDcr`).
5. **Calm error handling:** a non-pool caller gets `404` from `_assert_dcr_approver` (collapse — "not
   yours"); `409 dcr_approver_conflict` / `dcr_not_in_approval` → surface + the list self-heals on
   invalidation. The existing `DecisionCard` catch already handles `403 sod_violation` / generic 409 /
   `step_up_required`.

## 7. Error / gating matrix (FE)

| Surface | Gate (show) | Failure handling |
|---|---|---|
| Assess / Route / Cancel / Edit | `capabilities.{assess,route,close}` + legal state | surface `ApiError.message`; `onSettled` self-heal on a 409 race |
| Implement (REVISE) | `capabilities.implement` + `Approved` + `change_type!=='CREATE'` | submit-and-show 409 verbatim |
| Implement (RETIRE) | as above | 409 `obsoletion_blocked` → reveal force-retire + justification, re-submit |
| Implement (CREATE) | **never** (deferred) | calm note, no button |
| Close | `capabilities.close` + `Implemented` | submit-and-show 409 `dcr_effectivity_pending` (3 strings) |
| `/tasks` DCR decide | `task.state==='PENDING'` (membership-gated server-side) | 404 collapse; 409/403 surface + self-heal |
| DcrApprovalContext / target doc | `useDcr`/`useDocument` calm-403 (`forbidden` + `retry:false`) | bare-id fallback, never a crash |

## 8. Testing strategy (web — `/check-web`)

Subagent-driven TDD, per-task spec + quality review. Pin every MSW fixture to the real serializer via
`satisfies <Type>` (incl. the new `capabilities` block once it lands in the type). `import { expect, it }
from "vitest"` in every component test (the jest-dom×tsc trap). Mantine v7 `required` labels → regex
`getByLabelText(/.../)`. Duplicate-aria-label Selects → `getAllByLabelText(...)[0]`. A jest-axe smoke per
new page/drawer state. Conditional-mount modals (`{open && <Modal/>}`) + a reopen-resets test.

Test targets (extend the existing `features/dcr/*.test.tsx` + `features/review/*`):
- `DcrAdvancePanel.test.tsx` — per-state affordance visibility gated on `capabilities.*` (mirror the
  existing `grant()` helper but over the `capabilities` block, not `/me/permissions`); terminal states
  show nothing; `InApproval` shows the banner not a button; CREATE-Approved shows the note not Implement.
- New `ImplementDcrModal.test.tsx` — REVISE confirm submits `{}`; RETIRE 409 `obsoletion_blocked` reveals
  the force-retire + justification then re-submits `{force_retire:true,...}`; empty-justification guard.
- New `CloseDcrAction.test.tsx` — submit-and-show the three `dcr_effectivity_pending` strings verbatim.
- New mutations tested via the modal/panel tests (invalidation keys asserted).
- `DecisionCard.test.tsx` — a DCR subject renders 3 outcomes + the signing checkbox on `approve`
  (`meaning: approval`), comment required on `changes_requested`/`reject`.
- `ReviewApprovePage.test.tsx` — a DCR task renders `DcrApprovalContext` + the DCR `DecisionCard` and does
  NOT resolve a document/redline (the `isDocumentSubject` negation guard); a decided DCR task shows the
  alert.
- `useDecideTask` invalidation — a DCR decision invalidates the dcr keys, not capa.

Full suite run via `--pool=forks --poolOptions.forks.singleFork=true` for a clean signal. Backend:
`/check-api` (ruff + mypy + targeted unit) + `/check-contracts` (redocly); integration is CI-only on this
Windows box.

## 9. Live-smoke plan (F6 — MINOR + MAJOR two-approver)

Rebuild `web` + `api`/`worker` (`docker compose … up -d --build web api worker beat`). The owner does the
Keycloak login(s). Mechanics from the ui-2a note: drawer/modal portals are screenshot/JS-read (invisible
to `find`/`get_page_text`); use client-side nav (a full `navigate` to a deep route reloads → SSO → Home).

**Principals** (the implement leg needs author ≠ releaser; the approval is candidate-pool — a SYSTEM
override does NOT join the pool, so real role-holders are required):
- A **target document** with an Effective version authored by principal **A** (so A is the version author).
- **B** — holds `changeRequest.assess`/`.route`/`.implement` + `document.release` (SoD-2: B ≠ A) +
  `changeRequest.read` (SYSTEM overrides + role as needed).
- A **QMS Owner** role-holder (the MINOR approver + the MAJOR stage-2 approver) and a **Process Owner**
  role-holder (the MAJOR stage-1 approver), two **distinct** users (the `dcr_approver_conflict` guard
  forbids one user clearing both MAJOR tiers). Grant the approvers `changeRequest.read` so
  `DcrApprovalContext` shows full context (else it calm-degrades to a bare id — also worth seeing once).

**MINOR REVISE (the full loop):** B raises a MINOR REVISE DCR against the target → **Assess** (impact
populates) → **Route** (lands `InApproval`, MINOR → QMS Owner stage) → the QMS Owner opens `/tasks`,
sees the DCR approval, signs **approve** → DCR `Approved` → B **Implements** (REVISE, no input) → trigger
`release_due` in the worker (the REVISE release signature is system-swept; until it runs, close 409s) →
the version goes Effective → B **Closes** (200). Also verify: closing *before* the sweep 409s
`dcr_effectivity_pending` (submit-and-show), and a `changes_requested` decision returns the DCR to `Open`.

**MAJOR REVISE (two-approver SoD):** raise a MAJOR REVISE DCR → Assess → Route (→ Process Owner stage 1,
then QMS Owner stage 2, SEQUENTIAL) → Process Owner approves (signature 1, stays `InApproval`) → QMS Owner
approves (signature 2, → `Approved`) → confirm a single user holding both roles is **blocked**
(`409 dcr_approver_conflict`) on the second stage → Implement → sweep → Close.

**RETIRE escalation (optional, if a coverage-gap doc is to hand):** Implement a RETIRE DCR whose target
is a sole-coverer → `409 obsoletion_blocked` reveals the gap + force-retire + justification → re-submit →
target Obsolete → Close.

## 10. Out of scope / deferred (named, not faked)

- **CREATE-implement** — no client `version_id → document_id` resolution; `resulting_version_id` is
  required. The panel shows a calm note for a CREATE-Approved DCR. (→ a later slice if a version-picker
  lands.)
- **Impact-dimension annotation** (`PUT /dcrs/{id}/impact`) — `api.send` has no `PUT`; `DcrImpactTable`
  stays read-only. (→ a focused follow-up.)
- **The page-image visual diff + redline** — **S-dcr-ui-3** (reuse `features/document/VisualDiffViewer`).
- **A `GET /dcrs/{id}/approval` read** — not needed (InApproval = banner; `/tasks` context = `useDcr`).
- **Target-picker server-side search** (the ui-2a Codex P2 first-page limit) — v1.x.

## 11. Risks / traps carried

- **Capability/enforcement drift on `implement`** — mitigated by the shared `_underlying_control_target`
  derivation + the drift-pin test (§4.4). The submit-and-show 403 is the defense-in-depth net regardless.
- **The `isDocumentSubject` negation + the `useDecideTask` CAPA catch-all** — the two `/tasks` booby-traps
  (§3.7); explicit DCR legs + a regression test each.
- **`Routed` non-observability** — the panel must NOT offer a "submit to approval" step or a
  cancel-from-Routed path (route() collapses to `InApproval`).
- **`release_due` timing in the live smoke** — close 409s until the sweep runs; trigger it manually.
- **MSW fixtures** — pin to the real `_dcr` + the new `capabilities` shape via `satisfies` (the #1
  web false-PASS).
