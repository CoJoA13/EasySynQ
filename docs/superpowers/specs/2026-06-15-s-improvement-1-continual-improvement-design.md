# S-improvement — Improvement Initiatives (ISO 9001 clause 10.3, Continual Improvement)

> Spec. Status: **APPROVED** (owner, 2026-06-15 — all four §13 sign-offs accepted as proposed). Date: 2026-06-15. Branch: `feat/s-improvement-1`.
> Opens a NEW v1.x feature family — the last named-but-unbuilt entity in doc 14 §9
> (`improvement_initiative`, line 459). Closes the long-reserved `review_output.spawned_initiative_id`
> seam (S-mr-3 §deferred: "points at a table that does not exist anywhere in the codebase … stays
> reserved-null — there is nothing to wire it to"). Proposes **decisions-register R46** (the additive
> `improvement.read` / `improvement.manage` keys). Migration head **0051 → 0052**.

---

## 0. Owner decisions (locked this session)

The brainstorm fan-out (6 parallel precedent readers + synthesis) surfaced six forks; the owner locked
the four that needed a call, and ratified two secondary recommendations by non-objection.

| # | Decision | Choice | Notes |
|---|----------|--------|-------|
| **F1** | Storage classification | **Own-table mutable-state workflow object** (the DCR / **R22** doctrine) | NOT a `documented_information` subtype, NOT a `kind=RECORD` subtype. See §1, §3. |
| **F2** | Lifecycle richness + effectiveness review | **Simple stage-completion close** (unsigned) | `Open → InProgress → Completed → Closed` (+ `Cancelled`). No signed gate, no not-effective loop. SignatureMeaning stays closed (R2). The Verified/effectiveness stage is a named §11 deferral. |
| **F3** | Which sources seed an initiative | **OFI-finding one-click + MR-output spawn + standalone manual raise** | `source` enum `{OFI, review, manual}`. Objective-miss / 9.1.3 auto-seed deferred (undocumented). |
| **F4** | Permission keys | **Additive `improvement.read` / `improvement.manage`** | CONTENT-domain, PROCESS finest-scope, via a new register entry **R46**. Catalog 100 → 102. NOT riding `capa.*` (the R41/R42 anti-pattern). |
| **F5** (secondary, ratified) | Spawn cardinality | **1:N one-way `source_link_id`** on the initiative | The OFI source needs `source_link_id` regardless → one uniform mechanism. `review_output.spawned_initiative_id` is **left reserved-null** (not dropped). The latch alternative is re-flagged in §13 for sign-off. |
| **F6** (secondary, ratified) | Management-authorization gate | **None in v1.x** (unsigned lifecycle) | The optional engine-routed approval (new `WorkflowSubjectType`) is a named §11 deferral. |

**Settled by data, not a fork:** **clause 10.3 is NON-★.** The frozen clause seed carries
`is_mandatory_star=False` for 10.3 ([iso9001_clauses.py](../../../apps/api/src/easysynq_api/db/seeds/iso9001_clauses.py)),
doc 02 §2.1's consolidated ★ list ends at 10.2, and doc 02:157 classifies `ImprovementInitiative` as a
**retained record (R)** with a blank ★ column. The compliance checklist only scans
`is_mandatory_star=True` clauses and only counts `current_effective_version_id`
([checklist.py:84-86](../../../apps/api/src/easysynq_api/services/reports/checklist.py)). So the
"must be a DOCUMENT to flip the ★ node" rationale that justified routing OBJ (R44) and MR (R45) through
the `kind=DOCUMENT` path is **absent here** — there is nothing to flip. See §9.

---

## 1. Why this slice — and why an own table

Clause 10.3 requires the organisation to **continually improve the suitability, adequacy and
effectiveness of the QMS**, considering the results of analysis & evaluation (9.1.3) and the outputs of
management review (9.3.3) to identify improvement needs/opportunities (verbatim clause seed intent,
[iso9001_clauses.py](../../../apps/api/src/easysynq_api/db/seeds/iso9001_clauses.py); doc 02 §2 Clause-10
note: "Clause 10 is the ACT stage; CAPAs and improvement initiatives feed back into Clause 6 planning and
Clause 4 context — the loop closes"). It is the **only** named-but-unbuilt entity left in the data model
(doc 14 §9:459, "deferred/unbuilt in v1").

An improvement initiative's essence is a **progressing activity owned over time** — it moves through a
lifecycle, it is not a frozen versioned commitment and not point-in-time immutable captured content. That
is precisely the **own-table mutable-state workflow object** the DCR family established under **R22** ("a
controlled workflow object, NOT a `kind=RECORD` immutable artifact"), and exactly the shape doc 14 §9
already sketches (`id`, `org_id`, `title`, `stage`, `source (OFI/review)`). The two heavier alternatives
were rejected with reason:

- **`kind=DOCUMENT` shared-PK subtype (OBJ/MR shape, R44/R45):** buys versioning/approve/release/Library
  "for free" but forces a `submit → approve → release` gate that does not model "continual improvement,"
  and — because 10.3 is non-★ — buys **zero** checklist benefit. The very rationale that justified R44/R45
  is absent. Heaviest, worst-fit. (Copying it here by blind analogy is a documented category error.)
- **`kind=RECORD` shared-PK subtype (CAPA/finding shape):** matches doc 02's `M/R='R'` framing and would
  reuse `record.*` keys + `object_type='record'` + the evidence/disposal machinery — but a record is
  point-in-time **immutable** captured content, whereas an initiative's headline is a **moving stage**, so
  most state would live awkwardly outside the immutable capture, and the WORM-destroy/disposal overhead is
  irrelevant to a tracker. (Re-classifying away from doc 02's `R` framing is itself a register-level
  posture call — recorded in R46.)

This slice family delivers: the model + lifecycle (slice 1), the spawn wiring that closes the reserved
seam (slice 2), and the register/drawer/dashboard UI (slice 3).

---

## 2. Ground truth (verified against code, not narrative)

- **doc 14 §9:459** prescribes `improvement_initiative | id PK, org_id, title, stage, source (OFI/review) | Cl 10.3`.
- **`review_output.spawned_initiative_id`** is a bare `UUID, nullable, NO FK` — the target table is absent
  ([review_output.py:65-67](../../../apps/api/src/easysynq_api/db/models/review_output.py)); plus a
  reserved-inert `ReviewOutputType.IMPROVEMENT` enum value. `spawned_capa_id` was un-reserved to a real FK
  in S-mr-3 / migration 0051 — the exact recipe if the latch alternative (§13) were ever chosen.
- **`FindingType` = `NC` / `OBSERVATION` / `OFI`** ([_iso_audit_enums.py:31-38](../../../apps/api/src/easysynq_api/db/models/_iso_audit_enums.py)).
  doc 10 §5.3:306: "Observation / OFI → optional CAPA or improvement initiative; no auto-creation, but a
  one-click 'Raise CAPA/Initiative' from the finding." NC findings auto-create CAPAs (not initiatives).
- **No `improvement.*` permission key exists** (grep of the 0004 seed is empty); catalog count is **100**
  ([test_authz.py:133](../../../apps/api/tests/integration/test_authz.py) `assert len(perms) == 100`).
- **`AuditObjectType`** is a closed set extended only by additive `ALTER TYPE … ADD VALUE` (the `ncr`/`dcr`
  own-table precedents, lines 70/75 of [_audit_enums.py](../../../apps/api/src/easysynq_api/db/models/_audit_enums.py)).
  `EventType` is the explicitly-extensible enum; both source their migration tuples from the ORM
  `AUDIT_OBJECT_TYPE_VALUES` / `EVENT_TYPE_VALUES` (the 0010/0011 rule).
- **DCR own-table template** ([dcr.py:61-142](../../../apps/api/src/easysynq_api/db/models/dcr.py)):
  mutable `state` (`server_default 'Open'`), `UNIQUE(org_id, identifier)`, polymorphic `source_link_id`
  (UUID, NO FK), `spawn_idempotency_key`; the append-only `dcr_stage_event` (`REVOKE UPDATE,DELETE`,
  pg_roles-guarded GRANT block, no `updated_at`); `audit_object_type='dcr'`; `DCR-{YYYY}-{SEQ}` via
  `allocate_seq` + `format_identifier`.
- **MR-output spawn endpoints enforce the TARGET system's create key:** `raise-capa` → `capa.create`
  ([mgmt_review.py:514](../../../apps/api/src/easysynq_api/api/mgmt_review.py)); `raise-dcr` →
  `changeRequest.create` ([mgmt_review.py:538](../../../apps/api/src/easysynq_api/api/mgmt_review.py)).
  The MR close gate `output_blocks_close` reads ONLY the `MR_ACTION` task state, never `spawned_*` → a
  spawned initiative does **not** block MR close (no close-gate change).
- **`clause_mapping`** keys on `documented_information.id`; an own-table initiative is **not** a
  `documented_information` row, so it takes **no** `clause_mapping` row (cf. the OBJ auto-map to 6.2 which
  worked only because OBJ *is* documented_information). The 10.3 association is structural/implicit.
- **`raise_dcr_from_capa`** is the 1:N one-way spawn precedent: replay-lookup FIRST, then the terminal /
  mutable-state 409 gate, then `IntegrityError → rollback → re-lookup` for the concurrent-winner race
  ([capa/service.py:1004-1087](../../../apps/api/src/easysynq_api/services/capa/service.py)).

---

## 3. Data model (migration `0052`)

Two new tables + two new enums. **All spawn-seam columns ship in slice 1** so slice 2 (spawn wiring) is
**zero-migration** (the DCR precedent: `source_link_*` shipped in 0040, the CAPA→DCR spawn wired in 0044).

### 3.1 `improvement_initiative` (headline, mutable)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` PK, default `uuid4` | |
| `org_id` | `uuid` FK → `organization.id` RESTRICT, NOT NULL | §1.1 org convention |
| `identifier` | `Text` NOT NULL | `IMP-{YYYY}-{NNNN}` via `allocate_seq(org,'IMP',year)` + `format_identifier('IMP',seq,year,pad=4)`; `UNIQUE(org_id, identifier)` |
| `title` | `Text` NOT NULL | |
| `description` | `Text` nullable | |
| `target_outcome` | `Text` nullable | the intended improvement / success measure (10.3 "opportunity") |
| `source` | `improvement_source` enum NOT NULL | `{OFI, review, manual}` |
| `source_link_id` | `uuid` nullable, **NO FK** | polymorphic origin id: a `finding.id` (source=OFI) or a `review_output.id` (source=review); NULL for `manual` (the `dcr.source_link_id` precedent) |
| `spawn_idempotency_key` | `Text` nullable | the `Idempotency-Key` header value (1:N spawn retry-safety; see partial-UNIQUE below) |
| `process_id` | `uuid` FK → `process.id` RESTRICT, nullable | PROCESS-scoped authz selector |
| `owner_user_id` | `uuid` FK → `app_user.id` RESTRICT, nullable | the accountable owner |
| `stage` | `improvement_stage` enum NOT NULL, `server_default 'Open'` | the mutable headline |
| `opened_at` | `timestamptz` NOT NULL, `server_default now()` | |
| `closed_at` | `timestamptz` nullable | set at the `Closed`/`Cancelled` transition |
| `created_by` | `uuid` FK → `app_user.id` RESTRICT, NOT NULL | |
| `created_at` | `timestamptz` NOT NULL, `server_default now()` | |
| `updated_at` | `timestamptz` nullable, `onupdate now()` | the headline is mutable |

Indexes: `UNIQUE(org_id, identifier)`; `ix_improvement_initiative_org_id_stage (org_id, stage)`;
`ix_improvement_initiative_source_link_id (source_link_id)`; `ix_improvement_initiative_process_id`.
**Partial-UNIQUE** `uq_improvement_initiative_spawn (org_id, source_link_id, spawn_idempotency_key) WHERE spawn_idempotency_key IS NOT NULL`
— a migration-managed partial index that **MUST be excluded** in `migrations/env.py._include_object`
(the 0020 GIN / the DCR `spawn_idempotency_key` precedent; otherwise `alembic check` phantom-reports it).

App role grant: **`SELECT, INSERT, UPDATE`** (stage / owner / closed_at mutate; never `DELETE`).

### 3.2 `improvement_initiative_stage_event` (append-only trail)

The immutable transition history — the load-bearing append-only artifact (the `dcr_stage_event` /
`capa_stage` / `signature_event` house style).

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` PK, default `uuid4` | |
| `org_id` | `uuid` FK → `organization.id` RESTRICT, NOT NULL | |
| `initiative_id` | `uuid` FK → `improvement_initiative.id` RESTRICT, NOT NULL | |
| `from_state` | `improvement_stage` enum nullable | NULL on genesis (the raise/create event) |
| `to_state` | `improvement_stage` enum NOT NULL | |
| `actor_id` | `uuid` FK → `app_user.id` RESTRICT, nullable | NULL for a future system/Beat move; always a user in v1.x |
| `comment` | `Text` nullable | |
| `payload` | `JSONB` nullable | the sealed per-transition narrative (e.g. the `Closed` outcome / realized-benefit note — the lightweight 10.3 evidence) |
| `signed_event_id` | `uuid` FK → `signature_event.id` RESTRICT, nullable | **ships day-one, stays NULL/unsigned in v1.x** — the D3 Part-11 reserved hook |
| `occurred_at` | `timestamptz` NOT NULL, `server_default now()` | **no `updated_at`** |

Index `ix_improvement_initiative_stage_event_initiative_id`. App role grant: a **pg_roles-guarded DO block**
`GRANT SELECT, INSERT … ; REVOKE UPDATE, DELETE … FROM easysynq_app` (structural append-only — without the
REVOKE, immutability is merely conventional). **If** a stage is ever signed (the §11 deferred effectiveness
review), it MUST use the **pre-generated-UUID + flush + two mutually-referencing INSERTs** seam (never an
UPDATE — the table is REVOKE-immutable; the `capa_stage` precedent
[capa/service.py:808-833](../../../apps/api/src/easysynq_api/services/capa/service.py)).

### 3.3 Enums (CREATE TYPE, values sourced from ORM `*_VALUES`)

- **`improvement_stage`** = `Open`, `InProgress`, `Completed`, `Closed`, `Cancelled`
- **`improvement_source`** = `OFI`, `review`, `manual` (lowercase `review`/`manual` extends the R2/R16
  lowercase-token precedent; `OFI` is upper to match `FindingType.OFI`)

Both bound `create_type=False` on the SAEnum; the migration's `CREATE TYPE` sources its tuples from
`IMPROVEMENT_STAGE_VALUES` / `IMPROVEMENT_SOURCE_VALUES` so DDL and ORM never drift (the 0010 rule).

### 3.4 Audit-keying enums (additive `ALTER TYPE … ADD VALUE` in 0052)

- `AuditObjectType` **+ `improvement_initiative`** (an own-table id is not a doc/record id — it CANNOT
  reuse `document`/`record`; the `ncr`/`dcr` precedent).
- `EventType` **+ `INITIATIVE_RAISED`** (intake/create) **+ `INITIATIVE_UPDATED`** (metadata edit)
  **+ `INITIATIVE_TRANSITIONED`** (any stage move) **+ `MGMT_REVIEW_INITIATIVE_SPAWNED`** (the MR-side
  spawn act, `object_type='document'`, mirroring `MGMT_REVIEW_CAPA_SPAWNED`/`_DCR_SPAWNED`; defined in 0052
  though first used in slice 2, so slice 2 is zero-migration).

`improvement_initiative`/`_stage_event` events key on `object_type=improvement_initiative`,
`object_id=initiative.id`, `scope_ref=identifier`, emitted by a direct `session.add(AuditEvent(...))` before
commit (the DCR `_emit` pattern, not `VaultAuditSink`). The MR-output spawn additionally emits
`MGMT_REVIEW_INITIATIVE_SPAWNED` on `object_type=document` (the MR id), `scope_ref=<MR identifier>`.

### 3.5 ORM registration & constraint mirroring (the recurring traps)

- The two new model modules MUST be imported in
  [db/models/__init__.py](../../../apps/api/src/easysynq_api/db/models/__init__.py) and added to `__all__`
  (the 0027 phantom-DROP lesson — `Base.metadata` is populated only there).
- Every migration-created FK/CHECK MUST be mirrored in the ORM with a **name-matching** constraint
  (`alembic check` phantom-DROPs an unmirrored FK; a `ck` token must be the **bare** token in both places to
  avoid the doubled `ck_…_ck_…` re-tokenization — the 0037 lesson).
- No `kind=RECORD`/`documented_information` rows are created → **no blob, no disposition, no WORM-destroy
  path, no mirror entry** (see §10).

---

## 4. Lifecycle / FSM

A pure, I/O-free FSM in **`domain/improvement/fsm.py`** declaring the full edge map up-front (forward-compat,
unit-testable with zero DB — the `domain/dcr/fsm.py` / `domain/capa/fsm.py` precedent):

```
Open        → {InProgress, Cancelled}
InProgress  → {Completed, Cancelled}
Completed   → {Closed}
Closed      → {}            # terminal
Cancelled   → {}            # terminal
```

- **Genesis** = `Open` (a `stage_event` with `from_state=NULL, to_state=Open` written at raise/create).
- **`Cancelled`** is reachable only from the pre-completion states `{Open, InProgress}` (the DCR
  "Cancelled only from pre-approval states" posture); a `Completed` initiative is `Closed`, never cancelled.
- **`Closed`** is the terminal "filed" state; its transition `payload` MAY carry a free-text
  outcome / realized-benefit note — the **lightweight 10.3 "continual improvement evidence"**, frozen into
  the sealed (REVOKE-immutable) `stage_event`. This is *not* a signed gate and not recomputed (the
  S-obj-charts "frozen verdict" discipline applies if any verdict-like field is ever added).
- **Unsigned** in v1.x: clause 10.3 mandates no per-initiative sign-off; `SignatureMeaning` /
  `SignedObjectType` stay closed (R2). No not-effective loop (that is CAPA's 10.2 job).

Service skeleton per move (one transaction): `select(...).with_for_update().execution_options(populate_existing=True)`
on the initiative (the S-drift-1 stale-identity-map trap — a `require(...)`-loaded row in the identity map
returns stale attrs under a plain locked load) → `transition_allowed(from, to)` 409-guard
(`improvement_transition_invalid`) → append `stage_event` → flip `stage` (+ set `closed_at` on
`Closed`/`Cancelled`) → emit `INITIATIVE_TRANSITIONED` → commit.

---

## 5. API surface

Router prefix `/api/v1`, tag `improvement`. OpenAPI documented **in-PR**
([packages/contracts/openapi.yaml](../../../packages/contracts/openapi.yaml)).

### Slice 1 — lifecycle
| Method · path | Gate | Behaviour |
|---|---|---|
| `POST /improvement-initiatives` | `improvement.manage` | manual create (`source=manual`); allocates `IMP-YYYY-NNNN`; genesis `Open` + `INITIATIVE_RAISED` |
| `GET /improvement-initiatives` | `improvement.read` | list; filters `stage` / `source` / `owner_user_id` / `process_id`; row-filtered by grant scope |
| `GET /improvement-initiatives/{id}` | `improvement.read` | detail |
| `GET /improvement-initiatives/{id}/stage-events` | `improvement.read` | the append-only trail (oldest→newest) |
| `PATCH /improvement-initiatives/{id}` | `improvement.manage` | edit `title`/`description`/`target_outcome`/`owner_user_id`/`process_id`; emits `INITIATIVE_UPDATED` (mutable metadata only — never `stage`) |
| `POST /improvement-initiatives/{id}/transition` | `improvement.manage` | body `{to_state, comment?, outcome?}`; FSM-guarded; the single move endpoint (covers `InProgress`/`Completed`/`Closed`/`Cancelled`; a `Cancelled`/`Closed` move requires a `comment`) |

`outcome` (when present, on a `Closed` move) is folded into the sealed `stage_event.payload`.

### Slice 2 — spawn (zero-migration)
| Method · path | Gate | Behaviour |
|---|---|---|
| `POST /findings/{id}/raise-initiative` | `improvement.manage` | the OFI one-click. **422 `finding_not_improvable`** unless `finding_type ∈ {OBSERVATION, OFI}` (NC auto-creates a CAPA). `source=OFI`, `source_link_id=finding.id`, `process_id` inherited from the finding. `Idempotency-Key` header → 201 new / 200 replay. 1:N (distinct keys → distinct initiatives). |
| `POST /management-reviews/{review_id}/outputs/{output_id}/raise-initiative` | `improvement.manage` | mirrors `raise-capa`/`raise-dcr` (enforce the target-system create key). `source=review`, `source_link_id=output_id`. Guards: review `close_state=ActionsTracked`, output type `∈ {ACTION, IMPROVEMENT}`. `Idempotency-Key` → 201/200. Emits `INITIATIVE_RAISED` (initiative) + `MGMT_REVIEW_INITIATIVE_SPAWNED` (MR, `object_type=document`). `review_output.spawned_initiative_id` left reserved-null (F5; §13). |

Spawn ordering (the `raise_dcr_from_capa` recipe): **idempotent-replay lookup FIRST → then the
guard/409 → then `IntegrityError → rollback → re-lookup`** for the concurrent-winner race. A spawn is a
**recording act → NO `signature_event`** (R43) — asserting no signature is a load-bearing test.

Slice 2 also adds `spawned_initiative_id` to the `_review_output` serializer + the FE `ReviewOutput` type
+ the OpenAPI `ReviewOutput` schema (currently omitted) so the FE can deep-link a spawned initiative.

---

## 6. Authorization — proposed register entry R46

Two **additive (R38)** CONTENT-domain keys, seeded in 0052:

| key | resource | action | is_system_domain | sod_sensitive | sig_hook | finest_scope |
|---|---|---|---|---|---|---|
| `improvement.read` | `improvement` | `read` | `false` | `false` | `false` | `PROCESS` |
| `improvement.manage` | `improvement` | `manage` | `false` | `false` | `false` | `PROCESS` |

Seed recipe (the 0028 `retention.read`/`retention.manage` precedent):
`pg_insert(permission_t).values([...]).on_conflict_do_nothing(['key'])`; resilient org lookup
(`short_code='DEFAULT'` else `scalar_one` over all orgs — this install's is **`AHT`**);
`role_grant(...).on_conflict_do_nothing(['org_id','role_id','permission_id'])` with a `scope_template`;
downgrade deletes **`permission_override` → `role_grant` → `permission`** (the RESTRICT-FK order). Bump
`test_authz.py:133` `assert len(perms) == 100` → `102` and update its comment.

**Role grants (for owner confirmation in §13):**
- `improvement.read` + `improvement.manage` → **QMS Owner** (org/QMS scope) and **Process Owner**
  (PROCESS-scoped via the `:assignment_process` placeholder — rides SYSTEM overrides until owner-assignment
  binding lands, the S-dcr-1 / R39 backfill recipe).
- `improvement.read` → **Internal Auditor** (the checklist-read precedent — the auditor raises OFIs and
  reads the improvement pipeline but does not drive initiatives).

Scope resolver `_improvement_scope` (the `_objective_scope`/`_capa` precedent):
`ResourceContext(process_ids={initiative.process_id})` when set, else `ResourceContext.system()`. The
listing reuses the `gather_grants` + `authorize` row-filter populating the **full** `ResourceContext`
(process_ids), or a PROCESS-scoped grant silently mis-denies (the S-pack-1 R28 lesson). On this install
`demo` (System Administrator) holds no CONTENT keys → live smoke needs SYSTEM overrides for `improvement.*`.

**Why not ride `capa.*`** (rejected): conflates corrective action (10.2) with improvement opportunity
(10.3) and silently widens every CAPA holder's reach — the exact R41/R42 anti-pattern. Permission keys are
additive-only/permanent (R38), so the clean separation is worth the catalog growth.

---

## 7. Relationship to CAPA & Management Review

`improvement_initiative` is a **sibling to CAPA under clause 10**, not a subtype of it and not routed
through `capa_source`:

- **CAPA (10.2)** = corrective action reacting to a *nonconformity*: severity-routed approval, the M4
  effectiveness gate, signed `verify`. **Initiative (10.3)** = a continual-improvement *opportunity*:
  lighter, unsigned, no approval gate.
- They **share the spawn mechanics**: an OFI/Observation finding offers a one-click *Raise CAPA **or**
  Initiative* (doc 10 §5.3); an MR ACTION/IMPROVEMENT output can spawn either. The initiative carries its
  **own** `source` vocabulary `{OFI, review, manual}` — do **not** add an `improvement` member to
  `capa_source` (none is anticipated).
- **Decoupled from the MR close gate** (the S-mr-3 F3 posture, verbatim-reused): a spawned initiative does
  **not** block MR close — `output_blocks_close` reads only the `MR_ACTION` task state and never `spawned_*`,
  so **zero close-gate change**.

This closes the R45 named deferral and the S-mr-3 deferral (`spawned_initiative_id` "stays reserved-null —
there is nothing to wire it to"): there is now a table to point at.

---

## 8. UI surface (slice 3)

- A new **"Improvement › Continual Improvement"** register (the doc 02:157 IA home), in the Improvement
  nav group beside "Nonconformity & CAPA," built on the **register-triage primitives** from design-critique
  #5 (`useRowKeyboardNav`, search/sort/URL-state).
- **List**: a calm table — `identifier`, `title`, `stage` (StatusBadge), `source`, `owner`, opened/aging —
  progressively disclosed to a per-initiative drawer/page showing the **stage timeline** (the append-only
  `stage_event` trail), the linked source (deep-link to the OFI finding or MR output), and
  edit/transition affordances gated `usePermissions().can('improvement.manage')` (a read the caller lacks
  → a calm `forbidden` panel, never a crash).
- **Raise affordances**: a "Raise Initiative" button on an OFI/Observation finding detail and on an MR
  `IMPROVEMENT`/`ACTION` output (gated `improvement.manage`; submit-and-show; show-then-403 avoided via a
  `capabilities` flag on the serializer).
- **PDCA-ACT quadrant tile** (doc 13 §5.2(f): "Improvement pipeline, count by stage"): a calm count-by-stage
  pipeline + aging RAG band — **tables + RAG, NOT charts** (N6/N9; the S-mr-1 "restate every widget as a
  calm table" correction). A hand-rolled SVG only if a trend line is later deemed essential (S-obj-charts).
- **StatusBadge tones** (reconcile to the ONE app-wide convention — the S-statusbadge-2 cross-surface
  lesson; the orchestrator reconciles, fan-out agents can't see cross-surface tone): `Open` neutral/info ·
  `InProgress` info · `Completed` success ✓ · `Closed` success ✓ (or neutral "filed") · `Cancelled`
  neutral/✕ (NOT danger — a cancelled improvement is not an error). Final tone mapping is a §13 design
  confirm.
- Web-test traps to honour: MSW fixtures pinned to the real serializer via `satisfies`; `expect`/`it`
  imported from `"vitest"`; distinct `aria-label`s; identity check via `/me`.id.

---

## 9. Compliance checklist / ★ — explicitly NO wiring

Clause 10.3 is `is_mandatory_star=False`, so `compute_checklist` (which filters
`WHERE Clause.is_mandatory_star.is_(True)`) never scans it, and an own-table initiative — not a
`documented_information` row — earns no `current_effective_version_id` anyway. An initiative therefore
**cannot, and must never be made to, flip a ★ node.** The only ★ node in clause 10 is **10.2**, already
COVERED by the CAPA family. Optionally the initiative register may surface a clause-10.3 *informational*
traceability line in the **full clause-IA view** (dashboard-only, zero compliance-checklist effect). The
R44/R45 `kind=DOCUMENT` rationale is **not** copied here (category error — there is no ★ to flip).

---

## 10. D1–D4 / WORM / append-only implications

- **D1 (self-hosted, single-org):** every table carries its own `org_id` FK; org-scoped throughout; no
  phone-home. Codex cross-org/multi-tenant flags on this family are moot under D1 (the known
  false-positive) — but fix any non-tenant bug riding the same comment.
- **D2 (vault → mirror authority):** an own-table initiative is **not** a `documented_information` row,
  never enters the mirror, earns no Released version / controlled copy — purely operational PostgreSQL
  state. (This is also *why* it cannot flip a ★ node.) Nothing touches the vault→mirror flow.
- **D3 (architected for Part 11 / multi-standard; don't build Part 11, don't remove hooks):** the
  `stage_event.signed_event_id` reserved hook ships day-one but stays **NULL/unsigned** in v1.x; **no new
  `SignatureMeaning`** (R2 closed); a future signed effectiveness review reuses `meaning='approval'`/
  `'verify'`. No `framework_id`/M:N clause-mapping work (the family is structurally 10.3-bound).
- **D4 (stack fixed):** PostgreSQL tables + Alembic + FastAPI routes + React/Mantine register; dashboards
  are calm tables + RAG (no charting dependency).
- **Append-only (load-bearing):** `improvement_initiative_stage_event` MUST carry the pg_roles-guarded
  `GRANT SELECT,INSERT … REVOKE UPDATE,DELETE` block and have **no `updated_at`** — structural, not
  conventional. Any future signed stage uses the pre-generated-UUID + flush + two-INSERTs seam (never an
  UPDATE).
- **No record/WORM coupling:** because the headline is an own table (not a `kind=RECORD` subtype), there is
  **no disposition / WORM-destroy path and no blob** — the blob-row-iff-bytes / evidence-purge invariants
  are not engaged (another reason the own-table shape is cleaner than the RECORD subtype). **If** the §11
  benefit-review later attaches `evidence_for_link(...)` (Mode-C), its unlink path MUST load the parent
  initiative `FOR UPDATE` so the stage read serialises against a concurrent close (the
  `records.unlink_evidence` freeze precedent).
- **Append-only audit chain:** the four new event types append to the hash chain like any event; the spawn
  emits no `signature_event` (R43).

---

## 11. Slice breakdown

| Slice | Scope | Migration | Verify |
|---|---|---|---|
| **S-improvement-1** (backend core) | `0052` (2 enums from ORM `*_VALUES`, 2 tables + the REVOKE block, the partial-UNIQUE + `env.py` exclusion, `audit_object_type` + 4 `event_type` ADD VALUEs in an `autocommit_block`, the `improvement.*` keys + role grants + downgrade, model registration in `__init__.py`) · `domain/improvement/fsm.py` · `services/improvement` (create / transition / list / get / stage-events; `_improvement_scope`) · `api/improvement.py` · `openapi.yaml` · **register R46** | `0052` | `/check-migrations`, `/check-api` (ruff + mypy-strict + **targeted** units; integration + full-unit are **CI-only** on this Windows box), `/check-contracts`; `migration-reviewer` + `diff-critic` |
| **S-improvement-2** (spawn wiring) | `POST /findings/{id}/raise-initiative` (OBSERVATION/OFI only) + `POST /management-reviews/{id}/outputs/{oid}/raise-initiative` (1:N `source_link`, replay-before-gate, `Idempotency-Key`, `IntegrityError` re-lookup) · `MGMT_REVIEW_INITIATIVE_SPAWNED` emit · `spawned_initiative_id` in the `_review_output` serializer + FE type + OpenAPI | **none** (all columns/enums in 0052) | as above + `diff-critic` |
| **S-improvement-3** (web) | the register + per-initiative drawer + stage timeline + raise affordances + the PDCA-ACT pipeline tile | none | `/check-web` (full eslint + strict tsc + build + vitest); `web-test-trap-reviewer` |
| **S-improvement-4** (DEFERRED, named — not built) | the optional unsigned **Verified** benefit-review stage (verdict frozen into the sealed `stage_event.payload`) and/or an engine-routed **management-authorization** approval (a new `WorkflowSubjectType`) | TBD | opt-in only |

**Other named deferrals (not faked):** discrete `improvement_initiative_action` milestone rows (the
`objective_plan` precedent — v1.x uses the stage trail + comments); objective-miss / 9.1.3 auto-seed;
list pagination beyond the standard page; a clause-10.3 informational line in the full clause-IA view.

**Known limitation — authz/lock TOCTOU on a concurrent process reassignment (Codex P2, deferred to the
owner-assignment-binding track):** the `_manage` scope resolver reads `process_id` *unlocked* during
authz, then the service reloads under `FOR UPDATE`; a reassignment committed in that window could let an
A-manager mutate a now-B-scoped initiative without holding manage on B. **Not exploitable in v1** — the
seeded PROCESS grant rides the unbound `:assignment_process` placeholder (matches no process,
`processes.py:6`), so every real `improvement.manage` holder is SYSTEM-scoped (QMS Owner / SYSTEM
override) with authority over *all* processes, which a reassignment cannot escalate. It is the
codebase-wide `require(resolver)` → locked-service pattern (CAPA/DCR/objectives share it); the correct
fix (lock-during-authz or post-lock re-authorization) is a **cross-cutting hardening to land WITH the
owner-assignment binding**, when concrete PROCESS-scoped grants first make the race exploitable.

---

## 12. Testing posture

- **`domain/improvement/fsm.py`** is pure → exhaustive unit coverage with zero DB (allowed-targets,
  terminal states, every illegal edge 409s).
- **Integration (CI-only on Windows):** the lifecycle happy path + each FSM 409; the spawn idempotency
  (201 then 200-replay, distinct keys → distinct initiatives, concurrent-winner race); the **no-signature**
  assertion on every spawn/transition; the append-only REVOKE (an UPDATE/DELETE by the app role is denied);
  the authz gate (a caller without `improvement.manage` is a calm 403; row-filtering by PROCESS scope); the
  `finding_not_improvable` 422 for an NC; the MR close gate **unchanged** (an open spawned initiative does
  not block close). **Assertions must be delta-based / run-scoped** (the shared session DB — the S-ing-4
  lesson); self-provide every precondition (the S-drift-2 inverse lesson).
- **Review rhythm:** `migration-reviewer` after 0052, `diff-critic` on each branch diff before PR,
  `web-test-trap-reviewer` on slice 3. Codex review on each PR (Codex repeatedly catches P1/P2s the clean
  diff-critic + web-test-trap miss — budget a follow-up round).

---

## 13. Decisions for owner sign-off (before code)

> **RESOLVED (owner, 2026-06-15): "approved" — all four items accepted as proposed.** (1) 1:N
> `source_link_id`, `spawned_initiative_id` left reserved-null. (2) R46 keys + the proposed role bundles.
> (3) `Open → InProgress → Completed → Closed` (+ `Cancelled`), with the optional frozen benefit note on
> `Closed`. (4) Badge tones as proposed (`Cancelled` = neutral/✕, not danger).

1. **Spawn cardinality (F5).** Proceeding with **1:N one-way `source_link_id`** and **leaving
   `review_output.spawned_initiative_id` reserved-null** (not dropped). The alternative — **un-reserving
   `spawned_initiative_id` to a real FK** (a 1:1 latch, mirroring `spawned_capa_id` verbatim, honouring the
   original scaffolding intent and enforcing one-initiative-per-MR-output) — is the only place this spec
   overrides explicit reserved-seam intent. **Confirm 1:N, or flip to the latch.**
2. **Register entry R46 + role grants (§6).** Confirm the additive `improvement.read`/`improvement.manage`
   keys and the proposed role bundles (QMS Owner + Process Owner manage; + Internal Auditor read).
3. **Lifecycle terminal shape (§4).** Confirm `Open → InProgress → Completed → Closed` (+ `Cancelled`),
   with the `Closed` transition optionally capturing a frozen realized-benefit note. (Alternative: collapse
   `Completed`/`Closed` into a single terminal if the two-step file step feels redundant.)
4. **StatusBadge tone mapping (§8)** — a small design confirm, reconciled to the app-wide convention.

Everything else is settled by the §0 locked decisions + verified precedent. **No production code until
this spec + the plan are approved.**

---

## 14. Migration 0052 trap checklist (for the implementer)

- [ ] `CREATE TYPE` tuples sourced from ORM `IMPROVEMENT_STAGE_VALUES` / `IMPROVEMENT_SOURCE_VALUES` (0010 rule).
- [ ] `ALTER TYPE … ADD VALUE` (audit_object_type + 4 event_types) inside `op.get_context().autocommit_block()` (PG16 `UnsafeNewEnumValueUsage`).
- [ ] Explicit FK names < 63 chars; ORM mirrors each with a name-matching constraint; bare `ck`/`uq` tokens.
- [ ] pg_roles-guarded `GRANT SELECT,INSERT; REVOKE UPDATE,DELETE` DO block on `improvement_initiative_stage_event`; **no `updated_at`** on it.
- [ ] Partial-UNIQUE `uq_improvement_initiative_spawn` excluded in `env.py._include_object`.
- [ ] Both model modules imported in `db/models/__init__.py` + `__all__` (0027 phantom-DROP).
- [ ] Downgrade NOT-EXISTS-guards seed-deletes against RESTRICT children; deletes `permission_override → role_grant → permission`; drops the two tables + two enums; `ALTER TYPE` is a no-op downgrade.
- [ ] Round-trip `alembic up ↔ down ↔ check` clean on throwaway PG16; `test_authz.py` count 100 → 102.
