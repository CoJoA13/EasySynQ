# S-obj-4 — Objective commitment revision (ISO 9001 clause 6.2) — design

- **Date:** 2026-06-12
- **Slice:** `S-obj-4` — backend-led (the commitment edit surface + the revision-aware submit + the read-back
  switch + the byte-path guard) + a front-end half (edit modal, revision panel, approver before/after,
  register state chips).
- **Status:** owner-approved design (brainstorm 2026-06-12; five owner forks + three micro-calls decided
  this session — see s0).
- **Closes:** the S-obj-3 deferral list (PR #118): **commitment-revision-edit**, **the frozen-snapshot
  read-back switch (F-2's deferred half)**, **the generic `checkout`/`checkin` byte-path guard on an OBJ**,
  and the **FE Submit-gate widening**. After this slice, an Effective Quality Objective's commitment can be
  revised end-to-end: Effective → UnderRevision → edit → re-freeze → re-approve → re-release, with the
  INV-1 cutover superseding v1 — and the live scorecard can never be re-graded by an unapproved edit.
- **Doc grounding:** R44 (the commitment is versioned and approved *through the vault lifecycle* — revision
  via the version chain is the R44-intended mechanism) · R2 (signature enum closed — re-approve/re-release
  reuse `approval`/`release`) · R43 (**in-force = `current_effective_version_id IS NOT NULL`, never
  doc-state** — opening a revision must lapse nothing) · R40 (SoD-2 via the shared
  `enrich_release_sod_scope`) · R38 (additive-only catalog — **not opened**; no new key) · R21
  (measurements stay ad-hoc, no version pin) · INV-1 (single-Effective partial-unique +
  SERIALIZABLE `_cutover`) · doc 04 T7/T9 (start-revision / re-submit) · doc 18 D-5 (**T8 abandon-revision
  is deferred from MVP** — no abandon affordance is promisable). As-built anchors cited inline.

> **The thesis.** S-obj-3 froze the commitment at first submit and shipped first-release-only; every read
> (register, scorecard, detail, Home, `record_measurement`) stayed on the mutable `quality_objective`
> working row — *provably equivalent* to the frozen snapshot **only because no commitment-edit path
> existed** (the F-2 owner call). S-obj-4 adds exactly that edit path, so the equivalence proof dies the
> moment this slice lands. The slice therefore does four things as one unit: (1) the edit surface
> (PATCH), (2) the revision lifecycle (start-revision + a revision-aware submit), (3) the **read-back
> switch** — every grading read resolves the *governing frozen commitment*, so an in-progress revision can
> never retroactively re-grade the live scorecard or leak an unapproved target into WORM evidence — and
> (4) the **byte-path guard**, closing the generic `/documents/*` seam that could otherwise mint or
> advance a commitment-less OBJ version around the freeze.

---

## s0 · Owner decisions (this session, 2026-06-12)

### Settled by the code (verified this session, not judgement calls)

- **V-1 — The freeze guard INVERTS on revision; submit needs two changes, not one.** The as-built skip
  condition — freeze iff `(latest.metadata_snapshot or {}).get("objective_commitment") is None`
  ([services/objectives/lifecycle.py:44-45](apps/api/src/easysynq_api/services/objectives/lifecycle.py:44))
  — would SKIP the re-freeze after `start_revision` (the latest version is v1's *Effective* version, whose
  snapshot **carries** a commitment), and T9 would then `IllegalTransition` (latest version not Draft). So
  S-obj-4 changes the Draft-only 409 **and** the freeze rule (s3.2). Vault-side, `submit_review` already
  handles T9 generically ([services/vault/lifecycle.py:181](apps/api/src/easysynq_api/services/vault/lifecycle.py:181);
  FSM rows T2+T9 at [domain/vault/lifecycle.py:74-79](apps/api/src/easysynq_api/domain/vault/lifecycle.py:74)),
  and the `_cutover` supersede is proven (`test_lifecycle.py::test_release_supersedes`).
- **V-2 — The read-back N+1 dissolves.** `list_objectives` is ONE query
  ([services/objectives/queries.py:21-37](apps/api/src/easysynq_api/services/objectives/queries.py:21));
  an `outerjoin(DocumentVersion, DocumentVersion.id == DocumentedInformation.current_effective_version_id)`
  is a **per-row PK probe** (the `drift_report.py` precedent), no new index. The only real cost: snapshot
  values are `build_commitment` **strings** and must be re-parsed before `rag_status` (s4.1).
- **V-3 — The objective-namespaced start-revision is forced, not preferred.** QMS Owner — the only
  `objective.manage` holder in v1 — holds **no `document.edit` and no `document.checkout`**
  (`0004_seed_authz.py` `_QMS_OWNER_KEYS`), so the generic `/documents/{id}/start-revision` gate
  (`document.edit`, [documents.py:1485-1493](apps/api/src/easysynq_api/api/documents.py:1485)) is
  unreachable for the objective owner. The S-obj-3 F-1 asymmetry repeats exactly. Migration-free:
  `objective.manage` is seeded; `REVISION_STARTED` is an existing audit type.
- **V-4 — The byte-path hole is wider than checkout/checkin: generic *submit* bypasses the freeze.**
  `checkout`/`checkin` are completely unguarded on an OBJ (zero objective references in
  `api/documents.py`; `_load_document` only checks `kind=DOCUMENT`, which an OBJ satisfies). But the
  dangerous bypass is generic `POST /documents/{id}/submit-review` (`document.submit`): it skips the
  objective freeze, so (a) it can advance a commitment-less byte-version toward an **Effective OBJ version
  with no commitment**, and (b) even with byte writers guarded, a PATCH edit after `changes_requested`
  followed by a *generic* submit would advance the **stale** frozen commitment around the content-compare
  re-freeze. Also confirmed: the FE `.find(Boolean)` detection would show an approver a **stale older
  commitment** when the newest version lacks one, and the v2-over-v1 ordering is pinned **by a comment
  only** — no test exists with two commitment-bearing versions.
- **V-5 — No audit event for working-copy edits is the precedent.** `PATCH /documents/{document_id}`
  (metadata) emits **no audit event** ([documents.py:720-765](apps/api/src/easysynq_api/api/documents.py:720))
  — the system's posture is that working-copy mutations are unaudited scratch; the auditable acts are the
  version-minting freeze (`CHECKIN` + `change_reason`) and the signatures. A revision's before/after is
  fully reconstructible from consecutive frozen snapshots. So the commitment PATCH emits nothing and the
  slice is **migration-free for certain** (no enum addition).
- **V-6 — R43 keeps everything in force through a revision for free.** The in-force predicate is the
  pointer, never doc-state — UnderRevision leaves the 6.2 ★ COVERED node, the mirror entry, and ack
  obligations intact; only the v2 cutover moves the pointer. (Smoke-assert, don't re-implement.)
- **V-7 — No stale-PENDING-task hazard.** An OBJ reaches Effective only after its cycle completed
  (task decided); at `start_revision` the latest instance is terminal (`APPROVED`), so no prior-cycle
  PENDING task can be decided against the v2 draft (`_decision_scope` reads the *latest* version — the
  hazard the verification flagged is unreachable in this flow).

### The owner forks (decided this session)

- **O-1 — Edit surface = `PATCH /objectives/{objective_id}`** *(chosen over edit-at-submit)*. The
  satellite is **already documented as "the editable working copy frozen into `metadata_snapshot` at
  check-in"** ([db/models/quality_objective.py:3-5](apps/api/src/easysynq_api/db/models/quality_objective.py:3))
  — the FRM working-schema shape was the intended design. Legal only in `Draft | UnderRevision` (the FRM
  form-schema guard posture). Iterative editing with live `BandPreview`; also closes the real v1 gap that
  a Draft objective is uneditable. Either option leaves the working row diverging from governing until
  re-release (the approval cycle is human-paced), so the read-back switch is load-bearing under both —
  this fork was UX/auditability/surface shape.
- **O-2 — Mid-revision measurement capture = the GOVERNING commitment** *(chosen over working-row /
  block-during-revision)*. When `current_effective_version_id` is set, `record_measurement`'s unit gate +
  `target_at_capture` read the governing Effective version's frozen snapshot (parsed); working-row
  fallback only pre-first-release (today's behavior, unchanged). Measurements keep flowing through a
  weeks-long revision, graded against what is actually approved; an unapproved edit can never leak into
  evidence-grade `KPI_READING` records (R44: `target_at_capture` "frozen at capture, never rewritten").
- **O-3 — Read-back switch = join everywhere** *(chosen over detail-only resolve / denormalize-at-release)*.
  ONE shared resolve helper feeds register, scorecard, detail, AND `record_measurement` — one semantics,
  no drift between surfaces. Detail-only was rejected (the scorecard would re-grade live against in-edit
  targets — the exact failure this slice exists to prevent); denormalize-at-release was rejected (it
  conflicts with O-1 — the satellite IS the edit buffer — and needs a kind-branch in `_cutover` or a
  crash-gapped second txn).
- **O-4 — Start-revision = full vault reuse** *(chosen over a minimal T7 flip / seeding `document.edit`)*.
  `POST /objectives/{objective_id}/start-revision` (`objective.manage`) is a thin wrapper over the SAME
  `services/vault/lifecycle.start_revision` (FSM T7 guard, Redis edit lock, WorkingDraft seeded from
  Effective, `REVISION_STARTED` audit) — the welded-path principle, parity with documents for free,
  doc-04's T7 canon verbatim. New obligation: submit, when freezing from UnderRevision, **releases the
  edit lock + deletes the WorkingDraft** (mirroring generic checkin) — else the lock dangles 8h (s3.3).
- **O-5 — Byte-path guard = all four writers** *(chosen over checkout/checkin-only / extended soft
  defense)*. 422 (the `not_form_template` precedent) on a doc with a `quality_objective` satellite at:
  `checkout` + `checkin` (service-level) and `start-revision` + `submit-review` (endpoint-level in
  `documents.py` — the guard CANNOT live inside the shared vault functions because the namespaced
  objective endpoints call those same functions). Generic release + all reads stay open (the approver card
  depends on `GET /documents/{id}/versions`). The snapshot-keyed freeze stays as belt-and-braces.
- **O-6 — FE trio:** (a) a **calm revision panel** replaces the stepper while UnderRevision ("Revision in
  progress — the released commitment keeps governing"; the latest-instance read would otherwise render
  v1's completed cycle as "Not yet released"); (b) the approver card gains **before/after rows** computed
  FE-side from the two newest commitment-bearing versions (no API change; forces the missing two-version
  test); (c) the register gets **state chips on non-Effective rows only** (Effective stays clean — calm;
  also fixes the pre-existing Draft-invisibility).

### Micro-calls (proposed in the design message; owner-approved)

- **A — PATCH audit = none** (resolved by V-5: the metadata-PATCH precedent; the version chain is the
  audit trail). No migration, no fallback needed.
- **B — Unit-change reset at release:** in the namespaced release endpoint, after `release()` +
  `expire_all`, if the newly-governing unit ≠ the previously-governing unit → `current_value = None`
  (RAG honestly reads `unmeasured` until a reading in the new unit lands). A crash in the gap self-heals
  at the next measurement (which validates against the new governing unit).
- **C — `ObjectiveCapabilities` gains `edit` + `start_revision`** — computed identically to `submit`
  today (all = `objective.manage` at the objective's scope; permission-only, state-blind, per the existing
  convention), added anyway as self-describing affordances so the FE never gates an Edit button on a flag
  named `submit`.

---

## s1 · What the code pins (verified, restated — not re-decided)

- **Submit today:** `submit_objective_for_review` 409s unless `Draft`
  ([lifecycle.py:31-37](apps/api/src/easysynq_api/services/objectives/lifecycle.py:31)); freeze-iff-snapshot-None
  (lines 44-45); ONE txn: freeze (flush) → `submit_review` → `instantiate_approval` → audit → commit.
  `checkin_objective_commitment` flushes-never-commits, flush-before-`_emit`, `_snapshot(doc,
  objective_commitment=...)` one-kwarg fold ([service.py:630-726](apps/api/src/easysynq_api/services/vault/service.py:630)).
- **Reads today:** EVERY commitment field in `_objective` reads the mutable satellite
  ([api/objectives.py:134-171](apps/api/src/easysynq_api/api/objectives.py:134)); the only version-sourced
  field is `effective_from` (the version *column*, not the snapshot). `record_measurement` validates unit
  and freezes `target_at_capture` from the working row under FOR UPDATE + `populate_existing`
  ([services/objectives/service.py:185-204](apps/api/src/easysynq_api/services/objectives/service.py:185)).
  The rollup is the only post-create satellite write (`qo.current_value = latest_value`, service.py:248).
- **`_load_objective_doc(for_update=True)`** locks the DOC row (+`populate_existing`) and freshens the
  satellite with `populate_existing` only — the doc row is the serialization point
  ([api/objectives.py:246-263](apps/api/src/easysynq_api/api/objectives.py:246)).
- **Vault T7:** `start_revision` requires Effective (FSM), acquires the Redis edit lock (409
  `lock_conflict`), upserts a WorkingDraft seeded from the Effective version, flips `current_state` to
  UnderRevision (the Effective VERSION untouched — keeps governing), audits `REVISION_STARTED`, commits
  in the request session, **no signature** ([services/vault/lifecycle.py:267-321](apps/api/src/easysynq_api/services/vault/lifecycle.py:267)).
- **Generic checkin** requires the actor to hold the WorkingDraft, INV-3 reason+significance, staged WORM
  bytes; mints `version_seq` N+1 `version_state=Draft`; **no doc-state guard anywhere**; releases the lock
  post-commit ([service.py:274-363](apps/api/src/easysynq_api/services/vault/service.py:274)).
- **Approval machinery:** every submit instantiates a FRESH instance + one APPROVE task (no single-active
  guard on DOCUMENT subjects — the doc-row FOR UPDATE + the FSM gate is the duplicate protection);
  `GET /objectives/{id}/approval` returns the latest instance by `started_at DESC`
  ([api/objectives.py:466-484](apps/api/src/easysynq_api/api/objectives.py:466)); the decide leg's approve
  is T2/T9-agnostic (keyed on `InReview` only) and SoD-1 binds the latest version's immutable
  `author_user_id`; release SoD-2 rides `enrich_release_sod_scope` (R40 — shared with DCR).
- **Re-release emissions:** `_cutover` unconditionally signs `meaning=release` on the promoted version and
  audits `SUPERSEDED` on the demoted one — **no new signature meaning needed** (R2 closed). The
  commitment freeze dedups identical bytes on sha256 (`on_conflict_do_nothing`) — a re-freeze of an
  unchanged commitment would reuse the SAME WORM blob (but the s3.2 rule skips minting in that case).
- **FE today:** `canSubmit = caps.submit && state === "Draft"`
  ([ObjectiveDetailPage.tsx:51-53](apps/web/src/features/objectives/ObjectiveDetailPage.tsx:51)); widening
  it breaks **zero** existing tests (additions only). `AuthorActions` is the precedent
  (`canRevise = state === "Effective" && caps.edit`; `draftLike = Draft || UnderRevision`). Register rows
  already carry `current_state` (NOT detail-only). `StateBadge` has the "Under revision" treatment but is
  typed `DocumentCurrentState`; `ObjectiveState` is a structurally identical separate alias. The approver
  detection `.find(Boolean)` over newest-first versions lives in
  [ReviewApprovePage.tsx:63-75](apps/web/src/features/review/ReviewApprovePage.tsx:63).

---

## s2 · Backend — the edit surface (`PATCH /objectives/{objective_id}`)

Gate: the existing `_objective_manage_path` (`objective.manage`, PROCESS resolver, SYSTEM fallback).
Load: `_load_objective_doc(for_update=True)` — the doc-row FOR UPDATE is the serialization point against
a concurrent submit/measurement; `populate_existing` on BOTH rows (the S-drift-1 trap; the satellite's
fields are what we're editing).

- **Body** (Pydantic, all fields optional): `target_value` (decimal string), `unit`, `direction`,
  `due_date`, `at_risk_threshold` (nullable), `baseline_value` (nullable), `policy_id` (nullable).
  **Explicit-null clears** the three nullable fields — distinguish omitted-vs-null via
  `model_fields_set` (the `review_period_months` precedent, [documents.py:740](apps/api/src/easysynq_api/api/documents.py:740)).
- **State guard:** 409 unless `current_state ∈ {Draft, UnderRevision}` (the FRM form-schema posture:
  "Draft/UnderRevision only"). The kind guard rides `_load_objective_doc`'s satellite-existence 404.
- **Validation mirrors create** (whatever `create_objective` validates — direction enum, decimal parsing,
  policy-id check if any; verify at plan time and mirror exactly). A backwards at-risk threshold stays
  collapse-to-red server-side (the FE soft-warns; the S-obj-2 posture).
- **No audit event** (micro-call A / V-5). `doc.updated_by = caller.id`; commit; respond with the
  standard `_objective(...)` shape (the submit/release response posture — non-detail; the FE invalidates
  and re-fetches detail).
- The PATCH never touches the Redis edit lock (the lock guards the *byte* path; the objective surface's
  serialization is the doc-row FOR UPDATE).

---

## s3 · Backend — start-revision + the revision-aware submit

### s3.1 · `POST /objectives/{objective_id}/start-revision` — gate `objective.manage`

Thin wrapper (O-4): `_load_objective_doc(for_update=True)` → the SAME vault
`start_revision(session, vault_sink, caller, doc)` (it enforces T7 via the FSM — 409 from any state but
Effective; acquires the lock — 409 `lock_conflict` if held; commits internally). Response: `_objective`
(`current_state=UnderRevision`). Contract: new path; migration-free.

### s3.2 · The revision-aware submit (`submit_objective_for_review`)

Two changes (V-1), same one-transaction shape otherwise:

```python
# State guard widens (T2 + T9):
if doc.current_state not in (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision):
    raise ProblemException(409, "conflict", "Objective is not in Draft or UnderRevision", ...)

# The freeze rule becomes content-aware (replaces the snapshot-is-None skip):
latest = await vault_repo.latest_version(session, doc.id)
working = build_commitment(target_value=qo.target_value, ...)   # the SAME 7-field canonical dict
needs_freeze = (
    latest is None                                              # first submit (S-obj-3 path)
    or latest.version_state is not VersionState.Draft           # revision: latest = v1 Effective
    or (latest.metadata_snapshot or {}).get("objective_commitment") != working
    #  ^ byte-version (None ≠ working) OR a PATCH since the last freeze — re-freeze so the
    #    approver always signs the CURRENT commitment; an unchanged re-submit still dedups.
)
if needs_freeze:
    await checkin_objective_commitment(..., commitment=working,
        change_reason=change_reason or _default_reason(doc.current_state),
        change_significance="MAJOR")
result = await submit_review(session, actor, doc)               # T2 or T9 — the FSM adjudicates
await instantiate_approval(session, result.doc, actor)
audit_transition(session, vault_sink, result, actor)
# O-4 obligation — only when a WorkingDraft exists (i.e. we arrived via start_revision):
#   delete the working_draft row IN the txn; release the Redis lock (wd.lock_token) POST-commit
#   (the generic-checkin pattern). Release regardless of holder — the objective surface owns its lock.
await session.commit()
```

- Dict equality is exact: `build_commitment` produces canonical strings, and the snapshot was minted from
  the same function — `!=` is a faithful changed-content test.
- A PATCH-after-`changes_requested` (doc back in Draft, a frozen Draft version exists) re-freezes a NEW
  version; the old Draft version strands as an inert orphan (the same posture as the S-obj-3 byte-orphan
  — version rows are WORM, never updated).
- The submit body gains optional `change_reason` (INV-3 still enforced non-empty by the freeze; defaults:
  `"Objective commitment submitted for review"` from Draft, `"Objective commitment revised"` from
  UnderRevision). Significance stays **MAJOR** always — which honestly triggers the R43 re-ack sweep on
  re-release if a distribution exists on the OBJ.
- SoD is unchanged and genuinely binds: the freezer/submitter IS the new version's `author_user_id`
  (SoD-1 blocks them approving; SoD-2 blocks author/approver releasing).

### s3.3 · Release — one addition (micro-call B)

The endpoint keeps its shape (imperative `enforce("document.release", enriched-scope, sig_hook=True)` →
shared `release()` → `session.expire_all()`). Addition: **before** calling `release()`, capture the
currently-governing unit (pointer → version snapshot, if any); **after** `expire_all`, read the
newly-governing unit; if both exist and differ → `qo.current_value = None` + commit (the request-session
txn). The next `record_measurement` (validating against the NEW governing unit) re-rolls it. `_cutover`
itself stays untouched and kind-agnostic.

---

## s4 · Backend — the read-back switch (F-2 closed)

### s4.1 · `parse_commitment` (pure, `domain/objectives/commitment.py`)

The inverse of `build_commitment`: `{strings} → (Decimal target_value, str unit, ObjectiveDirection,
date due_date, Decimal|None at_risk_threshold, Decimal|None baseline_value, UUID|None policy_id)`.
Strict — the snapshot is only ever minted by `build_commitment`, and the byte-path guard (s5) makes a
commitment-less governing version unconstructible; a parse failure is a drift-class event and may raise.

### s4.2 · The resolve helper + the query join

- `queries.list_objectives` gains
  `.outerjoin(DocumentVersion, DocumentVersion.id == DocumentedInformation.current_effective_version_id)`
  and selects `DocumentVersion.metadata_snapshot["objective_commitment"]` per row (OUTER — a NULL pointer
  row falls back to the working row; pre-first-release behavior stays bit-identical).
- One shared resolver (objectives service layer): governing snapshot **parsed** when present, else the
  working-row fields. `_objective` takes the resolved commitment for
  `target_value/unit/direction/due_date/at_risk_threshold/baseline_value/policy_id` and computes
  `rag/pct_toward_target/attainment` from **resolved commitment + `qo.current_value`** (the operational
  rollup stays satellite-side). Serialization stays decimals-as-strings — output shape unchanged.
- **Register, scorecard, detail, and `record_measurement` all read through the same resolver** (O-3).
  `record_measurement` resolves the governing commitment via a fresh pointer read (a plain
  `populate_existing` doc-row SELECT — **no lock**: it already holds the qo FOR UPDATE, and a doc-row lock
  here would invert `_load_objective_doc`'s doc→satellite lock order against a concurrent submit) for the
  unit gate + `target_at_capture`; working-row fallback pre-first-release (today's behavior).
- The detail endpoint already `session.get`s the Effective version for `effective_from` — reuse that one
  read for the governing snapshot (no extra round-trip).

### s4.3 · `pending_commitment` (detail-only) + capabilities

- Detail gains **`pending_commitment`**: the working-row `build_commitment(...)` dict **when a governing
  commitment exists AND differs**, else `null`. (Pre-first-release: governing is null → `pending_commitment`
  null — the main fields already show the working values.) Shape = the same 7-string-field commitment
  dict the approver card already types as `ObjectiveCommitment`.
- `_objective_capabilities` returns `{submit, release, edit, start_revision}` — `edit` and
  `start_revision` computed once with `submit` (all = `objective.manage` at the objective's scope;
  state-blind per the existing convention; the FE combines with state).

---

## s5 · Backend — the byte-path guard (O-5)

One helper (satellite-existence check — the S-rec-1 kind-guard posture; a PK probe):

```python
async def _reject_objective_byte_path(session, doc) -> None:
    if await session.get(QualityObjective, doc.id) is not None:
        raise ProblemException(status=422, code="validation_error",
            title="Quality Objectives are managed via /objectives",
            errors=[{"field": "document_id", "code": "objective_managed_via_objectives"}])
```

- **Service-level** at the top of `checkout` and `checkin` (before the lock/WD checks, so the 422 is
  deterministic) — covers any future route/CLI. The objective surface never calls either
  (`checkin_objective_commitment` is separate; vault `start_revision` seeds the WorkingDraft directly).
- **Endpoint-level** in `documents.py` for `start-revision` and `submit-review` (after `_load_document`)
  — it cannot live inside the shared vault `start_revision`/`submit_review`, which the namespaced
  objective endpoints call.
- Generic **release stays open** (it is the same shared `release()` the namespaced endpoint calls —
  nothing to bypass); **all reads stay open** (`GET /documents/{id}`, `/versions` — the approver card
  depends on them). `PATCH /documents/{id}` (title/folder/classification metadata) also stays open — it
  touches no commitment field and is the only title-edit path for an OBJ (noted in s12).
- `tests/integration/test_objective_lifecycle.py::test_submit_freezes_even_after_a_generic_byte_checkin`
  — the ONE test pinning the open seam — is **rewritten by design** to assert the four 422s. The
  snapshot-keyed freeze condition keeps its unit pin (belt-and-braces). The welded S5 suite drives plain
  SOPs and is untouched; add a non-OBJ no-misfire leg (an ordinary doc still checks out/in fine).

---

## s6 · Authz summary (no new key; catalog stays 100)

| Action | Gate key | Where | Notes |
|---|---|---|---|
| Edit commitment (working copy) | `objective.manage` | `PATCH /objectives/{id}` | Draft\|UnderRevision only; no audit (V-5) |
| Start revision (T7) | `objective.manage` | `POST /objectives/{id}/start-revision` | wraps vault `start_revision` (lock + WD + audit) |
| Re-submit (T9) / submit (T2) | `objective.manage` | widened `POST /objectives/{id}/submit-review` | content-aware re-freeze; releases lock/WD from UnderRevision |
| Approve | `document.approve` | existing `/tasks` DOCUMENT leg | unchanged; SoD-1 binds the freezer-author |
| Re-release (T6 + supersede) | `document.release` | `POST /objectives/{id}/release` | unchanged gate; + the unit-change reset (B) |
| Generic byte path on an OBJ | — | `checkout`/`checkin`/`start-revision`/`submit-review` | **422 guard** (s5) |

**Demo (live smoke):** the same SYSTEM overrides as S-obj-3 on the LIVE `demo` `app_user` row (org
**AHT**): `objective.manage` + `objective.read` + `kpi.*` + `document.approve`/`document.release` +
`document.read`/`document.read_draft`. The owner performs the Keycloak login.

---

## s7 · Migration

**None — guaranteed, not just expected. Head stays `0049`.** No new permission key (R38 untouched), no
new signature meaning (R2 closed), no new audit event type (micro-call A/V-5), no schema delta (the
governing read joins existing columns; `pending_commitment` is computed). `/check-migrations` must
round-trip clean as a no-op confirmation.

---

## s8 · Contracts (`packages/contracts/openapi.yaml`)

All objective schemas are `additionalProperties:false` — every addition is load-bearing:

1. **New paths:** `PATCH /objectives/{objective_id}` (the edit body; 409 state guard documented),
   `POST /objectives/{objective_id}/start-revision` (T7; 409 not-Effective / lock_conflict).
2. **`ObjectiveCapabilities`** += `edit`, `start_revision` (booleans).
3. **`Objective`** += `pending_commitment` (nullable object, detail-only like
   `capabilities`/`effective_from`) — define an `ObjectiveCommitment` schema (the 7 string/nullable-string
   fields) and reference it.
4. **Prose fixes (now wrong):** the submit-review op's description ("the FIRST submit freezes … a
   re-submit advances the existing Draft version unchanged") and its 409 ("Not in Draft") → T2/T9 +
   content-aware re-freeze; the release op's "a v1 objective has exactly one version stream" framing →
   supersession semantics.
5. **422 notes** on the four guarded document ops (`checkout`/`checkin`/`start-revision`/`submit-review`):
   rejected on Quality Objectives.

---

## s9 · Front-end (apps/web)

### s9.1 · Types + MSW

- `lib/types.ts`: unify `ObjectiveState` with `DocumentCurrentState` (alias — structurally identical);
  `capabilities` gains `edit`/`start_revision`; `Objective` gains
  `pending_commitment?: ObjectiveCommitment | null`.
- MSW: handlers for PATCH + start-revision; fixtures **pinned to the as-built serializers** (incl. an
  UnderRevision detail fixture with `pending_commitment`, and a **two-commitment-version** `/versions`
  fixture: v2 InReview over v1 Effective).

### s9.2 · Objective detail page

- **Start revision** button: `caps.start_revision && state === "Effective"` (the `canRevise` precedent);
  `useStartObjectiveRevision()`.
- **Edit commitment** modal: the `NewObjectiveModal` field set (segmented direction + `BandPreview` +
  the soft non-blocking backwards-threshold warn), seeded from `pending_commitment ?? current fields`;
  gated `caps.edit && (Draft || UnderRevision)`; conditionally rendered (`{open && <Modal/>}`) + a
  reopen-resets test; explicit-null semantics for cleared optional fields. `useUpdateObjective()`.
- **Submit** widens to `caps.submit && (Draft || UnderRevision)`.
- **The calm revision panel** (O-6a): while `UnderRevision`, the Lifecycle card replaces the stepper with
  "Revision in progress — the released commitment keeps governing" + the Edit/Submit affordances; the
  stepper returns when re-submit creates the v2 instance.
- **Proposed revision card:** when `pending_commitment` is non-null, was→now rows (governing main fields
  vs pending) for changed fields only.
- `CommitmentHero` needs **no change** — the API switch makes its fields governing automatically. The
  header badge upgrades from the raw-enum gray `Badge` to the shared `StateBadge` (prop type unified).

### s9.3 · Register / scorecard

State chip next to Ref **only on non-Effective rows** (O-6c) via the unified `StateBadge`; Effective rows
stay clean. The scorecard band needs no change (its counts now ride governing-graded RAG server-side).

### s9.4 · `/tasks` approver card

`ReviewApprovePage` computes the **two newest commitment-bearing versions**; `ObjectiveCommitmentContext`
gains an optional `previous` prop and renders **was → now** per changed field (e.g. "Target: 95 % → 97 %"),
unchanged fields plain (O-6b). Add the missing **two-version test** (v2 InReview over v1 Effective — the
comment-only pin becomes a regression pin). With the byte path guarded, `.find(Boolean)` is safe by
construction; the before/after picker naturally skips commitment-less orphans.

### s9.5 · Untouched by design

`RecordMeasurementModal` (its locked unit IS the governing unit post-switch), Home dashboard (rides the
scorecard), the DOC_ACK/periodic-review legs, the documents surfaces (bar the four 422s, which no UI
offers on an OBJ anyway).

---

## s10 · Testing strategy

**Local gates (this box):** `/check-web` (full — the tsc-only jest-dom `import {expect, it} from "vitest"`
trap), `/check-api` (static), `/check-contracts`, `/check-migrations` (no-op confirm). Unit+integration
pytest = Linux CI; backend behavior verified live via the worker heredoc.

- **API unit:** `parse_commitment` round-trip with `build_commitment` (incl. None legs); the freeze-rule
  matrix (no version / latest-not-Draft / equal-dict skip / differing-dict re-freeze / byte-version
  re-freeze); resolver fallback (pointer-null → working row); the PATCH explicit-null field semantics.
- **API integration** (run-scoped/delta assertions; self-provided preconditions): the full revision
  round-trip — release v1 → `start-revision` → PATCH (new target/unit) → re-submit (asserts a NEW frozen
  version, lock released, WD gone) → approve → re-release → **v1 Superseded + v2 Effective +
  `effective_count == 1` + fresh `approval`/`release` signatures + the 6.2 node STAYS COVERED across the
  whole window (R43)**; the read-back switch — during UnderRevision-with-edits the register/scorecard/
  detail still serve v1's values + RAG, `pending_commitment` carries the edit; **mid-revision
  `record_measurement` validates against v1's unit and freezes v1's target** while the working row holds
  the edit; the unit-change reset (current_value → None at re-release; rag `unmeasured`); the four guard
  422s + the non-OBJ no-misfire leg; PATCH 409 on Effective/InReview; submit 409 on InReview/Effective;
  the two-session `populate_existing` race posture on PATCH (prime via `session.get`, mutate via B,
  locked-load on A).
- **Web:** the s9 surfaces — edit modal (seeding, soft-warn, explicit-null, reopen-resets), revision
  panel, Submit-on-UnderRevision, proposed-revision card, register chips (incl. Draft), approver
  before/after + the **two-commitment-version detection test**, hooks. Baseline **679** → delta tracked.
- **Pre-merge live smoke:** backend worker-heredoc (start-revision → PATCH → re-submit → re-approve →
  re-release → v1 Superseded + v2 Effective + 6.2 COVERED throughout + RAG reads the new frozen target);
  FE via Chrome MCP — **rebuild the web image too** (`up -d --build web` + hard refresh; api-only rebuilds
  leave the old bundle); grants per s6; the owner does the Keycloak login.
- **diff-critic** on the branch diff pre-PR; Codex triage (disregard D1-moot multi-tenant framing; verify
  each claim against code before fixing).

---

## s11 · Risks / traps to carry in

- **`populate_existing` on EVERY handler-level locked load** — PATCH, start-revision, submit (the authz
  resolver identity-maps both rows; a stale satellite would freeze yesterday's commitment — the trap that
  bit twice in S-obj-3).
- **The freeze rule's `!=` compares canonical dicts** — both sides must come from `build_commitment` /
  the snapshot it minted; never compare against a hand-built dict (string canonicalization differs).
- **Lock release at submit** uses the WD's stored `lock_token`, post-commit, regardless of holder; the WD
  row delete rides the txn. Submit from Draft (no WD) skips both.
- **Snapshot strings re-parse before `rules.py`** — `Decimal >=` against a str TypeErrors; the resolver is
  the ONLY place parsing happens.
- **`record_measurement`'s governing read must be fresh** (pointer read with `populate_existing` — a
  cutover may have landed while waiting on the qo lock).
- **The guard helper runs BEFORE the lock/WD checks** in checkout/checkin so the 422 is deterministic, and
  lives at the ENDPOINT for start-revision/submit-review (the vault functions are shared with the
  objective surface).
- **Contract additions to `additionalProperties:false` schemas are load-bearing** — `pending_commitment`,
  the two capability flags, and both new paths must land in `openapi.yaml` in-PR.
- **MSW fixtures pinned to the as-built serializers** (copy shapes from `api/objectives.py`); detail-only
  fields stay off list fixtures.
- **An errored/forbidden read shows neutral copy** — the revision panel and proposed-revision card must
  never render a positive claim over an error (the S-home-1 green-on-error class).
- **Drive-by:** fix the stale comment in `checkin_objective_commitment`
  ([service.py:712-714](apps/api/src/easysynq_api/services/vault/service.py:712)) claiming
  `checkin`/`checkin_form_schema` emit pre-flush — both were fixed in the same PR; the comment misleads.

---

## s12 · Honest deferrals (named, not faked)

- **Abandon-revision (T8)** — deferred from MVP (doc 18 D-5) for ALL documents; an UnderRevision objective
  (like an UnderRevision SOP) can only move forward via re-submit. No abandon affordance is shipped or
  faked.
- **Approval-cycle history** — `GET /objectives/{id}/approval` stays latest-instance; v1's completed cycle
  leaves the stepper when v2 submits (the audit trail + version chain hold the history). A history read is
  a future contract addition.
- **Management-review approval routing** (the OBJ rides the generic `document_approval` pool);
  **Process-Owner `objective.manage`** (owner-assignment binding); **KPI trend charts** (N6);
  **org-tunable RAG thresholds** (v1.x); **title edit via the objectives surface** (title is document
  metadata, not commitment — the generic `PATCH /documents/{id}` path remains the title route, gated
  `document.manage_metadata`, which the QMS Owner does not hold in v1).
- **The §9.3 Management-Review epic** (its input entities mostly don't exist) — unchanged.

---

## s13 · Build sequence (for the implementation plan)

1. **Pure core** — `parse_commitment` + the freeze-rule predicate as a testable unit + the resolver;
   unit tests (round-trip, matrix, fallback).
2. **The byte-path guard** (s5) — the helper + four call sites; rewrite the pinned open-seam test; the
   no-misfire leg. (Early, so every later integration test runs against the guarded world.)
3. **PATCH** (s2) — body model (explicit-null), state guard, validation-mirrors-create; unit + integration.
4. **Lifecycle** (s3) — start-revision endpoint; the widened submit (state guard + content-aware freeze +
   lock/WD release + `change_reason`); the release unit-change reset; the full revision-round-trip
   integration test.
5. **The read-back switch** (s4) — the query join + resolver into register/scorecard/detail +
   `record_measurement`; `pending_commitment` + capability flags; the mid-revision read/measure
   integration tests.
6. **Contracts** (s8) — paths, schemas, prose; `/check-contracts`.
7. **FE** (s9) — types/MSW → detail affordances + revision panel + edit modal → register chips → approver
   before/after; web tests throughout.
8. **Verify** — `/check-api`, `/check-web`, `/check-contracts`, `/check-migrations` (no-op); diff-critic;
   live smokes (worker heredoc + Chrome MCP with the web image rebuilt); slice-history entry + CLAUDE.md
   learnings line; Codex triage; squash-merge on owner OK.

After this slice the Quality Objectives family is **revision-complete**: the commitment lifecycle is a
closed loop (author → release → revise → re-release), the governing frozen commitment is the single
source every grading read resolves, and the generic byte seam S-obj-3 left open is welded shut.
