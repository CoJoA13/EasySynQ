# S-mr-3 — Management Review outputs → the action systems (clause 9.3)

> Spec. Status: **draft, awaiting owner review.** Date: 2026-06-13. Branch: `feat/s-mr-3-mr-outputs-to-action-systems`.
> Supersedes nothing; closes two of the R45 named deferrals (the CAPA `review_output` un-reserve +
> the DCR `mgmt_review` link) and the S-mr-2 Codex-#1 chip (`task_ae567104`).

## 1. Why this slice

The Management Review family (clause 9.3) is CLOSED and usable end-to-end (S-mr-1 #120 backend, S-mr-2
#121 UI, head `0050`). S-mr-1 shipped three **reserved-null seams** so that management decisions could
later drive real action systems without a re-architecture. S-mr-3 activates the two that have a real
target today, plus a low-risk read-model fix:

- **(a) CAPA un-reserve** — an MR **ACTION** output can spawn a CAPA, so a management decision drives
  formal corrective/preventive action. Closes the `review_output.spawned_capa_id` /
  `CapaSource.review_output` seam.
- **(b) DCR `mgmt_review` link** — an MR **ACTION** output can spawn a Document Change Request, so a
  management decision drives a controlled document change. Closes the `DcrSourceLinkType.mgmt_review`
  seam. **Backend-only** (see §3.3 — the DCR domain has no SPA surface).
- **(c) Codex #1** — a SoD-aware `capabilities.release` on the MR detail serializer, so the FE Release
  button stops being able to show-then-403 when the caller authored the frozen minutes.

**Deferred, named (not faked):**
- **MR minutes revision** (the S-obj-4 analog) — a 9.3.3 record is point-in-time; you convene a new
  review, not revise a filed one. Low value for the heavy S-obj-4 machinery. **Out of scope.**
- **`improvement_initiative`** — `review_output.spawned_initiative_id` points at a table that **does
  not exist anywhere in the codebase** (docs/14 §9: "deferred/unbuilt in v1"). It **stays reserved-null**
  — there is nothing to wire it to. The DCR link is a *separate* seam and does NOT use this column.
- **The MR→DCR front-end affordance** — the DCR domain has **zero SPA surface** (no `features/dcr/`, no
  DCR list/detail/raise UI; grep for `/dcrs`/`change_type`/`source_link` across `apps/web/src` is
  empty). Wiring a Raise-DCR button would spawn a DCR into a black hole. Deferred to a future DCR-UI
  track; S-mr-3 ships the DCR seam **backend-only** (a tested endpoint that the future UI will call).
- **Broader output-type spawning** — both CAPA and DCR spawn are restricted to **ACTION** outputs in
  v1 (they carry owner + due and are the tracked outputs). DECISION/IMPROVEMENT outputs stay recorded
  text. Expandable later.

### Decisions settled with the owner (brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| F1 | Slice scope | CAPA + DCR + Codex #1 (improvement_initiative + minutes-revision deferred) |
| F2 | CAPA spawn trigger | **On-demand** per ACTION output (forced: a CAPA requires a severity the output doesn't carry) |
| F3 | CAPA → close gate | **Decoupled** — a spawned CAPA does NOT block MR close (the MR_ACTION task DONE stays the sole signal) |
| F4 | `spawned_capa_id` shape | **Add FK → `capa.id`** (RESTRICT, migration `0051`) |
| F5 | Codex #1 | **In scope** (rides this slice) |
| F6 | MR minutes revision | **Defer** |
| DCR sub-scope | (re-decided after finding no DCR FE) | **Backend-only** — wire + test the seam, no FE affordance |

## 2. Ground truth (verified against code, not narrative)

- `review_output.spawned_capa_id`: `UUID, nullable, NO FK` ([review_output.py:60](../../../apps/api/src/easysynq_api/db/models/review_output.py)).
  `spawned_initiative_id`: same, target table absent. `spawned_task_id` HAS a RESTRICT FK → `task.id`
  (the precedent for adding the CAPA FK on the same model).
- `output_type` enum: `DECISION` / `ACTION` / `IMPROVEMENT` ([_mgmt_review_enums.py:26](../../../apps/api/src/easysynq_api/db/models/_mgmt_review_enums.py)).
  Only ACTION spawns an MR_ACTION task today.
- `CapaSource.review_output` exists, **reserved** — `raise_capa()` 422s it ([capa/service.py:369](../../../apps/api/src/easysynq_api/services/capa/service.py)),
  but `build_capa()` does **not** guard it (it just sets the field) → the un-reserve uses `build_capa`
  directly and leaves the `raise_capa` guard intact (the generic `POST /capas` still refuses
  `review_output`).
- `build_capa(session, actor, *, title, severity: NcSeverity, source, process_id=None,
  origin_finding_id=None, raised_block: dict, _commit=False)` → the `_auto_capa_for_finding` precedent
  ([audits/service.py:296](../../../apps/api/src/easysynq_api/services/audits/service.py)). It mints
  `CAPA_RAISED` (audit) and **no signature** — correct for a recording act (R43).
- `raise_dcr(..., source_link_type: DcrSourceLinkType|None, source_link_id: uuid|None,
  spawn_idempotency_key: str|None, _commit=False)` ([dcr/service.py:172](../../../apps/api/src/easysynq_api/services/dcr/service.py)).
  The DCR holds the link one-way (no reciprocal column on the source — the `raise_dcr_from_capa` 1:N
  precedent, [capa/service.py:1029](../../../apps/api/src/easysynq_api/services/capa/service.py)).
- `DcrSourceLinkType.mgmt_review` exists, reserved, NO FK (polymorphic `source_link_id`). `DcrReasonClass`
  has no `mgmt_review` member ([_dcr_enums.py:58](../../../apps/api/src/easysynq_api/db/models/_dcr_enums.py)).
- The MR close gate `output_blocks_close` reads ONLY the MR_ACTION task state (fail-closed OUTERJOIN);
  it never reads `spawned_capa_id` → F3 (decouple) requires **zero close-gate change**
  ([domain/mgmt_review/close_gate.py](../../../apps/api/src/easysynq_api/domain/mgmt_review/close_gate.py)).
- `_mgmt_review` serializer has **no** `capabilities` block ([api/mgmt_review.py:108](../../../apps/api/src/easysynq_api/api/mgmt_review.py));
  `_release_scope` ([mgmt_review.py:183](../../../apps/api/src/easysynq_api/api/mgmt_review.py)) already
  mirrors `_objective_release_scope` (already calls `enrich_release_sod_scope`).
- `_objective_capabilities` ([objectives.py:338](../../../apps/api/src/easysynq_api/api/objectives.py))
  is the exact template for the `release` capability.
- `ManagementReviewCloseState`: `ActionsTracked` (set at release) → `Closed` (close gate passed). The
  spawn window is `close_state == ActionsTracked` (released, not yet closed).
- Migration head is **`0050`** → next **`0051`**. The DCR domain is backend-only in the SPA; the CAPA
  domain has a full SPA home (board + drawer + `RaiseCapaModal` + the `/capa?capa=<id>` deep-link).

## 3. Design

### 3.1 Migration `0051` (breaks the migration-free streak — intended)

Two additive, round-trippable changes:

1. **FK on `review_output.spawned_capa_id` → `capa.id`** (`ondelete="RESTRICT"`, no `use_alter` —
   `review_output → capa` is acyclic; `capa` does not point back to `review_output`). Named
   `fk_review_output_spawned_capa_id_capa`. **Mirror the FK in the ORM** with the same name (else
   `alembic check` phantom-DROPs it — the engineering-patterns rule). Matches the existing
   `spawned_task_id → task.id` RESTRICT FK on the same model.
2. **`ALTER TYPE dcr_reason_class ADD VALUE 'mgmt_review'`** (additive-enum pattern since 0011; no-op
   downgrade). Add `DcrReasonClass.mgmt_review` to the ORM enum; source the migration tuple from the
   ORM `*_VALUES` (the 0010 precedent). Gives an MR→DCR a precise revision-history justification
   (mirrors `reason_class=capa` for the CAPA loop).

**Downgrade:** drop the FK (always safe — an FK-drop never RESTRICT-aborts, even on a populated DB);
the enum-add is a no-op downgrade. Round-trip up↔down↔`alembic check` on a throwaway PG16, and run a
**populated downgrade** (a `review_output` row with a non-null `spawned_capa_id` present) to prove the
FK-drop doesn't abort. ⚠ Verify in the plan that **no path row-deletes a `capa`** (CAPA disposal
destroys evidence/blobs, not the row; the close lifecycle is state, not delete) — if one ever did,
the RESTRICT FK would block it. The `spawned_task_id`/`origin_finding_id` RESTRICT precedents say this
is the accepted posture.

### 3.2 CAPA spawn (backend + FE)

**Endpoint:** `POST /management-reviews/{review_id}/outputs/{output_id}/raise-capa`
- Sub-path of `/{review_id}` → no S-pack-2 str-convertor shadow; still add a `route.matches` test.
- Body: `{ "severity": NcSeverity }` (Critical/Major/Minor — required; no default).
- Gate: `enforce(... "capa.create", ResourceContext.system())` imperatively (the MR has no process →
  SYSTEM scope; the `create_review`/objectives SYSTEM-enforce precedent). 404 cross-org on the review.
- Preconditions (each a clean 409, never a 500):
  - the output exists, belongs to this review, and is `output_type == ACTION` (else 409
    `output_not_actionable`);
  - `review.close_state == ActionsTracked` (released, not closed) (else 409 `review_not_tracking`);
  - `output.spawned_capa_id is None` — **one-shot latch** (else 409 `capa_already_spawned`, surfacing
    the existing `capa_id` in the problem detail so the FE can deep-link).
- Body of work (the `_auto_capa_for_finding` atomic-pattern):
  `capa = await build_capa(session, actor, title=<derived>, severity=body.severity,
  source=CapaSource.review_output, process_id=None, origin_finding_id=None,
  raised_block={...}, _commit=False)` → set `output.spawned_capa_id = capa.id` → `audit` the link
  (a new `MGMT_REVIEW_CAPA_SPAWNED` event, object_type=document, after={output_id, capa_id}; **no
  signature** — R43) → `await session.commit()`.
- Title: derive from the review identifier + output, e.g. `"CAPA (from management review {identifier})"`
  (the audit-finding title precedent).
- Returns the refreshed `_review_output` (now carrying `spawned_capa_id`). The FE then deep-links to
  the CAPA board.

**Close gate:** UNCHANGED (F3). The CAPA never blocks MR close. Add a regression test proving a
released MR with an ACTION output that has BOTH a DONE MR_ACTION task AND an OPEN spawned CAPA still
closes cleanly.

**Serializer:** `_review_output` gains `"spawned_capa_id": str|null`.

**FE (full):**
- `_review_output` → `ReviewOutput` type gains `spawned_capa_id`.
- In `ReviewOutputsSection.ActionRow`: when the review is `ActionsTracked` and the caller
  `can("capa.create")`:
  - if `output.spawned_capa_id` is null → a **"Raise CAPA"** button → opens a severity-picking modal
    (`RaiseMrCapaModal`, mirroring `RaiseCapaModal` — severity required, `{open && <Modal/>}`
    conditional render so close unmounts/resets it). On success → invalidate the review detail +
    deep-link `/capa?capa=<id>` (the `FindingPanel.tsx:63` audit→CAPA precedent).
  - if `output.spawned_capa_id` is set → a calm **"View CAPA →"** anchor to `/capa?capa=<id>` (the
    one-shot latch; no re-spawn affordance).
- Per-key calm-403: a caller without `capa.create` simply sees no Raise button (affordance gating, not
  a crash). The modal's mutation error degrades calmly.
- A spawned-CAPA badge on the ACTION row when `spawned_capa_id` is set.

### 3.3 DCR spawn (backend-only)

**Endpoint:** `POST /management-reviews/{review_id}/outputs/{output_id}/raise-dcr`
- Mirrors `raise_dcr_from_capa_endpoint` ([api/dcr.py:444](../../../apps/api/src/easysynq_api/api/dcr.py)).
- Body: `{ change_type: DcrChangeType, change_significance: ChangeSignificance, reason_text: str,
  target_document_id: uuid|null }` (CREATE has no target → SYSTEM scope; REVISE/RETIRE require one →
  DOC_CLASS scope).
- Gate: `enforce(... "changeRequest.create", await _dcr_doc_scope(session, body.target_document_id))`
  — the exact `create_dcr_endpoint` / `raise_dcr_from_capa_endpoint` posture. (`_dcr_doc_scope` is the
  helper in `api/dcr.py`; the MR endpoint imports it or replicates it.)
- Preconditions: output exists + belongs to review (404 cross-org); `output_type == ACTION` (409
  `output_not_actionable`); `review.close_state == ActionsTracked` (409 `review_not_tracking`).
- Body of work:
  `dcr = await raise_dcr(session, caller, change_type=..., change_significance=...,
  reason_class=DcrReasonClass.mgmt_review, reason_text=...,
  target_document_id=..., source_link_type=DcrSourceLinkType.mgmt_review,
  source_link_id=output.id, spawn_idempotency_key=<Idempotency-Key header or None>, _commit=False)`
  → `await session.commit()`.
- **1:N, no latch** (an ACTION output may drive multiple document changes — the CAPA→DCR posture). An
  `Idempotency-Key` header makes a *retry* return the same DCR (the `dcr.spawn_idempotency_key`
  partial-UNIQUE scoped to `source_link_id`); distinct keys → distinct DCRs.
- Returns the DCR serializer (`_dcr`).
- **No serializer change on `review_output`** — the link lives one-way on the DCR
  (`source_link_type=mgmt_review`, `source_link_id=output.id`), discoverable via the DCR's own
  `source_link` fields. No reciprocal column, no FE consumer.

**No FE.** The endpoint is reachable by a future DCR-UI track + integration tests + the live smoke
(via curl/CLI). The OpenAPI doc + the integration test ARE the deliverable that proves the seam.

### 3.4 Codex #1 — SoD-aware `capabilities.release` (full)

- New helper `_mr_capabilities(session, caller, doc) -> dict[str, bool]` in `api/mgmt_review.py`,
  computing **only** `release` (the sole SoD-sensitive affordance — submit/close gate on
  `mgmtReview.record_outputs`, no author≠releaser rule). Mirror `_objective_capabilities`'s release
  branch verbatim:
  ```
  rel_ctx = RequestContext(now, actor_user_id=str(caller.id),
                           allow_approver_release=await get_allow_approver_release(session, org))
  release_scope = await _release_scope(session, doc)            # already SoD-2 enriched
  sod = await gather_sod_constraints(session, caller.org_id)
  rel_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
  release = authorize(rel_grants, "document.release", release_scope, rel_ctx,
                      sig_hook=True, sod=sod).allow
  return {"release": release}
  ```
- `_mgmt_review(... capabilities: dict|None = None)` — when not None, include `"capabilities"` in the
  output dict (the `_objective` detail-only kwarg pattern). **Only** `get_review_endpoint` (the detail
  read) passes a computed block; list/create/lifecycle endpoints pass `None` → capabilities is
  detail-only.
- **FE:** `MgmtReviewDetail` type gains `capabilities?: { release: boolean }`. `ManagementReviewDetailPage`
  changes `canRelease` from `can("document.release") && state==="Approved"` to
  `mr.capabilities?.release === true && mr.current_state === "Approved"` (the `ObjectiveDetailPage.tsx:61`
  pattern). The submit/close buttons stay on their existing `can()` gates (no SoD issue).

### 3.5 Contract (`packages/contracts/openapi.yaml`)

- `ReviewOutput`: add `spawned_capa_id: string|null` (uuid).
- `ManagementReviewDetail`: add `capabilities: { release: boolean }` (detail-only).
- New paths: `POST /management-reviews/{id}/outputs/{oid}/raise-capa` (body `{severity}` → ReviewOutput),
  `POST /management-reviews/{id}/outputs/{oid}/raise-dcr` (body `{change_type, change_significance,
  reason_text, target_document_id?}`, `Idempotency-Key` header → Dcr).
- redocly-lint clean.

## 4. Testing strategy

**API unit** (`apps/api/tests/unit`, run natively on this box):
- `route.matches` — the two new sub-paths resolve under `/{review_id}` and don't shadow the literals.
- `_mr_capabilities` release: the frozen-version author gets `release == false` (SoD-2); a distinct
  releaser gets `true` (mirror the objective capability unit test).
- close-gate regression: an ACTION output with a DONE task + an OPEN CAPA still passes
  `output_blocks_close` (F3).

**API integration** (`-m integration`, **CI-only on this Windows box** — ProactorEventLoop + native
crash; run targeted units locally, rely on CI here):
- raise-capa: ACTION output → CAPA at `Raised`, `source == review_output`, severity carried,
  `spawned_capa_id` set, `MGMT_REVIEW_CAPA_SPAWNED` audit, **no signature_event**; one-shot 409 on
  re-spawn; 409 on non-ACTION output; 409 on a non-`ActionsTracked` review; the spawned CAPA does NOT
  block MR close.
- raise-dcr: ACTION output → DCR with `source_link_type==mgmt_review`, `source_link_id==output.id`,
  `reason_class==mgmt_review`; an `Idempotency-Key` replay returns the same DCR; two distinct keys →
  two DCRs (1:N); the `changeRequest.create` gate; 409 on non-`ActionsTracked`; CREATE (no target) →
  SYSTEM scope, REVISE (target) → DOC_CLASS scope.
- ⚠ All integration assertions **run-scoped / delta-based** (the shared session-DB rule); self-provide
  every precondition (no leaning on neighbor rows).

**Migrations** (`/check-migrations`): 0051 up↔down↔`alembic check` on a throwaway PG16 + a **populated
downgrade** (a row with a non-null `spawned_capa_id`).

**Web** (`/check-web` FULL — eslint + strict tsc + build + vitest; the per-file run is blind to the
jest-dom×tsc trap + cross-file drift):
- `RaiseMrCapaModal`: severity-required gating, `{open && <Modal/>}` reopen-resets, success deep-link,
  calm-mutation-error.
- `ReviewOutputsSection`: the Raise-CAPA button gates on `capa.create` + `ActionsTracked` + null
  `spawned_capa_id`; the View-CAPA latch when set; per-key calm-403 (no button without `capa.create`).
- `ManagementReviewDetailPage`: Release button now gates on `capabilities.release` (hidden when false
  even at `Approved` — the SoD-2 fix).
- MSW fixtures pinned to the as-built serializers via `satisfies` (spawned_capa_id, capabilities.release).
- Every new/changed page test imports `{ expect, it }` from `vitest` + adds a **jest-axe smoke**
  (it caught a real heading-order bug on S-mr-2).

**Contracts** (`/check-contracts`): redocly lint clean.

**Pre-PR:** `diff-critic` on the branch diff (false-PASS hunt on the close-gate decouple, the FK
downgrade, the one-shot latch, the SoD-2 capability). **Pre-merge:** Chrome-MCP live smoke (rebuild
api + worker + web; SYSTEM overrides on the live `demo` `app_user` row, org **AHT**, adding `capa.*`
+ `changeRequest.create` + `document.release` — pre-create the row + grant before login so the
affordances appear first load).

## 5. Components & boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| migration `0051` | FK + enum-add | `review_output`, `capa`, `dcr_reason_class` |
| `services/mgmt_review/spawn.py` (or a new `actions.py`) | `spawn_capa_for_output` / `spawn_dcr_for_output` service fns (validate state/type/latch, call `build_capa`/`raise_dcr` `_commit=False`, set the link, audit, no signature) | `build_capa`, `raise_dcr`, the close-state/type guards |
| `api/mgmt_review.py` | the two POST endpoints + `_mr_capabilities` + the `_review_output`/`_mgmt_review` serializer extensions | the service fns, `enforce`, `_dcr_doc_scope`, authz helpers |
| `features/management-review/` (web) | `RaiseMrCapaModal`, the ActionRow Raise/View-CAPA affordances, the Release-button capabilities gate | `useRaiseMrCapa` mutation, `usePermissions`, the deep-link |

Keep the spawn logic in the **service** layer (a `spawn_capa_for_output`/`spawn_dcr_for_output` pair),
so the endpoints stay thin and the guards are unit-testable without HTTP. The CAPA spawn reuses
`build_capa(_commit=False)`; the DCR spawn reuses `raise_dcr(_commit=False)` — neither re-implements
the capture/genesis logic.

## 6. Risks & traps (carry into the plan)

1. **FK mirror** — the migration FK MUST be mirrored in the ORM with the same name or `alembic check`
   phantom-DROPs it. Populated-downgrade test (FK-drop is safe; prove it).
2. **`build_capa` not `raise_capa`** — the un-reserve calls `build_capa(source=review_output)` directly;
   leave the `raise_capa` 422-guard intact (generic `POST /capas` must still refuse `review_output`).
3. **No signature on either spawn** (R43) — both are recording acts; `build_capa`/`raise_dcr` already
   mint only audit events. Assert no `signature_event` in the integration tests.
4. **Close gate untouched** (F3) — the decouple is the *absence* of a change; pin it with the
   open-CAPA-still-closes regression test.
5. **One-shot latch (CAPA) vs 1:N (DCR)** — CAPA latches on `spawned_capa_id` (409 on re-spawn); DCR
   does not latch (Idempotency-Key for retry-safety only). Don't cross-wire them.
6. **Atomic commit** — set the link + audit in the SAME transaction as `build_capa(_commit=False)` /
   `raise_dcr(_commit=False)`; flush before any audit that needs the new uuid PK (the S-mr-1 flush-
   before-emit trap).
7. **Detail-only capabilities** — only the detail read computes the block; list/create pass `None`.
8. **FE traps** — `{open && <Modal/>}`, `import {expect,it} from "vitest"`, jest-axe smoke, MSW
   `satisfies`, calm-403 `forbidden`/`retry:false`, full `/check-web`.
9. **Windows verification reality** — integration + full-unit are CI-only here; verify backend locally
   via ruff + mypy + targeted units; FE runs fully native.
10. **Codex triage** — disregard D1-moot multi-tenant framing; verify each claim vs code (Codex caught
    a real byte-path gap on S-mr-1 the diff-critic missed).

## 7. Out of scope (named)

MR minutes revision (F6); `improvement_initiative` + `spawned_initiative_id` (no target table); the
MR→DCR front-end affordance + any DCR SPA surface (future DCR-UI track); broader-than-ACTION output
spawning; the MR-Pack PDF (v1.1); Top-Management approval routing.
