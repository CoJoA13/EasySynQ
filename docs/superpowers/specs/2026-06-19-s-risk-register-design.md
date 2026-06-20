# S-risk — Risk & Opportunity register (ISO 9001 clause 6.1) — family design (spec)

> The **first register family** in EasySynQ (clause 6.1 Risks & Opportunities). It is greenfield in
> code (`grep` confirms **no** `risk_opportunity` model/migration/key exists), though the entity is
> fully pre-specced in `docs/14 §9` + R18. This slice also **sets the register pattern** that the later
> Context (4.1) and Interested Parties (4.2) registers reuse — so it is designed cleanly, then scoped to
> risk only. SPEC-FIRST per CLAUDE.md; the architectural forks were the owner's calls (§0). The design
> was **adversarially validated by a 5-lens refute panel (§11)** that found and drove the fix of a real
> WORM-safety flaw (a freely-editable working satellite) and a real re-grade flaw (live-code band
> grading) **before any migration**.

## 0 · Owner decisions (RESOLVED — ratified 2026-06-19 via AskUserQuestion ×2)

- **D-1 — Register-as-Document.** **Ratified.** The clause 6.1 register is **one controlled
  `documented_information` (`kind=DOCUMENT`, a new `document_type` code `RSK`, `is_singleton`)** holding
  many `risk_opportunity` **satellite rows** that version together under a **lightweight approval
  profile** (`docs/04 §4.4`), riding the generic 7-state vault FSM. *Rejected:* an own-table workflow
  object (the `improvement_initiative`/R46 shape) — see §1 for why non-★ clause 6.1 still goes
  Document-backed.
- **D-2 — 5×5 matrix, stored + frozen.** **Ratified.** `scoring_method` enum (sole v1 value
  `5x5_matrix`); `likelihood`,`severity` ∈ 1..5; `risk_rating = likelihood × severity` ∈ 1..25 **stored
  numeric**, derived by a pure rule; a 4-band RAG over the numeric; the scoring **criteria frozen in the
  version snapshot** so a methodology change cannot re-grade history **or the live register** (§4 — the
  panel's L2 fix).
- **D-3 — Ride the seeded `register.*` keys.** **Ratified.** The risk **rows** gate on the
  already-seeded `register.read` / `register.manage` (`0004:97-98`, PROCESS finest-scope) — **no new key,
  catalog stays 102, no R38 catalog change**. The register **Document** rides `document.*`. One
  **R38-additive *grant*** of `register.manage` → Process Owner (§5) lets owners maintain risks in their
  own process. *Reconcile* `docs/15 §8.10b`'s aspirational `risk.*` gates → `register.*` (§8).
- **D-4 — Risk-row control model: strict controlled-document.** **Ratified (2nd question).** The risk
  rows **are the register version's controlled content**, edited **through FSM revisions** (T7
  `start_revision` → edit the satellite while Draft/UnderRevision → publish/release supersedes), exactly
  like the `form_template`/objectives working-copy. The satellite is **read-only while Effective** and
  equals the published snapshot; **live reads resolve against the governing Effective version** (§3). This
  is the WORM-pure realization of D-1's "rows version together." *The earlier "freely-editable working
  satellite while Effective" framing is **withdrawn** — the panel proved it WORM-unsafe (L3, §11).*
  **Consequence (owner-accepted):** risk edits are **batched into register revisions, one revision at a
  time**, stewarded at the head's (org) scope — a process owner contributes rows within an open revision
  window, not unilaterally anytime. *Rejected alternative:* operational rows + per-change audit +
  versioned criteria (objectives `current_value` style) — lighter multi-owner cadence, not chosen.

## 1 · What the canon already pins (settled — restated, not re-decided)

- **The entity is fully specced.** `docs/14 §9:348` — `risk_opportunity`: `id` PK, `register_doc_id` FK,
  `type` enum(`risk`,`opportunity`), `description`, `process_id` null, `clause_id` null, `likelihood`,
  `severity`, `risk_rating` (derived/stored), `scoring_method`, `treatment`, `effectiveness`,
  `linked_capa_id` null, `row_version`. ERD edges (`14:334/337/338`): `DOCUMENTED_INFORMATION ||--o{
  RISK_OPPORTUNITY` ("register row", **1:many**), `}o--o| CAPA` ("treated by"), `}o--o| PROCESS`
  ("scoped to").
- **R18** fixes the scoring **field names**, the entity, and the routing key (`subject.risk_rating`);
  back-prop targets 02/10/13/14.
- **R3 numbering collision (must not be propagated).** `docs/14:328` + the §0 gap-audit finding row
  `14:582` ("**R3 — Registers… Both, layered**") cite "R3" for registers-as-Documents, but the
  **published** R3 (`decisions-register.md:94`) is *Authorization precedence (deny-wins)*. The
  registers-as-Documents doctrine lives as the doc-14 finding-R3 + `docs/04 A3:33`, **not** a numbered
  resolution. The new **R49** (§13) restates the doctrine in its own text and cites `14 §0/§6` + `04 A3`.
- **Clause 6.1 is non-★** (`is_mandatory_star=False`, `iso9001_clauses.py:170`), PLAN phase. **Why
  Document-backed anyway** (the rationale a reviewer/Codex will demand): the fork is **register vs
  progressing-activity**, not ★-vs-non-★. ★ *forces* Document for a mandatory-DI clause (6.2/9.3 flip a
  checklist node); a **register** — a maintained controlled list — is bound to the Document lifecycle by
  `docs/04 A3` + the finding-R3 ("Both, layered") **regardless of ★**. `improvement_initiative` (10.3,
  also non-★) went own-table because it is a *progressing activity*, **not** a maintained register.

## 2 · The register-as-Document model + the edit model (D-4)

- **The head — one per org.** A `documented_information` with `kind=DOCUMENT`, `document_type` code
  **`RSK`** (new, seeded like `OBJ`), **`is_singleton=True`** (one Effective register at a time — the
  R25/`POL` posture). It carries **no** risk columns; the rows live in the satellite. Its only mapping is
  a **clause-6.1 `ClauseMapping`** (the objectives→6.2 precedent).
- **⚠ Invariant — the head carries ZERO `ProcessLink`s (L1-MAJOR).** Created via `create_document(...,
  processes=())`; a risk row's `process_id` lives on the **satellite** and is **NEVER** propagated to the
  head as a `ProcessLink`. *Why load-bearing:* `_document_scope_by_id` (`api/documents.py:365-385`)
  populates a doc's `process_ids` from its `ProcessLink`s; if the head ever acquired one, a bound
  Process-Owner's PROCESS-scoped `document.*` grant would match the org head and let them
  checkout/edit/submit/release the whole register. An unlinked head resolves to `process_ids=∅` →
  PROCESS-grant deny. Regression-tested (§10).
- **⚠ Single non-Obsolete head (L3-MAJOR).** `uq_doc_info_singleton_effective`
  (`documented_information.py:54-62`) blocks only two **Effective** heads — a 2nd **Draft** head is not
  blocked. The get-or-create resolver MUST target **"the single non-Obsolete `RSK` head for the org
  (Effective OR its Draft/UnderRevision successor)"**, and **publish runs T7 `start_revision` on the
  existing Effective head** to mint the Draft successor — **never** a 2nd head. Add a partial-unique guard
  (one non-Obsolete `RSK` head per org) + an integration test that a 2nd `risk.create` after the first
  publish attaches to the **same** head.
- **Head lifecycle authority (v1 detail).** Creating/revising/publishing the org-wide head is a
  **SYSTEM-scoped `document.*`** act (the head has no process). No seeded role holds SYSTEM
  `document.create`/`edit`, so in v1 head stewardship **rides a SYSTEM override** (the objectives
  "seed-then-ride SYSTEM override until the role/UI lands" precedent) — a register-steward role/UI is a
  named follow-up. The risk **rows** gate on `register.*` independently (§5).
- **The edit model (D-4 — the form_template/objectives precedent, NOT "free edit").** Satellite rows are
  editable **only when the head is Draft or UnderRevision** (the `services/vault/service.py:551-557`
  `set_working_schema` / `api/objectives.py:565-571` "409 unless Draft/UnderRevision" gate). To change
  risks on an **Effective** register: **T7 `start_revision`** (head Effective→UnderRevision, acquires the
  edit lock) → edit rows (`register.manage`, gated on head state, `row_version` optimistic concurrency) →
  **publish** (release supersedes, head→Effective, satellite frozen again). The **first-ever** register is
  authored Draft → released to first Effective. "Rows version together" = the working row-set snapshots
  **together** into one version at publish.

## 3 · Row content, the version snapshot, and the read-of-record

The S-rec-3 **structured-content-as-source-blob** pattern (`services/vault/service.py
checkin_objective_commitment` is the sibling — `finalize_worm` at `:747`, `rendition_blob_sha256` left
**NULL**, the mirror's controlled-copy-cache pointer never written):

- At **publish**, canonical-serialize the working row-set **and the scoring criteria** (RFC-8785/JCS, the
  `domain/objectives/commitment.py build_commitment` precedent) → the immutable `document_version`
  **source blob** + `document_version.metadata_snapshot.risk_register = { rows, criteria }`. Never branch
  the shared `_snapshot(doc)`. The blob is `application/json` → **non-renderable** (R26;
  `render_gotenberg._NON_RENDERABLE_PREFIXES` already covers it) → the mirror lands
  `no_controlled_rendition`, source-bytes-only.
- **Read-of-record (the WORM/D2-correct model — L3-CRITICAL fix).** When the head is **Effective**, the
  satellite **equals** the Effective version's snapshot (read-only), so live reads (register page, doc-13
  high-risk, MR input-e) read the satellite **== the controlled version** — **no D2 drift** (authority
  flows vault→mirror; the live read IS the governing version). When **UnderRevision**, controlled reads
  resolve against the **governing (prior Effective) version** snapshot (the
  `resolve_commitment(governing)` precedent, `api/objectives.py:181-195`); the working draft is shown only
  in the editor's working view. *This is the inverse of the withdrawn free-edit model, where the live
  satellite could diverge from the Effective version by an unbounded amount.*
- **blob-iff-bytes is preserved by grounding, not assertion (L3-MINOR).** `document_version` source blobs
  are **never byte-deleted** on this path (`lifecycle.obsolete` only state-flips `version_state`; the
  WORM-destroy/sweep purge is **records-only**), so a register accumulating one JSON source blob per
  publish keeps the invariant with **no purge wiring**.

## 4 · Scoring methodology & `risk_rating` derive-and-freeze (L2 fix)

- **The pure rule — `domain/risk/rules.py`** (I/O-free, mirrors `domain/objectives/rules.py`):
  `risk_rating(likelihood, severity, scoring_method) -> int` = `likelihood × severity` for `5x5_matrix`
  (∈ 1..25). `risk_band(risk_rating, criteria) -> RiskBand` is **total over 1..25**. Unit-tested against
  `docs/15 §8.10b` (`risk_rating: 20` from `4 × 5`).
- **`risk_rating` is STORED and ALWAYS re-derived on write (L2-MAJOR).** `POST /risks` **and every PATCH
  touching `likelihood`/`severity`/`scoring_method`** recompute `risk_rating` via the pure
  `risk_rating()` **in the same txn** — never accept a client-supplied rating. A service-level invariant
  test asserts `stored risk_rating == risk_rating(likelihood, severity, scoring_method)` on every write.
- **⚠ The BAND is graded against FROZEN criteria, never live code (L2-CRITICAL).** The live register band
  resolves against the **governing version's** `metadata_snapshot.risk_register.criteria`
  (`resolve_criteria(governing)` — the `resolve_commitment(governing)` switch, `api/objectives.py:186`),
  **not** the current code's `risk_band()` table. *Why:* the band is the graded verdict; grading the live
  satellite off code means an edit to the 5×5 band boundaries (e.g. Critical `20-25`→`16-25`) silently
  re-grades every stored row's band **on the live page** — the exact S-obj-freeze failure mode the
  objectives precedent avoids by resolving against the frozen snapshot. The criteria freeze in the version
  snapshot is therefore **load-bearing for the live read**, not decorative.
- **⚠ Code criteria are pinned by a GOLDEN TEST (L2-CRITICAL-2).** A code-level golden test pins the
  byte-shape of each `scoring_method`'s matrix + band-threshold table, so an **in-place** edit to
  `5x5_matrix` fails CI — forcing the **mint-a-new-`scoring_method`-value** path (the append-only enum
  doctrine; existing rows keep their value → their snapshot criteria → never silently re-graded). The
  "`scoring_method` is immutable by convention" claim alone is **unenforced** — the golden test is what
  makes it real.
- **`scoring_method` is write-once (L2-MINOR).** Rejected on PATCH; changing methodology is an explicit
  **re-score** action that recomputes `risk_rating` **and** emits a `RISK_RESCORED` audit event (additive
  `RISK_*` event type, §13) — auditable, never a silent in-place re-grade.
- **No per-row `*_at_capture` column** — *correct, for the right reason:* the live band resolves against
  the governing version's frozen criteria (above), so the version snapshot already pins the basis; a
  per-row `risk_band_at_capture` would duplicate it. (The earlier "scoring_method is the immutable key"
  justification was unenforced and is replaced by the governing-snapshot resolve + golden test.)
- **RAG bands (proposed — tweakable on the spec PR), reusing the objectives vocabulary + glyph canon:**

  | Band | `risk_rating` | Tone | Glyph | Label |
  |---|---|---|---|---|
  | Critical | 20–25 | `danger` | ✕ | "Critical" |
  | High | 12–16 | `danger` | ✕ | "High" |
  | Medium | 6–10 | `warning` | ◔ | "Medium" |
  | Low | 1–5 | `success` | ✓ | "Low" |
  | (unscored) | — | `neutral` | ○ | "Not yet measured" |

  Achievable 5×5 products are `{1,2,3,4,5,6,8,9,10,12,15,16,20,25}`; the band function is **total over
  1..25**. The doc-13 "high-risk" set = the **`danger`-tone** rows (High ∪ Critical). Sort by a numeric
  severity rank (`danger` 0 / `warning` 1 / `success` 2 / `neutral` 3 — the `RAG_SEVERITY` precedent),
  Critical above High by `risk_rating`. Status carried by **tone + glyph + label**, never colour alone
  (DP-5 / WCAG 2.2 AA).

## 5 · Authz — ride the seeded `register.*` (D-3)

- **Keys (no new key; catalog stays 102).** `register.read` / `register.manage` (`0004:97-98`,
  `is_system_domain=False`, `sod_sensitive=False`, `sig_hook=False`, `finest_scope=PROCESS`) gate the
  **rows**; the head lifecycle rides `document.*`. Today these reach no resource; this slice gives them
  one (the "seed-then-ride" precedent).
- **Role grants (today).** `register.read` → QMS Owner (`0004:178`) + Process Owner (`0004:220`) +
  Internal Auditor (`0004:255`); `register.manage` → QMS Owner only (`0004:185`).
- **⚠ The one additive grant (R38 grant, NOT a new key) — with the EXPLICIT correct template (L1-CRITICAL).**
  Migration `0058` grants `register.manage` to the **Process Owner** role with `scope_template =
  _PROCESS_SCOPE` = `{"level":"PROCESS","selector":{"process_id":":assignment_process"}}` — **NOT**
  `_SYSTEM_SCOPE`. *Why this matters and why the `clauseMap.read`/S-records-C grant is the WRONG precedent
  to copy:* a **SYSTEM** scope_template is **exempt from `bound_scope` clamping**
  (`services/authz/repository.py:53-58` `template_is_system` → used unclamped) and a SYSTEM grant **matches
  every resource** (`pdp.py:76`). `clauseMap.read` was correctly granted at SYSTEM **because its resource
  is org-level**; `register.manage`'s resource is a **per-process row**, so a SYSTEM template would let a
  bound owner manage **every** process's risks and org-level rows — defeating §6. The `_PROCESS_SCOPE`
  placeholder is required so the owner's `bound_scope` clamps it to owned processes (`repository.py:57`;
  the `0004:322` Process-Owner-bundle precedent). Catalog stays 102 (a new `role_grant`, not a key).
- **ADMIN gets nothing** (AZ-INV-6 — the register is a QMS act).
- **Coarseness accepted (D-3).** `register.manage` is one umbrella over create/re-score/treat and is
  shared with the future Context/IP registers — the deliberate shared-umbrella design `doc 07 §3.7`
  provisioned. Finer SoD is an additive R38 add if ever needed.

## 6 · Process-scope authz wiring (R48 own-id-only)

Risk rows carry `process_id`; `register.*` is PROCESS-scoped. **Cleaner than records** because the
binding is the row's **own** `process_id` column (set by an already-authorized write), not a leg-A/leg-B
graph — but the discipline ships **with** the write path (S-risk-1).

- **`GET /risks` LIST = filter-not-403.** `Depends(get_current_user)` + `gather_grants("register.read")`
  + per-row `authorize` over `ResourceContext(process_ids={row.process_id} if set else ∅)` — **no
  `artifact_id` (L1-MINOR):** stamping the shared head id on every row's context would let a (hypothetical)
  ARTIFACT-scoped `register.read` over the head read all rows; the row-level binding is purely the
  `process_id`. No-grant → `200` + empty; SYSTEM grant matches every row (byte-identical to today's QMS
  Owner / auditor). Thread `source_ip` (`request.client.host if request.client else None`).
- **`GET /risks/{id}` = scoped `require()` enforce** (403-on-deny).
- **Writes re-enforce the TARGET process.** `POST /risks` enforces `register.manage` over
  `body.process_id` (the `improvement.create`/`capa.create` body-scope precedent, confirmed sound by the
  panel). `PATCH` that **reassigns `process_id`** re-enforces over the **new** target (the
  `documents._enforce_target_process:1098-1116` / S-records-W escalation guard, confirmed sound). A
  `process_id`-null (org-level) row resolves to `ResourceContext.system()` → reachable/creatable only at a
  SYSTEM grant (panel-verified). All writes additionally require the head Draft/UnderRevision (§2) → `409`
  when Effective.
- **R48 own-id-only.** Scope by the row's **own** `process_id`; the PDP `_matches_scope` PROCESS branch
  (`pdp.py:81-83`) does **not** walk `parent_id`. `include_subprocesses` stays the named v1.x deferral.

## 7 · CAPA spawn & clause wiring

- **Risk → CAPA, one-click, idempotent — with the explicit LOCK (L4-MAJOR).** `risk_opportunity.linked_capa_id`
  is the latch, but R16's real idempotency guard is the **`FOR UPDATE` on the parent row held across
  check-then-spawn** (`capa/service.py:231` `get_complaint(..., for_update=True)`, latch checked
  `:235-242`) — **not** a UNIQUE on the latch (two spawns mint two distinct capa ids that never collide).
  So: `risk = await repo.get_risk(session, risk_id, for_update=True)` held across the check-then-spawn;
  return `(capa, created=False)` when `linked_capa_id` is already set and commit promptly to release the
  lock. A 2-session `asyncio.gather` race test proves a single CAPA results.
- **`CapaSource.risk` (additive enum).** `CapaSource` today is `audit/process/complaint/review_output`
  (`_capa_enums.py`, no `risk`). Add `risk` via `ALTER TYPE … ADD VALUE` (the 0010 pattern; no-op
  downgrade; source the tuple from the ORM `*_VALUES`; **the add-value must commit before any same-migration
  seed uses it** — the 0053 autocommit lesson). The spawn re-auths `capa.create` over the risk's
  `process_id` (panel-verified vs the complaint→CAPA `api/capa.py:579-581` precedent; does **not** re-open
  the records/CAPA escalation surface — the CAPA's `process_id` is the risk's own already-authorized
  column). **S-risk-3.**
- **No 2-table cycle, RESTRICT verified (L4-MINOR).** `linked_capa_id → capa.id` is one-way (`capa` has no
  risk back-ref → no `use_alter`). `ondelete=RESTRICT` gates **no** erasure path: capa rows are never
  hard-deleted (no `session.delete(Capa)` path; `capa.id→record.id` is itself RESTRICT) — unlike the
  blob-RESTRICT case (S-rec-2).
- **Clause mapping.** Head auto-maps to **clause 6.1** at create; a row's optional `clause_id` is a finer
  per-risk tag.

## 8 · Downstream consumers & back-propagation (staged with the slices)

- **MR input (e) "risks and opportunities"** (`services/mgmt_review/compile.py`): drop
  `RISK_OPPORTUNITY_ACTIONS` from `_SOURCELESS_GAPS` (`:76`) + a `_build_row` branch mirroring
  `OBJECTIVES_STATUS` (`:134`) summarizing the register (counts by band + `effectiveness`
  recorded-vs-pending). **S-risk-2.**
- **doc 13 high-risk dashboard / Home PLAN tile** — the `danger`-tone (High ∪ Critical) count. **S-risk-2/4.**
- **doc 10 workflow routing on `subject.risk_rating` — DEFERRED (named, not faked).** The routing
  *subject* is the **document**, but `risk_rating` lives on the **row**; no document→rating resolver
  exists. v1 `risk_rating` is a **stored/sortable/dashboard** field only.
- **Docs-only reconciles (land WITH S-risk-1, the "as the model lands" convention):** `docs/15 §8.10b`
  gate mapping — `risk.read → register.read`, **`risk.create` + `risk.update` → `register.manage`** (a
  deliberate 3→2 coarsening, losing the create-vs-update split; cross-ref §5/D-3 — coherence-MINOR);
  `docs/04 A3:33` + `§3.6:221` pointer `§4.5` → `§4.4`; `docs/14 §9:365` `kpi_measurement` stale row gains
  `direction_at_capture` + `at_risk_threshold_at_capture` (migration 0055 added them); R18 back-prop into
  02/10/13/14.

## 9 · Web SPA surface (S-risk-4, its own spec/plan)

- **`RiskRegisterPage`** mirrors `ObjectivesRegisterPage.tsx`: gated **New** on `register.manage` at the
  row's PROCESS scope (the `InitiativeAdvancePanel` precedent, not bare SYSTEM) **and** on the head being
  Draft/UnderRevision (an open revision); a scorecard band rollup; the register-triage toolbar
  (search/sort/keyboard/URL-state); `lib/states` primitives; a `forbidden` flag → calm no-access panel.
- **RAG legibility** via `StatusBadge` tone+glyph+label reusing `features/objectives/labels.ts` + the
  ✓◔✕○ glyph canon (§4). The **5×5 matrix** as **hand-rolled SVG** (D4 — the objectives band-zone SVG
  precedent): a 5×5 grid of rects coloured by each cell's band tone, per-cell glyph + `<title>`, the
  current row's cell highlighted.
- **`RiskDetailDrawer`** (`?risk=` URL param) for view/score/treat + the risk→CAPA spawn seam.
- **Home PLAN card** — mirror `PlanCard.tsx` via a new `useRisks` hook; a neutral high-risk `StatLine`;
  `allForbidden` from **actionable** reads only. **`/risks`** route + a **LeftRail PLAN entry**.
- **Test traps:** `import { expect, it } from "vitest"`; required Mantine field by placeholder; MSW
  fixtures `satisfies <Type>` to the real serializer; distinct `aria-label`s; no persistently-mounted modal.

## 10 · Slice plan

1. **S-risk-spec** (this doc + **R49** + the two self-range bumps) — **docs-only, no code**. §11 ran here;
   D-1..D-4 ratified. *(The doc-15/04/14/02/10/13 reconciles land with the slices they describe.)*
2. **S-risk-1 — BE register core (migration `0058`, down_revision `0057_process_owner_clausemap`).**
   `risk_opportunity` satellite model (imported in `db/models/__init__.py` + `__all__`); the `RSK`
   `document_type` seed + the single-non-Obsolete-head partial-unique guard; the `register.manage`→Process-Owner
   additive grant **with `_PROCESS_SCOPE`**; the pure `domain/risk/rules.py` + the **criteria golden test**;
   the head get-or-create + the FSM-revision edit gate (Draft/UnderRevision only) + the publish/check-in
   (S-rec-3) freezing rows + criteria into the snapshot; live grading via `resolve_criteria(governing)`;
   auto-map clause 6.1; `/risks` CRUD with the §6 filter-not-403 LIST (no `artifact_id`) + scoped GET +
   PATCH-reassign re-enforce + **re-derive-rating-on-write**; `openapi.yaml` in-PR. **Required tests:** the
   **Slice-W-style escalation test** (a PROCESS-only bound P1 owner — no SYSTEM override — is 403'd on
   POST/PATCH against a P2-bound row AND an org-level null-process row), the **head-no-ProcessLink** 403
   test, the **re-derive invariant** test. **migration-reviewer + diff-critic + @codex.** Write-path
   re-auth ships **with** the write.
3. **S-risk-2 — read consumers + MR input (e).** The `compile.py` input-e branch; the doc-13 high-risk
   read. (doc-10 routing stays deferred.)
4. **S-risk-3 — CAPA-spawn seam.** `CapaSource.risk` (additive enum) + `linked_capa_id` latch with the
   **`FOR UPDATE` lock + the 2-session race test** + the spawn endpoint.
5. **S-risk-4 — FE register + matrix + Home** (§9; its own spec/plan).

## 11 · 5-lens adversarial validation — RESULT (run 2026-06-19, before ratification)

A 5-lens refute panel returned **`needs_rework`**; the confirmed holes are folded above. Record:

- **L1 — authz escalation [found + fixed].** **CRITICAL:** the "grant `register.manage` mirroring
  `clauseMap.read`" guidance would mint an **unclamped SYSTEM** grant (`repository.py:53` SYSTEM-template
  exemption) → matches every process → **fixed** (§5: explicit `_PROCESS_SCOPE`, analogy removed).
  **MAJOR:** head must carry zero `ProcessLink`s → **fixed** (§2 invariant + test). **MINOR:** drop
  `artifact_id` from the LIST context → **fixed** (§6). *Dismissed (verified):* adding a key to a role's
  grant bundle is membership-neutral (no `users_with_roles`/`resolve_audience` leak); null-process rows are
  SYSTEM-only; PATCH-reassign re-auth is sound.
- **L2 — freeze / re-grade [found + fixed].** **CRITICAL ×2 + MAJOR:** grading the live satellite off
  current `risk_band()` code silently re-grades the live register on a band-threshold edit; the
  version-snapshot freeze was decorative → **fixed** (§4: live band resolves against the governing
  snapshot's criteria — `resolve_criteria(governing)`, making the freeze load-bearing — + a golden test
  pinning code criteria). **MAJOR:** PATCH must re-derive `risk_rating` → **fixed** (§4/§6). **MINOR:**
  `scoring_method` write-once + `RISK_RESCORED` audit → **fixed** (§4).
- **L3 — lifecycle / WORM / mirror-authority [found + resolved by D-4].** **CRITICAL ×2:** the
  "freely-editable working satellite while Effective" model has **no FSM state** (the generic FSM seals
  content at Effective; only `start_revision` unlocks) and made the live mutable satellite authoritative,
  inverting D2 with unbounded drift; "form_template precedent exactly" was **false** (form_template seals
  at Effective) → **resolved** (D-4 ratified strict controlled-document; §2 edit-via-revisions; §3
  governing-version reads). **MAJOR:** single-non-Obsolete-head → **fixed** (§2). **MINOR:** blob-iff-bytes
  grounded → **fixed** (§3).
- **L4 — convergence / CAPA-spawn [found + fixed].** **MAJOR:** the latch needs the `FOR UPDATE`
  parent-row lock, not a UNIQUE → **fixed** (§7 + race test). **MINOR:** RESTRICT confirmed safe → **fixed**
  (§7). *Dismissed (verified):* `CapaSource.risk` does not re-open the records/CAPA escalation surface; no
  2-table cycle.
- **L5 — canon-coherence [safe_with_caveats, fixed].** Two MINORs: the D-1/D-4 cross-reference and the §8
  3→2 `risk.*`→`register.*` mapping → **fixed** (D-4 now references D-1's "version together"; §8 spells the
  mapping). All ~16 confirmations (R3 collision, R49-is-next, both self-range labels, catalog stays 102,
  seed-line citations, is_singleton/R25, §4.5→§4.4, `CapaSource.risk` genuinely new, kpi staleness, head
  0057→0058) verified accurate.

## 12 · Rejected alternatives & named deferrals

- **Rejected — pure own-table register** (R46): loses the controlled-document audit trail `docs/04 A3`
  binds; R46's progressing-activity rationale doesn't hold for a register (§1).
- **Rejected — shared-PK subtype** (the `quality_objective` shape): a register has *many* rows; satellite
  (1:many) is the only fit (the sibling `context_issue`/`interested_party` shape).
- **Rejected — `risk_rating` computed-at-read** (objectives N9): doc 10/13/15 want a stored, sortable,
  routable column.
- **Rejected — open `risk.*` keys** (doc 15's aspiration): `register.*` is seeded for the three registers
  and sets the clean shared umbrella; dedicated keys would make `register.*` vestigial once Context/IP
  arrive (D-3).
- **Rejected — freely-editable working satellite while Effective** (my first draft): the panel proved it
  inverts D2 (the live satellite diverges from the controlled Effective version by an unbounded amount) and
  has no FSM state — withdrawn for the strict controlled-document model (D-4 / §11 L3).
- **Rejected — operational rows + per-change audit + versioned criteria** (the objectives `current_value`
  style, D-4's alternative option): a defensible lighter-cadence model, **not chosen** — the owner elected
  strict version-content control.
- **Rejected — per-row `*_at_capture` freeze column** (a literal S-obj-freeze copy): unnecessary — the live
  band resolves against the governing version's frozen criteria (§4), so the version snapshot already pins
  the basis.
- **Deferred (named, not faked):** doc-10 `subject.risk_rating` routing (resolver gap, §8);
  `include_subprocesses` (R48, §6); a register-steward role/UI for the org head lifecycle (§2); the
  **Context 4.1 / Interested Parties 4.2** registers (this slice sets the pattern, ships risk only); finer
  `register.*` create-vs-update SoD (additive R38 if ever needed).

## 13 · Register entry **R49** + back-propagation (write with the S-risk-spec PR)

**R49 — Risk & Opportunity register family (clause 6.1) — slice family S-risk.** *(Decision, owner,
2026-06-19.)* The clause 6.1 Risks & Opportunities register is the **first register family** and is a
**maintained controlled Document**: one `documented_information` (`kind=DOCUMENT`, new `document_type`
`RSK`, `is_singleton`, one non-Obsolete head per org) holding many `risk_opportunity` **satellite rows**
(`id` PK + `register_doc_id` FK + `row_version`) that **version together** under the lightweight approval
profile (`docs/04 §4.4`). The rows **are the version's controlled content**, edited **through FSM
revisions** (`start_revision`→edit→publish), read-only while Effective; live reads resolve against the
governing Effective version (the `form_template`/objectives precedent). *(The registers-as-Documents
doctrine this restates is the doc-14 §0 finding-R3 / §6 + `docs/04 A3`; the published register R3 is
"Authorization precedence".)* Non-★ clause 6.1 is Document-backed because a **register** is bound to the
Document lifecycle by A3 regardless of ★ (register vs progressing-activity; `improvement_initiative`/10.3/R46
is the own-table contrast). **Scoring (R18):** `scoring_method` enum (v1 `5x5_matrix`), `risk_rating =
likelihood × severity` ∈ 1..25 **stored** + re-derived on every write; a 4-band RAG **graded against the
governing version's frozen criteria** (no live-code re-grade), the criteria pinned in the version snapshot
+ a golden test on the code criteria; `scoring_method` write-once + `RISK_RESCORED` audit. **Permissions:**
the rows ride the **already-seeded** `register.read`/`register.manage` (PROCESS) — **no new key, catalog
stays 102, no R38 catalog change**; one **R38-additive *grant*** of `register.manage` → Process Owner with
the **`_PROCESS_SCOPE` template** (migration `0058`; a SYSTEM template would be unclamped — the load-bearing
detail). **Process-scope:** own-id-only per **R48**; filter-not-403 LIST (no `artifact_id`); PATCH-reassign
re-enforces the target. **CAPA:** `linked_capa_id` latch under a **`FOR UPDATE` parent lock** + additive
`CapaSource.risk` (S-risk-3). **No new** `SignatureMeaning` (R2), `audit_object_type`, or sig-hook; additive
`RISK_*` event types only. **Migration `0058`.** **Deferred (named):** doc-10 `subject.risk_rating` routing;
`include_subprocesses` (R48); the org-head register-steward role/UI; Context 4.1 / Interested Parties 4.2.

**Self-range bump (the S-include-subprocesses / Codex-P3 lesson — both labels):** `decisions-register.md:3`
intro `(R1–R48)` → `(R1–R49)`, and `decisions-register.md:55` `## Part 3 — Resolutions R1–R48` → `R1–R49`.

**Back-propagation** (staged with the slices, per the family-spec convention): `02` (6.1 register
as-built), `04` (the `§4.5`→`§4.4` pointer fix; `RSK` in the hierarchy), `07` (`register.*` now reach a
resource; §3.7 already correct), `10` (routing seam noted-deferred), `13` (high-risk dashboard), `14`
(as-built `risk_opportunity` + the stale `kpi_measurement` row fix), `15` (`/risks` endpoints; `§8.10b`
gate mapping `risk.*`→`register.*`), `16` (register family shipped), `18` (slice ledger). Plus
`docs/slice-history.md` + a capped `CLAUDE.md` Recent-learnings line + the memory resume note, on merge
(via `finish-slice`).
