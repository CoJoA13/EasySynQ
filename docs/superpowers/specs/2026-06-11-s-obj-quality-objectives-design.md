# S-obj — Quality Objectives (ISO 9001 clause 6.2) — family design

- **Date:** 2026-06-11
- **Family:** `S-obj-1` (backend engine, this spec) → `S-obj-2` (trailing front-end-only web UI; gets its own spec/plan)
- **Status:** owner-approved design (brainstorm 2026-06-11)
- **Unblocks:** the **PDCA Home dashboard** (deferred "until acks/objectives land" — acks shipped S-ack-1/2; objectives are the *last* blocker). The dashboard remains its **own later slice** — it consumes objectives **and** acks **and** audit/CAPA/checklist; tiles must read real data, never faked.
- **Doc grounding:** R3 (objectives are maintained Documents) · R25 (singleton Quality Policy/Scope Statement) · R2 (signature-meaning enum closed) · R38/R5 (additive-only catalog — **not opened here**) · R39 (reuse `object_type='document'`, no new `audit_object_type`) · N6 (no SPC analytics) · N9 (RAG against a rule, never an auto-compliance verdict) · docs `02 §6.2/§9`, `06 §KPI_READING`, `07 §3.7`, `10 §7/§9.3`, `13 §5.2 PDCA + Objectives scorecard`, `14 §6 (quality_objective/objective_plan/kpi_measurement)/§5.5 (form_template subtype)/§15.3 (tenancy)`, `16 §roadmap`. As-built anchors: `0004_seed_authz.py:95-100` (objective./kpi./register. keys), `0006_seed_vault.py:29-34` (document_type seed incl. `POL`), `documented_information.py:47-83` (R25 partial-unique index + `document_type_id`), `form_template.py` (the kind=DOCUMENT subtype precedent), `0048_acknowledgements.py` (the new-family migration recipe), `api/capa.py:260-301` (`_capa_scope` PROCESS-resolver precedent).

> **The thesis.** A Quality Objective *inverts* the versioned-document-vs-live-metric tension: the **commitment** (statement, target, unit, direction, due, baseline, policy link) is a **controlled document** — versioned, approved, clause-mapped, on the 7-state lifecycle — while the **measurement** (`current_value` + the reading log) is **operational evidence** that never spawns a controlled revision. The objective row is a shared-PK subtype of `documented_information`; the readings are append-only `KPI_READING` records that roll up into a mutable `current_value`; on/off-target is computed at read.

---

## s0 · Owner decisions (this session, 2026-06-11)

1. **Entity shape — controlled-document subtype (R3).** `quality_objective` is a shared-PK subtype of `documented_information` (`kind=DOCUMENT`, `document_type='Quality Objective'`), exactly like `form_template` (doc 14 §5.5). It inherits the 7-state lifecycle, versioning, approval/SoD, `clause_mapping`, `process_link`, search, the audit trail, **and** the S-drift-1 periodic-review clock — for free, and genuinely satisfies the **6.2 ★** compliance-checklist node (flips it COVERED).
   - *Grounding:* R3 (doc 14 §6: "registers/objectives are maintained Documents whose rows version together; the tables are their row/satellite stores"); the capa/audit_finding/complaint/form_template shared-PK precedent.
   - *Rejected:* (a) an **own-table register** (the R39 audit_program path) — lighter but loses Library/search/clause-map "for free", needs a bespoke lifecycle, and leaves 6.2-★ satisfaction undecided; (b) a **register-row middle ground** (rows under one parent register document) — least-precedented for an entity that is individually owned, due-dated, and dashboard-gauged.
   - *Register obligation:* reaffirm R3 for objectives in the new **R44** entry.

2. **Slice scope — the full 6.2/9.1 surface, in one slice.** `S-obj-1` ships: the **Quality Policy** link (R25 singleton, already seeded), `quality_objective`, `objective_plan` (the "…and planning to achieve them" half of 6.2), and the **append-only KPI-readings log** (`KPI_READING` records) with `current_value` rolled up from the latest reading. One migration (`0049`), one PR.
   - *Grounding:* doc 14 §6 (all three tables designed); doc 06 (KPI_READING is a Record); the owner's call to ship the complete clause-6.2+9.1 measurement story rather than a fast-follow.
   - *Rejected:* a lean engine (defer plans + KPI log) and a 1a/1b split — both viable, owner chose the complete engine in one slice for a single coherent review.

3. **Progress capture — `current_value` is a denormalized rollup from append-only KPI readings.** Each measurement is a `KPI_READING` record (WORM, retention, corrections-via-new-row) with `target_at_capture` frozen at capture; recording one rolls `current_value` up *latest-period-wins* onto the mutable satellite. The objective's `current_value` is a fast-read cache over the immutable log, never the sole source of truth.
   - *Grounding:* the repo's "mutable current scalar + append-only trail" house pattern (dcr.state/capa.close_state vs their event tables; documented_information.next_review_due over the review log). `target_at_capture` mirrors `document_version.metadata_snapshot`/`signature_event.content_digest` — a later target edit cannot retroactively rewrite a past reading's verdict.
   - *Rejected:* a directly-edited `current_value` with no evidence log (loses 9.1.1 monitoring evidence); a non-record standalone measurement table (re-implements retention/immutability/pinning the records engine already provides).

4. **On/off-target — direction-aware, amber-banded, computed at read.** Each objective declares a `direction` (`HIGHER_IS_BETTER` / `LOWER_IS_BETTER`) and an optional `at_risk_threshold` → green/amber/red. Computed by a pure rule (`domain/objectives/rules.py`), never stored.
   - *Grounding:* DP-2 calm RAG (doc 11); N9 ("against a rule, never an auto-compliance judgment"); N6 (descriptive, no SPC). "Reduce complaints by 20%" is a first-class lower-is-better case the simple `current≥target` boolean gets wrong.
   - *Rejected:* simple `current≥target` (wrong for the many lower-is-better objectives); direction-only with no amber (loses the dashboard's at-risk zone).

5. **Authz — ride the already-seeded keys; objectives carry a nullable `process_id`.** `objective.read`/`objective.manage` (PROCESS finest-scope) + `kpi.read`/`kpi.record` are **already in the closed-96 catalog** (`0004_seed_authz.py:95-100`) and role-granted. **No new permission key, no catalog-count change (stays 100), no R38 entry.** A new `_objective_scope` async resolver (the `_capa_scope` precedent) loads `process_id → ResourceContext(process_ids=…)` with a **SYSTEM fallback when null** (org-level objectives). `objective.manage` stays QMS-Owner-only.
   - *Grounding:* the "first real consumer of a long-dormant seeded key" precedent (S-rec-4 lit up `retention.*`); doc 07 §3.7; pdp.py PROCESS-vs-SYSTEM matching.
   - *Rejected:* minting new finer-grained keys (none needed — read/manage + kpi cover it); org-level-only scoping (loses the per-process PDCA PLAN quadrant); granting `objective.manage` to Process Owners now (→ deferred, additive role grant).

6. **Quality Policy — built via the already-seeded singleton.** `POL`/`Quality Policy` (`L1_POLICY`, `is_singleton=True`) is **already seeded** and R25 is **already enforced** by the base-table partial-unique index (`uq_doc_info_singleton_effective` WHERE `current_state='Effective' AND is_singleton`). So "build it now" = author a Quality Policy through the *existing* document flow + validate `objective.policy_id` against the **current Effective** policy at create + surface "N objectives consistent with this policy".
   - *Grounding:* `documented_information.py:51-62`; `0006_seed_vault.py:30`; doc 02 §502 ("consistent with the Quality Policy — a validation hint"); R25.
   - *Rejected:* dropping `policy_id` for v1 (diverges from doc 14:350); a separate `quality_policy` satellite table (YAGNI — no policy-specific columns in the spec; the policy *is* a documented_information row).

**Accepted defaults (no pushback):** sequencing `S-obj-1` backend → `S-obj-2` UI → PDCA dashboard (separate slice); objective/policy authoring reuses `create_document`/checkin/release (no new lifecycle); attainment (`met`/`missed`) is computed, not a stored status; the new register entry is **R44**.

---

## s1 · What the canon already pins (settled — restated, not re-decided)

- **Clause 6.2** is seeded (`iso9001_clauses.py:175-182`): id `6.2`, "Quality objectives and planning to achieve them", parent `6`, `is_mandatory_star=True`, `pdca_phase=PLAN`, leaf (no 6.2.x children). Objectives map to this single node.
- **Permission keys** `objective.read`/`objective.manage`/`kpi.read`/`kpi.record`/`register.read`/`register.manage` are seeded at PROCESS finest-scope (`0004_seed_authz.py:95-100`); QMS Owner holds objective.read+manage+kpi.* (SYSTEM scope_template), Process Owner holds objective.read+kpi.record, Internal Auditor holds objective.read. **The catalog is closed at 100 and we do not open it.**
- **R25 singleton** is enforced structurally (the partial-unique index); `POL` document_type seeded. **No new SignatureMeaning** (R2 enum closed) — objective/policy release emits `meaning=release` like any document. **Reuse `object_type='document'`** (R39 — no new `audit_object_type`).
- **`document_type` seed shape** = `(code, name, document_level, is_singleton)` via `pg_insert(...).on_conflict_do_nothing(["org_id","code"])` (`0006_seed_vault.py:63-84`); `{code}` drives the `{TYPE}` identifier token.
- **`RecordType.KPI_READING`** already exists (`_record_enums.py`); KPI readings are Records (doc 06 §70: objective_id/period/value/target_at_capture/unit/source).
- **The compliance checklist** already lists 6.2 (`mapped_count`/`effective_count`/`status`); once an Effective objective maps to 6.2, the node resolves against real data — likely **no checklist code change** (confirm the clause_mapping join auto-picks it up).

---

## s2 · Data model — migration `0049` (down_revision `0048_acknowledgements`)

New enum module `db/models/_objective_enums.py`: `ObjectiveDirection {HIGHER_IS_BETTER, LOWER_IS_BETTER}` with `OBJECTIVE_DIRECTION_VALUES = tuple(_vals(...))` (the 0010 rule — the migration sources its CREATE TYPE tuple from here). RAG is **computed**, never a stored enum.

**`quality_objective`** — shared-PK subtype of `documented_information` (the `form_template` pattern):
| column | type | notes |
|---|---|---|
| `id` | UUID PK | `→ documented_information.id` ondelete RESTRICT, `primary_key=True`, **no uuid default** (id IS the base row's id) |
| `org_id` | UUID NOT NULL | `→ organization.id` RESTRICT (every-table tenancy, §15.3) |
| `target_value` | Numeric NOT NULL | measurable-by-construction |
| `unit` | Text NOT NULL | |
| `baseline_value` | Numeric NULL | for % toward target |
| `current_value` | Numeric NULL | **mutable rollup** — latest KPI reading; NULL until first reading |
| `direction` | `objective_direction` NOT NULL | RAG comparator |
| `at_risk_threshold` | Numeric NULL | amber band boundary; NULL → green/red only |
| `due_date` | Date NOT NULL | |
| `process_id` | UUID NULL | `→ process.id` RESTRICT — PROCESS scoping; NULL = org-level (SYSTEM scope) |
| `policy_id` | UUID NULL | `→ documented_information.id` RESTRICT — the consistent-with Quality Policy |
| `created_at`/`updated_at` | timestamptz | mutable-entity convention |

**Owner** is the **base** `documented_information.owner_user_id` (already NOT NULL, line 85 — set by `create_document`), **not** a duplicated satellite column (doc 14:350 pre-dates the base owner; we reconcile to the base). The **commitment** fields (target/unit/baseline/direction/at_risk_threshold/due/policy) are the editable working copy, **frozen into `document_version.metadata_snapshot` at check-in** (form_template precedent). `current_value` is operational and **never** snapshotted.

**`objective_plan`** — plain mutable satellite (the "…planning to achieve them" rows):
`id` UUID PK · `org_id` FK RESTRICT · `objective_id` FK `→ quality_objective.id` RESTRICT · `action` Text NOT NULL · `resource` Text NULL · `responsible_user_id` FK `→ app_user.id` NULL · `due_date` Date NULL · `created_at`/`updated_at`.

**`kpi_measurement`** — the append-only time-series projection of a `KPI_READING` record (doc 14:365 shape):
`id` UUID PK · `org_id` FK RESTRICT · `record_id` FK `→ record.id` RESTRICT (the WORM evidence row) · `objective_id` FK `→ quality_objective.id` NULL · `process_id` FK `→ process.id` NULL · `period` Date NOT NULL (reading as-of) · `value` Numeric NOT NULL · `target_at_capture` Numeric NOT NULL (frozen) · `unit` Text NOT NULL · `source` Text NULL · `created_at`. **`REVOKE UPDATE, DELETE`** from the non-owner `easysynq_app` role (the capa_stage/acknowledgement house style) — no `updated_at`; corrections via a new record (record `correction_of`).

**`document_type` seed:** add `('OBJ', 'Quality Objective', <document_level>, False)`. *Plan detail:* the `document_level` for an objective — default `L1_POLICY` (6.2 sits at the planning apex) vs a dedicated level; settle in the plan, default `L1_POLICY`.

**Build hygiene (load-bearing):** register `QualityObjective`, `ObjectivePlan`, `KpiMeasurement` in `db/models/__init__.py` + `__all__` (the 0027 phantom-DROP trap); name every FK explicitly under 63 chars (`fk_quality_objective_process_id`, …); mirror migration constraint **names** in the ORM; source the enum tuple from the ORM; round-trip up↔down↔`alembic check` on PG16. New `event_type` values (s7) added via `ALTER TYPE … ADD VALUE` in an `autocommit_block()` **iff** seeded same-migration. Resilient org lookup `scalar_one_or_none('DEFAULT')` + single-org fallback (the 0045 trap). Downgrade in strict reverse, RESTRICT-FK-aware.

---

## s3 · The RAG / attainment rule (`domain/objectives/rules.py`, pure, no I/O)

`rag_status` / `pct_toward_target` inputs: `current_value`, `target_value`, `baseline_value`, `direction`, `at_risk_threshold`. `attainment` inputs: `current_value`, `target_value`, `direction`, `due_date`, `today`.

- **`pct_toward_target`** = `(current − baseline) / (target − baseline)` (direction-normalized; `None` when current is NULL or denominator is 0).
- **RAG (`green`/`amber`/`red`):**
  - `HIGHER_IS_BETTER`: green `current ≥ target` · amber `at_risk_threshold ≤ current < target` · red `current < at_risk_threshold` (no threshold → amber collapses; non-green is red).
  - `LOWER_IS_BETTER`: green `current ≤ target` · amber `target < current ≤ at_risk_threshold` · red `current > at_risk_threshold`.
  - `current_value IS NULL` → **`unmeasured`** (no reading yet) — distinct from red.
- **`attainment`** (`in_progress` / `met` / `missed`) = `in_progress` before `due_date`; at/after, `met` iff the target is reached, else `missed` (a never-measured objective is `missed` at due). Computed, not stored.
- No SPC/trend/forecast (N6). The rule is total and deterministic; unit-tested at every boundary.

---

## s4 · Lifecycle & the measurement flow

1. **Author a Quality Policy** (if none Effective) — existing document flow, `document_type='Quality Policy'`, `is_singleton=True`; R25 index rejects a second Effective.
2. **Create an objective** (`objective.manage`) — **measurable-by-construction gate**: `target_value`, `unit`, `direction`, `due_date` required on the satellite + `owner` (the base `documented_information.owner_user_id`, always set by `create_document`) (422 otherwise — an un-measurable objective would break the gauge and the 9.3.2 input). Validate `policy_id` is the current Effective Quality Policy (or null). Create the `documented_information` (`kind=DOCUMENT`, type `OBJ`) + the `quality_objective` satellite in one txn via the existing document machinery; **auto-create a `clause_mapping` to the 6.2 leaf** so the ★ checklist resolves on release.
3. **Version / release** — the standard lifecycle (Draft→InReview→Approved→Effective…); the commitment freezes into `metadata_snapshot` at check-in; release emits `meaning=release`. Revising the *commitment* (new target) is a new version; updating the *metric* is not.
4. **Record a measurement** (`kpi.record`) — create an **ad-hoc** `KPI_READING` record (via `capture_record`, **no `source_document_id`** — the `capture_complaint` precedent; this avoids the R21 version-pin trap, since a Draft objective has no version to pin, while keeping the reading WORM/retention-governed evidence) + the `kpi_measurement` projection (`target_at_capture` = the objective's **current `target_value`**, frozen at capture); roll `current_value` up *latest-period-wins* under `SELECT … FOR UPDATE` + `.execution_options(populate_existing=True)` (the S-drift-1 stale-identity-map trap — the authz resolver already `session.get`-loaded the row); audit `OBJECTIVE_MEASUREMENT_RECORDED`; one transaction. Measuring does **not** require the objective to be Effective in v1.
5. **Read** — `GET /objectives` and `/scorecard` compute RAG/attainment per objective from one serializer; the future PDCA PLAN gauge and CHECK "objectives scorecard" reuse it.

---

## s5 · API surface (`api/objectives.py`, new router; documented in `openapi.yaml` in-PR)

| Method · path | Gate | Notes |
|---|---|---|
| `POST /objectives` | `objective.manage` | measurable-by-construction gate; auto-maps to 6.2 |
| `GET /objectives` (`?process_id=`) | `objective.read` | list + computed RAG/attainment; row-filtered by scope |
| `GET /objectives/{id}` | `objective.read` | detail incl. plans + latest measurement |
| `POST /objectives/{id}/measurements` | `kpi.record` | append a reading, roll up `current_value` |
| `GET /objectives/{id}/measurements` | `kpi.read` | the reading history (time-series) |
| `POST/PATCH/DELETE /objectives/{id}/plans[/{plan_id}]` | `objective.manage` | objective_plan CRUD |
| `GET /objectives/scorecard` (`?process_id=`) | `objective.read` | org/process rollup `{on_target, total, by_rag}` + per-objective rows — **the one serializer the PDCA tile reuses** |

Quality Policy rides the **existing** document-authoring endpoints (it *is* a document) — no new policy route; add a small "current Effective Quality Policy" read for the objective create form. Serializers are private `_objective(row)`/`_measurement(row)`/`_scorecard(...)` helpers (stringify UUIDs, `.value` enums, `.isoformat()` dates) — MSW fixtures in `S-obj-2` pin to **these** shapes (`satisfies <Type>`), never a guess.

---

## s6 · Authz

- **Keys:** ride `objective.read`/`objective.manage` + `kpi.read`/`kpi.record` (no new keys). `objective_plan` rides objective.read/manage; measurements ride kpi.read/record. Quality Policy authoring rides `document.*`.
- **Resolver:** `_objective_scope(objective_id)` (async, the `api/capa.py:260-274` precedent) loads `quality_objective.process_id` → `ResourceContext(process_ids=frozenset({str(process_id)}))`, **SYSTEM fallback when `process_id IS NULL`**. PROCESS-scoped grants then match only in-scope objectives; the QMS Owner's SYSTEM scope_template matches all; a SYSTEM admin override is the live-smoke path (demo holds no content keys).
- **Read filtering:** a caller lacking `objective.read` at a scope sees the row **filtered**, not a crash; populate the FULL `ResourceContext` (process_ids) per the R28/S-pack-1 row-filter lesson.
- `objective.manage` stays QMS-Owner-only (Process-Owner manage → s10 deferral).

---

## s7 · Audit & events

- **Lifecycle** (create/release/supersede of objective + policy) reuses the existing `DOCUMENT_*` events (kind=DOCUMENT) — `object_type='document'`, `scope_ref=identifier`.
- **New additive `event_type` values** (`_audit_enums.py` + `ALTER TYPE` in `0049`): `OBJECTIVE_MEASUREMENT_RECORDED`, `OBJECTIVE_PLAN_ADDED`, `OBJECTIVE_PLAN_REMOVED`. All `object_type='document'`, `scope_ref=identifier` (R39 — **no new `audit_object_type`**). KPI readings also emit the standard record-capture event from `services/records`.
- **No signature_event** for measurements/plans (not signing acts); objective/policy *release* emits `meaning=release` via the existing lifecycle. No new SignatureMeaning (R2 closed).

---

## s8 · Testing (Linux-CI is the real run on the owner's Windows box)

- **Unit** (`tests/unit/`, `pytest.mark.unit`): the RAG rule (both directions, amber boundaries, NULL→unmeasured, % toward target, 0-denominator), the rollup (latest-period-wins, target_at_capture freeze), the measurable-by-construction gate, policy-consistency validation, and (if a Beat sweep lands) `test_objectives_task_registration.py`.
- **Integration** (`tests/integration/test_quality_objectives.py`, testcontainers): the full loop — author policy → create objective (gate 422 on missing target) → release → record measurements → `current_value` rolls up → RAG green→amber→red transitions → `/scorecard` rollup; the **R25 singleton** (second Effective policy rejected); the **authz matrix** (`objective.read/manage`, `kpi.record` at PROCESS vs SYSTEM, the `_objective_scope` resolver, 403/filtered); a measurement recorded on a Draft objective (succeeds — no Effective requirement in v1); **`alembic check` clean**; the catalog count **stays 100** (no key added — assert unchanged). Assertions delta-based / run-scoped; self-provide every precondition; include `app_under_test` even for service-level tests.

---

## s9 · `S-obj-2` (trailing, front-end-only — its own spec/plan)

No migration/key/endpoint/contract change. A `/objectives` register surface (PLAN, Mara-owned): a list with the RAG gauge + attainment, an objective detail page (commitment + plans + the measurement time-series), a measurable-by-construction create form (validates against the current Effective policy), a record-measurement action, and the `/scorecard` rollup. Replaces the `HomePage.tsx` placeholder's PLAN slot intent only when the **PDCA dashboard slice** lands (separate). TDD per task, MSW fixtures pinned to the s5 serializers.

---

## s10 · Deferred — named, not faked

- **The PDCA Home dashboard** → its own later slice (objectives unblock it; tiles read real objectives + acks + audit/CAPA/checklist — never faked).
- **Objective/KPI trend views & sparklines** → v1.x (docs/16:192); N6 keeps v1 descriptive (no SPC/forecast).
- **KPI readings pinned to an objective document-version** (require the objective Effective + an R21 version pin so a reading records "measured against v2.0") → v1.x. v1 captures ad-hoc `KPI_READING` evidence with `target_at_capture` frozen on the projection; the per-version pin is the tightening.
- **Management Review (9.3) inbound loop** (`review_output` → objective changes; mgmtReview.* keys seeded) → the deferred Mgmt-Review family. We build **no producer** and no consumer seam beyond the objective existing.
- **Process-Owner `objective.manage`** → an additive role grant when owner-assignment binding lands (read already works for Process Owners). Recorded so that track inherits it.
- **R35 content-domain-list extension** (so a QMS Owner can *self-grant* `objective.manage` via `permission.grant`) → optional later register tweak; **not blocking** (role assignment already grants it). objective.* is absent from R35's enumerated content-domain list today.
- **A bespoke objective-review obligation** → **not needed**: objectives are documents, so periodic-review reminders come free from the S-drift-1 clock (`review_period_months`/`next_review_due`). Nothing to build.
- **`mgmtReview.record_outputs` SoD** (seeded `sod_sensitive=True`, no engine path) → aspirational/Part-11-reserved, unchanged here.

---

## s11 · Register entry **R44** + back-propagation (write with the `S-obj-1` PR)

**R44 — Quality Objectives family (clause 6.2).** A Quality Objective is a **maintained Document** (kind=DOCUMENT shared-PK subtype of `documented_information`, type `OBJ`) per R3 — its commitment versioned/approved, its `current_value` a **mutable rollup over append-only `KPI_READING` records** (`target_at_capture` frozen). On/off-target is **direction-aware + amber-banded, computed at read** (N9), never stored (N6). The **Quality Policy** is the R25 singleton (already seeded `POL`); `objective.policy_id` records the consistency link (validation hint, doc 02:502). The family **rides the already-seeded** `objective.*`/`kpi.*` keys (PROCESS scope, `_objective_scope` resolver, SYSTEM fallback) — **no new permission key, catalog stays 100, no R38 change**. No new SignatureMeaning (R2) and no new `audit_object_type` (R39); additive `OBJECTIVE_*` event types only.

**Back-propagation** (edit on the PR): `02` (6.2 entity as-built), `04` (objective in the doc hierarchy/level), `06` (KPI_READING ↔ kpi_measurement wiring), `07` (objective/kpi keys now reach a resource), `10` (objectives feed 9.3.2; loop seam noted-not-built), `13` (Objectives scorecard backed by real `quality_objective`+`kpi_measurement`), `14` (as-built `quality_objective`/`objective_plan`/`kpi_measurement` incl. `direction`/`at_risk_threshold` additions vs the spec), `15` (the new `/objectives*` endpoints + scorecard), `16` (objectives shipped; PDCA dashboard now buildable), `18` (slice ledger). Plus the `docs/slice-history.md` narrative entry + a capped `CLAUDE.md` Recent-learnings line on merge.
