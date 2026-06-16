# Plan — S-improvement (Improvement Initiatives, clause 10.3)

> Implementation plan for the spec
> [`2026-06-15-s-improvement-1-continual-improvement-design.md`](../specs/2026-06-15-s-improvement-1-continual-improvement-design.md).
> **Spec + plan APPROVED (owner, 2026-06-15).** Implementation may begin with S-improvement-1. Branch `feat/s-improvement-1`
> (spec only); each build slice lands on its own `feat/s-improvement-N` branch → PR → green CI →
> squash-merge. Migration head **0051 → 0052** (slice 1 only; slices 2–4 are zero-migration).

## Build order & dependencies

```
S-improvement-1 (backend core, mig 0052)  ──►  S-improvement-2 (spawn wiring, zero-mig)
        │                                              │
        └──────────────────►  S-improvement-3 (web register + drawer + tile)
S-improvement-4 (effectiveness/approval) — DEFERRED, opt-in only
```

Slice 2 and slice 3 both depend on slice 1; slice 3 can proceed against slice-1 endpoints and add the
raise affordances once slice 2's spawn endpoints exist (build 1 → 2 → 3).

---

## Slice 1 — S-improvement-1 (backend core)

**Goal:** the `improvement_initiative` own-table workflow object + lifecycle API + the `improvement.*`
authz keys, migration `0052`, register R46. Spawn-seam columns/enums ship here so slice 2 is zero-migration.

### Step 1 — enums (ORM)
- New `apps/api/src/easysynq_api/db/models/_improvement_enums.py`:
  `ImprovementStage` (`Open`,`InProgress`,`Completed`,`Closed`,`Cancelled`),
  `ImprovementSource` (`OFI`,`review`,`manual`); `*_enum = SAEnum(..., values_callable=_vals, create_type=False)`;
  export `IMPROVEMENT_STAGE_VALUES` / `IMPROVEMENT_SOURCE_VALUES` tuples (the `_capa_enums` precedent).
- Extend `_audit_enums.py`: `AuditObjectType.improvement_initiative`; `EventType.INITIATIVE_RAISED`,
  `INITIATIVE_UPDATED`, `INITIATIVE_TRANSITIONED`, `MGMT_REVIEW_INITIATIVE_SPAWNED` (with the standard
  inline comment citing migration 0052). `AUDIT_OBJECT_TYPE_VALUES`/`EVENT_TYPE_VALUES` pick them up.

### Step 2 — models (ORM)
- `db/models/improvement_initiative.py` (§3.1 columns; `UNIQUE(org_id,identifier)` + the 3 indexes;
  FK names < 63 chars).
- `db/models/improvement_initiative_stage_event.py` (§3.2; `signed_event_id` FK present, no `updated_at`).
- Register **both** in `db/models/__init__.py` import block + `__all__` (the 0027 phantom-DROP gate).

### Step 3 — migration `0052_improvement_initiatives.py`
- `down_revision = "0051"`.
- `CREATE TYPE improvement_stage / improvement_source` sourced from the ORM `*_VALUES` tuples.
- `ALTER TYPE audit_object_type ADD VALUE 'improvement_initiative'` + the 4 `event_type` ADD VALUEs,
  inside `op.get_context().autocommit_block()`.
- `create_table improvement_initiative` (+ the partial-UNIQUE `uq_improvement_initiative_spawn … WHERE spawn_idempotency_key IS NOT NULL`).
- `create_table improvement_initiative_stage_event` + the pg_roles-guarded `GRANT SELECT,INSERT; REVOKE UPDATE,DELETE` DO block.
- App-role `GRANT SELECT,INSERT,UPDATE` on `improvement_initiative`.
- Seed `improvement.read` / `improvement.manage` (`on_conflict_do_nothing(['key'])`) + role grants
  (`on_conflict_do_nothing(['org_id','role_id','permission_id'])`, resilient org lookup) — the 0028 recipe.
- `downgrade`: NOT-EXISTS-guarded; delete `permission_override → role_grant → permission` for the 2 keys;
  drop the 2 tables; drop the 2 enums; `ALTER TYPE` ADD VALUEs are no-op on downgrade.
- Exclude `uq_improvement_initiative_spawn` in `migrations/env.py._include_object`.

### Step 4 — domain FSM
- `domain/improvement/__init__.py` + `domain/improvement/fsm.py`: the `IMPROVEMENT_TRANSITIONS:
  dict[ImprovementStage, frozenset[ImprovementStage]]` edge map (§4) + `allowed_targets` /
  `transition_allowed` / `is_terminal` helpers. Pure, no I/O.

### Step 5 — service
- `services/improvement/__init__.py`, `repository.py`, `service.py`:
  - `create_initiative(...)` (manual; `allocate_seq(org,'IMP',year)` + `format_identifier`; genesis `Open`
    stage_event + `INITIATIVE_RAISED`; `_commit` param for reuse by slice-2 spawns).
  - `transition_initiative(...)` (FOR UPDATE + `populate_existing` → `transition_allowed` 409
    `improvement_transition_invalid` → append stage_event → flip stage (+ `closed_at`) → `INITIATIVE_TRANSITIONED`).
  - `update_initiative(...)` (metadata PATCH → `INITIATIVE_UPDATED`).
  - `list_initiatives(...)` (the `gather_grants`+`authorize` row-filter with the FULL `ResourceContext`),
    `get_initiative`, `list_stage_events`.
  - `_improvement_scope(initiative)` async resolver (`process_ids={process_id}` else `system()`).

### Step 6 — API
- `api/improvement.py`: the 6 slice-1 endpoints (§5) with `require("improvement.read"/"improvement.manage")`
  dependencies / imperative `enforce(...)` on create (the raise-on-body precedent); request/response
  Pydantic schemas; register the router in `main.py`.

### Step 7 — contract + register
- Document the 6 endpoints + schemas in `packages/contracts/openapi.yaml`.
- Add **R46** to `docs/decisions-register.md` (the additive keys, role grants, catalog 100→102, the
  own-table-over-RECORD posture note); bump `test_authz.py:133` to `102` + its comment.

### Step 8 — verify
- `/check-migrations` (round-trip up↔down↔`alembic check` on PG16) · `/check-api` (ruff + mypy-strict +
  **targeted** unit tests; integration + full-unit are **CI-only** on this Windows box) · `/check-contracts`.
- Targeted unit tests: the pure FSM (all edges + terminals + illegal-edge 409); a service-level happy path.
- Run `migration-reviewer` on 0052, then `diff-critic` on the branch diff. PR → green CI (all 5 jobs).

---

## Slice 2 — S-improvement-2 (spawn wiring) — zero-migration

**Goal:** close the reserved seam — OFI-finding + MR-output spawn into an initiative.

- **`POST /findings/{id}/raise-initiative`** (in `api/audits.py` or `api/improvement.py`): gate
  `improvement.manage`; **422 `finding_not_improvable`** unless `finding_type ∈ {OBSERVATION, OFI}`;
  `Idempotency-Key` header; calls `create_initiative(_commit=False, source=OFI, source_link_id=finding.id,
  process_id=finding.process_id)`; replay-lookup FIRST → guard → `IntegrityError → rollback → re-lookup`.
- **`POST /management-reviews/{id}/outputs/{oid}/raise-initiative`** (in `api/mgmt_review.py`): gate
  `improvement.manage` (enforce-in-handler, the `raise-capa`/`raise-dcr` pattern); guards review
  `close_state=ActionsTracked` + output type `∈ {ACTION, IMPROVEMENT}`; `source=review`,
  `source_link_id=output_id`; emits `INITIATIVE_RAISED` + `MGMT_REVIEW_INITIATIVE_SPAWNED`
  (`object_type=document`); **F5: leave `review_output.spawned_initiative_id` reserved-null** (unless the
  owner flips to the latch at sign-off — then un-reserve it to a real FK per the 0051 `spawned_capa_id`
  recipe + a `0053` migration).
- **NO `signature_event`** on either spawn (R43) — assert it.
- Add `spawned_initiative_id` to the `_review_output` serializer ([api/mgmt_review.py](../../../apps/api/src/easysynq_api/api/mgmt_review.py)),
  the FE `ReviewOutput` type, and the OpenAPI `ReviewOutput` schema.
- **Verify:** `/check-api`, `/check-contracts`; integration (CI) for idempotency + no-signature + the
  422 + the MR-close-gate-unchanged proof; `diff-critic`.

---

## Slice 3 — S-improvement-3 (web)

**Goal:** the Improvement › Continual Improvement register + drawer + raise affordances + the ACT tile.

- `apps/web/src/features/improvement/`: `ImprovementListPage` (register-triage primitives — search/sort/
  keyboard/URL-state, critique #5), `InitiativeDrawer`/page (stage timeline from `stage-events`, linked-
  source deep-link, transition/edit forms gated `improvement.manage`, `forbidden` panel on a lacked read),
  a `StatusBadge` for `improvement_stage` (tones per §8, reconciled to the app-wide convention).
- Raise affordances: a "Raise Initiative" button on an OFI/Observation finding detail and on an MR
  `IMPROVEMENT`/`ACTION` output (gated `improvement.manage`; submit-and-show).
- PDCA-ACT quadrant tile: count-by-stage pipeline + aging RAG (calm table, NOT charts — N6/N9).
- Nav: add "Continual Improvement" under the Improvement group.
- **Web-test traps:** MSW fixtures pinned to the real serializer via `satisfies`; `expect`/`it` from
  `"vitest"`; distinct `aria-label`s; `/me`.id identity; `scrollIntoView` stub for any Combobox.
- **Verify:** full `/check-web` (eslint + strict tsc + build + vitest); `web-test-trap-reviewer`;
  optional live smoke (grant SYSTEM `improvement.*` overrides to the live `demo` app_user).

---

## Slice 4 — DEFERRED (opt-in only)

The optional unsigned **Verified** benefit-review stage (verdict frozen into the sealed
`stage_event.payload`; if ever signed, reuse `meaning='verify'` + the pre-generated-UUID seam) and/or an
engine-routed **management-authorization** approval (a new `WorkflowSubjectType`, the DCR/CAPA engine
precedent). Built only if the owner requests it after the family is live.

---

## Risks & guards

- **alembic-check phantom-DROP** — the 0052 partial-UNIQUE must be `env.py`-excluded; FK/CHECK names mirrored
  in the ORM; both models registered in `__init__.py`. `migration-reviewer` is the backstop.
- **Stale identity map** — every locked load uses `.execution_options(populate_existing=True)` (S-drift-1).
- **Shared-DB integration assertions** — delta-based / run-scoped; self-provide preconditions (S-ing-4 /
  S-drift-2).
- **Windows local-verify** — integration + full-unit suites are CI-only here; locally rely on ruff +
  mypy-strict + targeted units (the windows-unit-failure baseline).
- **Codex follow-ups** — budget a review round; Codex repeatedly catches P1/P2s a clean diff-critic misses.
- **finish-slice** — record each shipped slice via the `/finish-slice` skill (slice-history + CLAUDE.md
  learning + memory resume note + test deltas).
