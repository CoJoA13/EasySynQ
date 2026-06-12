# S-obj-3 — Objective lifecycle & release (ISO 9001 clause 6.2) — design

- **Date:** 2026-06-11
- **Slice:** `S-obj-3` — backend-led (lifecycle wiring + commitment-freeze) + a smaller front-end half (detail-page affordances + create-modal policy picker).
- **Status:** owner-approved design (brainstorm 2026-06-11; the three forks below were decided by the owner this session).
- **Closes:** the Quality Objectives family — objectives stop being permanently-Draft; a released objective flips the **6.2 ★** compliance-checklist node to **COVERED** automatically and lights the dashboard CHECK quadrant's coverage honestly. The named deferrals from S-obj-1 / S-obj-2 / S-home-1 ("lifecycle/release omitted → the 6.2 ★ node stays not-COVERED"; "the create policy field omitted in v1") are resolved here.
- **Doc grounding:** R44 (Quality Objectives ARE versioned `kind=DOCUMENT` subtypes, approved through the existing vault lifecycle) · R25 (POL singleton) · R2 (signature meanings `review`/`approval`/`release` already exist — **enum closed, none added**) · R21 (version-pin) · R38 (additive-only catalog — **not opened here**) · R1/INV-1 (7-state machine + single-Effective SERIALIZABLE cutover). As-built anchors are cited inline as `file:line`.

> **The thesis (restated from R44).** A Quality Objective's **commitment** — statement, target, unit, direction, due date, at-risk threshold, baseline, policy link — is a *controlled document*: versioned, approved, clause-mapped, on the 7-state lifecycle. S-obj-1 built the satellite + the operational measurement rollup but **never wired the lifecycle**, so the commitment never got *frozen into a version* and the objective never reached Effective. S-obj-3 wires that lifecycle by **reusing the proven document machinery** — folding a `form_template`-style commitment-freeze into a single "Submit for review" action, then riding the generic review → approve → release path (DOCUMENT `/tasks` decide leg + the INV-1 SERIALIZABLE `_cutover`) unchanged.

---

## s0 · Owner decisions (this session, 2026-06-11)

The exploration verified that **five** of the originally-open questions are settled by the code, not by judgement; **three** were genuine forks the owner decided.

### Settled by the code (not a judgement call)

- **D-A — No migration. Head stays `0049`.** The freeze is a new optional JSONB key in `document_version.metadata_snapshot` (no column); the OBJ version reuses `document_version` as-is; no new permission key (catalog stays **100**); no new `signature_event.meaning` (R2 closed — `review`/`approval`/`release` already emitted by the existing lifecycle). The POL-id read is a GET (no schema). **Conditional:** migration-free *only because* the submit gate is `objective.manage` (a key the objective owner already holds) — see fork F-1; the rejected "uniform document.*" option would have needed a role-grant seed (a `0050`).
- **D-B — A source blob is forced, not chosen.** `document_version.source_blob_sha256` is `NOT NULL` ([db/models/document_version.py:77](apps/api/src/easysynq_api/db/models/document_version.py:77)) and `metadata_snapshot` is `NOT NULL` too. A version *cannot* exist with only a snapshot. So the commitment **must** be a WORM blob. The only viable shape is the S-rec-3 `form_template` recipe: canonical-serialize (rfc8785 JCS) → `put_staging_bytes(application/json)` → `finalize_worm` → a `application/json` blob, which already matches `_NON_RENDERABLE_PREFIXES` ([render_gotenberg.py:40](apps/api/src/easysynq_api/services/vault/render_gotenberg.py:40)) → the version lands `no_controlled_rendition` (R26): source-bytes-only in the mirror, never a garbage CONTROLLED COPY. **No render-config change needed** (`application/json` is already non-renderable).
- **D-C — The freeze folds into `_snapshot` via ONE optional kwarg, never a branch.** `_snapshot(doc, *, field_schema=None, distribution=None)` ([services/vault/service.py:93-121](apps/api/src/easysynq_api/services/vault/service.py:93)) is the single shared snapshot function; the form path extends it with one optional `field_schema` kwarg and ordinary docs are byte-untouched. S-obj-3 adds **one** more optional kwarg, `objective_commitment=None`, the same way.
- **D-D — Approval rides the DOCUMENT decide leg, byte-identical.** `instantiate_approval` hardcodes `subject_type=WorkflowSubjectType.DOCUMENT` ([services/workflow/service.py:67](apps/api/src/easysynq_api/services/workflow/service.py:67)); the `/tasks/{id}/decision` dispatcher ([api/workflow.py:196-275](apps/api/src/easysynq_api/api/workflow.py:196)) falls through to the DOCUMENT leg for any `subject_type` not CAPA/DCR/PERIODIC_REVIEW/DOC_ACK; `get_document` is kind-agnostic. **No new `WorkflowSubjectType`, no new decide branch.** The `approve` outcome emits `meaning=approval`; `release` emits `meaning=release` — both via the existing `sig_hook`+SoD path.
- **D-E — 6.2 ★ flips COVERED on release with zero further code.** The OBJ is auto-mapped to clause 6.2 at *create* ([services/objectives/service.py:134-153](apps/api/src/easysynq_api/services/objectives/service.py:134)); the checklist keys COVERED on `current_effective_version_id IS NOT NULL` ([services/reports/checklist.py:84-86](apps/api/src/easysynq_api/services/reports/checklist.py:84)), which `release`'s `_cutover` sets ([services/vault/lifecycle.py:513-514](apps/api/src/easysynq_api/services/vault/lifecycle.py:513)). The checklist is a pure live read — release one OBJ, the 6.2 node reads COVERED on the next request. The checklist tracks **no** 6.2 sub-requirements (policy/plans/measurements) beyond "≥1 mapped Effective doc", so one Effective objective fully satisfies the node.

### The three genuine forks (owner-decided)

- **F-1 — Lifecycle gating: `objective.manage` submit + `document.*` sign-offs.** *(chosen over "uniform document.*")*
  The freeze/submit is gated `objective.manage` — the QMS Owner *owns* objectives but does **not** hold `document.submit`; gating on `objective.manage` aligns "who puts an objective up for review" with objective ownership and avoids a role-grant migration. The **sign-offs ride the document.* SoD+signature machinery**: `approve` is `document.approve` (the DOCUMENT `/tasks` leg, `sig_hook`, SoD author≠approver); `release` is `document.release` (the existing release endpoint's gate + `enrich_release_sod_scope`). SoD story: *ownership* puts it up for review; the *cryptographic sign-offs* are document-gated and separated by SoD.
  - *Rejected — "All `document.*` (uniform)":* zero new endpoints but QMS Owner lacks `document.submit`/`document.release`, so it would need role-grant seed additions (a `0050`) **or** only Authors/Process-Owners could ever submit an objective. The owner chose to keep the slice migration-free and ownership-aligned.

- **F-2 — Effective-commitment read-back: working row for v1; defer snapshot reads.** *(chosen over "frozen snapshot now")*
  The commitment is frozen into the WORM version (the signed audit truth + the mirror entry), but the **register / scorecard / detail DISPLAY reads stay on the mutable `quality_objective` working row**. This is *provably equivalent* in v1 because **there is no commitment-edit endpoint** — the working row cannot diverge from the latest frozen version. It avoids an N+1 Effective-version lookup across the register + scorecard lists. The read-from-immutable-snapshot switch (the S-rec-3 principle) is **deferred to the commitment-revision-edit slice** (named, see s11).
  - *Rejected — "frozen snapshot now (detail only)":* correct-by-construction and matches the carried-in trap verbatim, but adds a `commitment_from_version` accessor + resolve-effective-version + a serializer branch for behaviour identical to the working row until revision-edit exists. YAGNI for v1.

- **F-3 — Approver sees an OBJ commitment context card.** *(chosen over "reuse the document VersionCompare redline")*
  In `/tasks`, the OBJ approval rides the DOCUMENT decide leg whose default context is a `VersionCompare` page redline — meaningless for a first-release JSON-source objective (no prior version). Instead, a small `ObjectiveCommitmentContext` component renders the **frozen commitment** (target / direction / at-risk threshold / due date / baseline / linked policy) read from the version's `metadata_snapshot.objective_commitment`. **This needs no new backend read:** the DOCUMENT leg already fetches the version list under `document.read` (which the approver holds — `_APPROVER_KEYS` includes `document.read`/`document.read_draft`) and `_version` already returns `metadata_snapshot` ([api/documents.py:222-232](apps/api/src/easysynq_api/api/documents.py:222)).
  - *Rejected — "reuse VersionCompare":* zero new component, but an empty/odd redline for the approver. Weaker UX for the calm-progressive-disclosure thesis.

### Proposed-and-accepted (stated in the prior message, no owner objection)

- **POL-id read endpoint** = `GET /objectives/policy`, gated `objective.read`, returns `{id, identifier, title} | null` for the create modal's policy picker. (See s4.4.) Static route — **registered before `/objectives/{objective_id}`** (the `scorecard` precedent, S-pack-2 lesson).
- **Revision deferred.** First-release only in v1: Draft → InReview → Approved → Effective. The `_cutover` mechanism *supports* revision (Effective → UnderRevision → new version), but with no commitment-edit endpoint a "revision" would re-freeze identical bytes — pointless. **Defer commitment-revision-edit** (named, s11).

---

## s1 · What the canon already pins (settled — restated, not re-decided)

- **`quality_objective` is a shared-PK subtype** of `documented_information` (`id` is PK **and** FK→`documented_information.id`, RESTRICT) ([db/models/quality_objective.py:25-33](apps/api/src/easysynq_api/db/models/quality_objective.py:25)); `kind=DOCUMENT`, `document_type=OBJ`. Created at `current_state=Draft` with **no version** ([services/objectives/service.py:108-156](apps/api/src/easysynq_api/services/objectives/service.py:108); `create_document` sets Draft at [services/vault/service.py:188](apps/api/src/easysynq_api/services/vault/service.py:188)).
- **The commitment fields** on the satellite: `target_value (Numeric)`, `unit (Text)`, `direction (enum)`, `due_date (Date)`, `at_risk_threshold (Numeric|null)`, `baseline_value (Numeric|null)`, `policy_id (UUID|null)`. `current_value (Numeric|null)` is the **operational rollup, outside the version** (latest-period-wins from append-only `KPI_READING` records) — **not** part of the commitment freeze.
- **`_objective` serializer** ([api/objectives.py:100-148](apps/api/src/easysynq_api/api/objectives.py:100)): all numerics are decimal **strings** except `pct_toward_target` (JSON `number|null`); `current_state` exposed; `rag`/`attainment`/`pct_toward_target` computed at read from `domain/objectives/rules.py`. `Objective` does **not** currently expose `capabilities` or `effective_from`.
- **`document_version`** carries `source_blob_sha256` (NOT NULL, FK→blob, RESTRICT) + `metadata_snapshot` (JSONB, NOT NULL) + `version_state` + `author_user_id`. New version born `version_state=Draft` at check-in.
- **The lifecycle services** are all in place and kind-agnostic: `submit_review` (T2, asserts the latest version is Draft, requires ≥1 clause mapping — the OBJ auto-maps at create) ([services/vault/lifecycle.py:181-209](apps/api/src/easysynq_api/services/vault/lifecycle.py:181)); `instantiate_approval` (DOCUMENT-typed instance + APPROVE task) ([services/workflow/service.py:42-86](apps/api/src/easysynq_api/services/workflow/service.py:42)); the DOCUMENT `decide` (`approve`→`lifecycle.approve` T4 + `approval` signature; `changes_requested`/`reject`→`request_changes` T3, no signature) ([services/workflow/service.py:143-236](apps/api/src/easysynq_api/services/workflow/service.py:143)); `release` (own SERIALIZABLE session, `_cutover` demote-prior-before-promote, `release` signature, first-release trivial) ([services/vault/lifecycle.py:438-585](apps/api/src/easysynq_api/services/vault/lifecycle.py:438)).
- **`_NON_RENDERABLE_PREFIXES`** already lists `application/json` ([render_gotenberg.py:40](apps/api/src/easysynq_api/services/vault/render_gotenberg.py:40)).
- **Authz keys** (all pre-seeded, catalog stays 100): `objective.manage`/`objective.read`/`kpi.record`/`kpi.read` (PROCESS finest-scope, `sod_sensitive=false`, `sig_hook=false`) ([0004_seed_authz.py:95-100](migrations/versions/0004_seed_authz.py:95)); `document.submit`/`document.review`/`document.approve` (ARTIFACT, `sod_sensitive=true`; approve `sig_hook=true`); `document.release` (ARTIFACT, `sig_hook=true`, **held by no role** — SYSTEM-override in v1). `objective.manage` is **QMS-Owner-only**.
- **The POL singleton:** `POL` is seeded as a `document_type` (`is_singleton=true`) only — **no POL document instance is seeded**; an install starts with zero Effective POL until one is authored+released. `current_effective_policy(session, org_id)` exists service-side ([services/objectives/service.py:48-65](apps/api/src/easysynq_api/services/objectives/service.py:48)) but is **unexposed** by any route.

---

## s2 · Backend — the commitment freeze

### s2.1 · Extend the shared `_snapshot` (one optional kwarg)

In [services/vault/service.py:93](apps/api/src/easysynq_api/services/vault/service.py:93):

```python
def _snapshot(
    doc, *, field_schema=None, distribution=None, objective_commitment=None,
) -> dict[str, Any]:
    snap = { ... }  # unchanged base fields
    if field_schema is not None:
        snap["field_schema"] = field_schema
    if objective_commitment is not None:
        snap["objective_commitment"] = objective_commitment
    return snap
```

Ordinary documents and forms remain byte-identical (the kwarg defaults to None and adds nothing). **Do not branch on doc kind.**

### s2.2 · `checkin_objective_commitment` (the freeze)

A new vault-service function alongside `checkin_form_schema` ([services/vault/service.py:513](apps/api/src/easysynq_api/services/vault/service.py:513)). It receives a **pre-built `commitment` dict** (built by the objectives service from the already-loaded satellite — so the vault service stays agnostic of the `quality_objective` model), and it mints the version but **does NOT commit** (unlike `checkin_form_schema`, which commits internally), because the objective freeze is a *sub-step* of submit and must share the submit transaction:

```python
async def checkin_objective_commitment(
    session, sink, actor, doc, *, commitment: dict[str, Any], change_reason, change_significance,
) -> DocumentVersionModel:
    # 1. INV-3 gate: non-empty change_reason + significance ∈ {MAJOR, MINOR}. (mirror form path)
    # 2. Canonical-serialize + content-address the SAME dict that goes into the snapshot.
    payload = rfc8785.dumps(commitment)
    sha = hashlib.sha256(payload).hexdigest()
    # 3. Promote to WORM iff the blob is new (mirror checkin_form_schema:558-583 exactly):
    #    put_staging_bytes(payload, sha, content_type="application/json") → finalize_worm(sha)
    #    → assert promoted.exists (500) + retain_until (423) → pg_insert(Blob, mime="application/json",
    #      worm_locked=True, ...).on_conflict_do_nothing(["sha256"]) → flush
    # 4. Mint the Draft version with the SAME commitment object in the snapshot:
    version = DocumentVersionModel(
        ..., version_state=VersionState.Draft, source_blob_sha256=sha,
        metadata_snapshot=_snapshot(doc, objective_commitment=commitment, distribution=dist_snap),
        author_user_id=actor.id, created_by=actor.id,
    )
    session.add(version)
    _emit(session, sink, "CHECKIN", actor, "document_version", version.id, identifier=doc.identifier, reason=...)
    await session.flush()      # NOT commit — the submit service owns the txn boundary
    return version
```

- **bytes ≡ snapshot:** `payload` and `metadata_snapshot["objective_commitment"]` derive from the *same* `commitment` dict — they can never diverge (the S-rec-3 invariant).
- **WORM-before-commit:** `finalize_worm` promotes the bytes to the documents bucket (durable in MinIO) before the PG commit; a rolled-back txn leaves an orphan content-addressed object that dedups on retry — exactly the form path's contract.

### s2.3 · The canonical commitment dict

`_objective_commitment_dict(qo)` lives in the **objectives** layer (`services/objectives/` or `domain/objectives/`) — the submit service builds it from the loaded satellite and passes it down. It produces a deterministic, JSON-safe ordered dict (rfc8785 sorts keys, but we keep the shape explicit + stable). **Decimals serialize as strings** (mirroring the `_objective` serializer — never float, to keep the WORM bytes exact):

```python
{
  "target_value":      str(qo.target_value),
  "unit":              qo.unit,
  "direction":         qo.direction.value,          # "HIGHER_IS_BETTER" | "LOWER_IS_BETTER"
  "due_date":          qo.due_date.isoformat(),
  "at_risk_threshold": str(qo.at_risk_threshold) if qo.at_risk_threshold is not None else None,
  "baseline_value":    str(qo.baseline_value) if qo.baseline_value is not None else None,
  "policy_id":         str(qo.policy_id) if qo.policy_id is not None else None,
}
```

`current_value` is **excluded** (operational, outside the version). The frozen `policy_id` records the consistency link as it stood at approval.

---

## s3 · Backend — lifecycle endpoints (objectives router)

New endpoints in [api/objectives.py](apps/api/src/easysynq_api/api/objectives.py). Plain sub-paths (mirroring `/documents/{id}/submit-review`, `/documents/{id}/release` — **no colon syntax**). `/objectives/policy` is a static route registered **before** `/objectives/{objective_id}`.

### s3.1 · `POST /objectives/{objective_id}/submit-review` — gate `objective.manage`

Dependency `require("objective.manage", async_scope_resolver=_objective_scope)`. Handler loads the OBJ `for_update=True` + `populate_existing=True` (the authz resolver already `session.get`-loaded it — the S-obj-1 / S-drift-1 stale-identity-map trap), then calls a new service `submit_objective_for_review`:

```python
async def submit_objective_for_review(session, vault_sink, sig_sink, actor, doc):
    # doc is the documented_information row, loaded for_update + populate_existing.
    if doc.current_state is not DocumentCurrentState.Draft:
        raise ProblemException(409, "conflict", "Objective is not in Draft")   # v1: Draft-only
    # Freeze a new version IFF none exists yet (first submit). A re-submit after request_changes
    # advances the existing latest Draft version unchanged (no commitment-edit path in v1, so the
    # working commitment cannot have diverged — re-freezing identical bytes is pointless).
    if await repository.latest_version(session, doc.id) is None:
        qo = await load_quality_objective(session, doc.id)         # objectives-layer getter
        commitment = _objective_commitment_dict(qo)                # s2.3, objectives layer
        await checkin_objective_commitment(session, vault_sink, actor, doc, commitment=commitment,
            change_reason="Objective commitment submitted for review", change_significance="MAJOR")
    result = await submit_review(session, actor, doc)         # T2: Draft → InReview
    await instantiate_approval(session, result.doc, actor)    # DOCUMENT-typed instance + APPROVE task
    audit_transition(session, vault_sink, result, actor)      # mirror documents.py:1457-1460
    await session.commit()
    return result.doc
```

One transaction: freeze (flush) + T2 + approval instantiation + audit, exactly like the document submit endpoint ([api/documents.py:1452-1460](apps/api/src/easysynq_api/api/documents.py:1452)). Response: the `_objective(...)` serialization (now `current_state=InReview`).

- **SoD anchor:** the version's `author_user_id` = the submitter, so the DOCUMENT decide leg's SoD (`author≠approver`) blocks the submitter from approving their own objective.
- **Clause-gate:** `submit_review` requires ≥1 clause mapping — satisfied (OBJ auto-maps to 6.2 at create).
- **Approval pool:** `instantiate_approval` resolves the pool from the `document_approval` workflow definition's first-stage roles. In the demo (SYSTEM overrides) the pool may be empty → instance `NEEDS_ATTENTION`, but the task is still created and a PEP-authorized approver can decide it. *(Note: which role approves an objective vs a generic document is a future refinement — management-review approval is out of scope, see s11.)*

### s3.2 · `POST /objectives/{objective_id}/release` — gate `document.release`

Mirrors the document release endpoint ([api/documents.py:1464-1482](apps/api/src/easysynq_api/api/documents.py:1464)). The gate is `document.release` (with `sig_hook=True` + the SoD-2 overlay via `enrich_release_sod_scope`), enforced imperatively in-handler (not a `require(...)` dependency, because the scope needs enrichment). Then call the existing `release(actor, objective_id, sink, sig_sink, version_id=None)` ([services/vault/lifecycle.py:553](apps/api/src/easysynq_api/services/vault/lifecycle.py:553)) — it is self-contained (its own SERIALIZABLE session, the `_cutover`, the mirror+ack enqueues). The OBJ flows through `_cutover` kind-agnostically; first release is trivial (no prior Effective). Response: the released `_objective(...)` (`current_state=Effective`).

- **Why `document.release` and not `objective.manage`:** release is the org-level, SoD-2-gated cryptographic act; it ships SYSTEM-override-only for documents and stays so for objectives (consistent, no new key). In the demo it's exercised via a SYSTEM override on the live `app_user` row.
- **6.2 ★:** the `_cutover` sets `current_effective_version_id` → the checklist's 6.2 node reads COVERED on the next request (D-E).

### s3.3 · `GET /objectives/{objective_id}/approval` — gate `objective.read`

Mirrors `get_document_approval_endpoint` ([api/workflow.py:310-337](apps/api/src/easysynq_api/api/workflow.py:310)) but gated `objective.read` (so the objective *owner* — QMS Owner, who may not hold `document.read` — can render the detail-page stepper). Queries `latest_instance_for_subject(session, org_id, WorkflowSubjectType.DOCUMENT, obj.id)` (DOCUMENT, because `instantiate_approval` hardcodes it). Returns the `_instance(instance, tasks)` shape (the same `WorkflowInstance` the doc page consumes) or `null` for a never-submitted Draft. Lives in the objectives router (gated `objective.read` via `_objective_scope`).

### s3.4 · `GET /objectives/policy` — gate `objective.read`

Returns the Effective POL singleton for the create modal's policy picker:

```python
@router.get("/objectives/policy")   # registered BEFORE /objectives/{objective_id}
async def get_policy_endpoint(... _objective_read dependency ...):
    pol = await current_effective_policy(session, caller.org_id)   # the unexposed resolver, now exposed
    if pol is None:
        return None
    return {"id": str(pol.id), "identifier": pol.identifier, "title": pol.title}
```

Returns `200 null` (not 404) when no Effective POL exists, so the modal degrades calmly ("No effective Quality Policy yet — link later"). Contract change → `openapi.yaml` entry + `/check-contracts`.

---

## s4 · Backend — serializer additions

### s4.1 · `_objective` gains optional `capabilities` + `effective_from` (detail-only)

`_objective(qo, *, plans=None, capabilities=None, effective_from=None)`. The **list** (`GET /objectives`) and **scorecard** pass neither (so those fields are absent/null in lists — no per-row authz cost). Only `GET /objectives/{id}` passes them:

- `capabilities: {submit: bool, release: bool}` — computed by a new `_objective_capabilities(session, caller, doc)` mirroring `_document_capabilities` ([api/documents.py:414-441](apps/api/src/easysynq_api/api/documents.py:414)):
  - `submit` = `authorize(grants("objective.manage"), "objective.manage", _objective_scope(doc), ctx).allow`
  - `release` = `authorize(grants("document.release"), "document.release", enrich_release_sod_scope(...), ctx, sig_hook=True, sod=...)` (the SoD-2-aware release capability)
- `effective_from: str | null` — from the current Effective version (ISO date), for the `ApprovalStepper`.

The FE combines caps with state (mirroring `AuthorActions`/`ApprovalsTab`): Submit shown when `state ∈ {Draft} && caps.submit`; Release shown when `state === "Approved" && caps.release`.

### s4.2 · The `objective_commitment` is read by the FE approver card from the existing `_version` serializer

No serializer change for F-3: `_version` already returns `metadata_snapshot` ([api/documents.py:232](apps/api/src/easysynq_api/api/documents.py:232)) under `document.read`. The FE `ObjectiveCommitmentContext` reads `useDocumentVersions(docId)` (already fetched by the DOCUMENT decide leg) and pulls `versions[0].metadata_snapshot.objective_commitment`.

---

## s5 · Authz summary (no new keys; catalog stays 100)

| Action | Gate key | Where | SoD / sig_hook |
|---|---|---|---|
| Submit objective for review (freeze + submit) | `objective.manage` | `POST /objectives/{id}/submit-review` (`_objective_scope`) | none (the *version author* is recorded for the downstream SoD) |
| Approve | `document.approve` | existing `POST /tasks/{id}/decision` DOCUMENT leg | `sig_hook=True`, SoD author≠approver |
| Release | `document.release` | `POST /objectives/{id}/release` (`enrich_release_sod_scope`) | `sig_hook=True`, SoD-2 author≠releaser / approver≠releaser |
| Read approval cycle (stepper) | `objective.read` | `GET /objectives/{id}/approval` | — |
| Read Effective POL (create modal) | `objective.read` | `GET /objectives/policy` | — |
| Approver-context commitment read | `document.read` | existing `GET /documents/{id}/versions` (the approver holds it) | — |

**Demo (live smoke):** grant the LIVE `demo` `app_user` row (org **AHT**) SYSTEM overrides for `objective.manage` + `document.submit`/`document.approve`/`document.release` + `document.read`/`document.read_draft` (the content-read overrides are likely already present from prior smokes). `localhost` only; the owner performs the Keycloak login.

---

## s6 · Migration

**None. Head stays `0049` (next `0050`).** Rationale: the freeze adds a JSONB key (`objective_commitment`) to the existing `metadata_snapshot` column; the OBJ version reuses `document_version`; the lifecycle rides existing keys (no new permission key, no role-grant seed — F-1's `objective.manage` submit avoids it); R2 is closed (no new signature meaning). Confirm `alembic check` is clean (it should be — no schema delta).

---

## s7 · Contracts (`packages/contracts/openapi.yaml`)

New paths to document (redocly-lint, not codegen): `POST /objectives/{objective_id}/submit-review`, `POST /objectives/{objective_id}/release`, `GET /objectives/{objective_id}/approval`, `GET /objectives/policy`. Extend the `Objective` schema with optional `capabilities` + `effective_from`. Run `/check-contracts`.

---

## s8 · Front-end (apps/web)

### s8.1 · `Objective` type + MSW

- `lib/types.ts`: add `capabilities?: { submit: boolean; release: boolean }` and `effective_from?: string | null` to `Objective`. Pin against the as-built serializer (`satisfies`).
- `test/msw/handlers.ts` (objectives block ~1050-1080): add handlers for the four new endpoints; add an `objectiveDetailFixture` with `capabilities`; add an OBJ approval `WorkflowInstance` fixture (`subject_type: "DOCUMENT"`) + a pending version fixture carrying `metadata_snapshot.objective_commitment`; an OBJ approval **task** fixture for the `/tasks` leg.

### s8.2 · Objective detail page lifecycle affordances

`features/objectives/ObjectiveDetailPage.tsx` gains, gated per s4.1 + the reused components:
- **Submit-for-review** button: shown when `o.current_state === "Draft" && o.capabilities?.submit`. New hook `useSubmitObjectiveForReview()` → `POST /objectives/{id}/submit-review`.
- **Approvals stepper:** reuse `ApprovalStepper` *as-is* (its `docState` accepts `ObjectiveState` — the identical 7-state union; `effectiveFrom`/`nameOf` are plain inputs). Fed by a new `useObjectiveApproval(id)` → `GET /objectives/{id}/approval`.
- **Release** button: shown when `o.current_state === "Approved" && o.capabilities?.release`. New hook `useReleaseObjective()` → `POST /objectives/{id}/release`.
- The `current_state` badge stays; the page now tells the lifecycle story (Draft → … → Effective).

### s8.3 · `/tasks` approver context (F-3)

`features/review/ReviewApprovePage.tsx` DOCUMENT leg: when the subject document is an objective, render `ObjectiveCommitmentContext` instead of `VersionCompare`. **Detection:** the version's `metadata_snapshot.objective_commitment` is present (forms have `field_schema`, ordinary docs neither) — render the commitment card off `versions[0].metadata_snapshot.objective_commitment` (no new fetch; `useDocumentVersions(docId)` already runs). The `DecisionCard subjectType="DOCUMENT"` is unchanged. *(Verify the detection keys cleanly — see s10 risk.)*

### s8.4 · Create-modal policy picker

`features/objectives/NewObjectiveModal.tsx`: add an optional **Quality Policy** select backed by `useEffectivePolicy()` → `GET /objectives/policy`. When `null`, show a calm "No effective Quality Policy yet" and submit `policy_id: null`. When present, the single option is the Effective POL `{identifier — title}`; selecting it sends its `id`.

---

## s9 · Testing strategy

**Local gates (this Windows box):** web (`/check-web`: eslint + strict `tsc` + build + vitest) and the api static checks (`/check-api`: ruff/format/mypy). The api unit+integration suites and `/check-migrations` are Linux-CI gates; verify backend behaviour via the worker-container heredoc smoke + the live stack, not local pytest. `/check-contracts` (openapi changed).

- **API unit:** `_objective_commitment_dict` canonical shape (decimals-as-strings, None handling); `_snapshot` adds `objective_commitment` only when passed and leaves ordinary/form snapshots byte-identical; the submit service's Draft-only guard + the freeze-iff-no-version logic; `_objective_capabilities` gating.
- **API integration** (testcontainers; **run-scoped / delta-based assertions** — the shared session DB is dirty): create→submit→approve→release an OBJ; assert `current_state=Effective`, exactly one `document_version` (Effective) with `metadata_snapshot.objective_commitment` matching the committed values + the `application/json` WORM source blob; assert `signature_event` rows `approval` + `release` (and that the submitter is SoD-blocked from approving — author≠approver); assert the **6.2 checklist node flips COVERED** for *this run's* objective (scope the assertion to the created clause mapping / a before→after delta, never a global count); assert `GET /objectives/{id}/approval` returns the instance; `GET /objectives/policy` returns the Effective POL or null. Prove the **two-session populate_existing** trap (prime via `session.get`, mutate via session B, locked-load on A) for the submit handler's `for_update` load.
- **Web:** +tests for the three affordances + the create-modal policy picker + the `ObjectiveCommitmentContext` card + the new hooks/MSW. Every component test using a jest-dom matcher must `import { expect, it } from "vitest"` (the tsc-only trap). Conditionally render any modal in its parent (`{open && <Modal/>}`) + a reopen-resets test. Baseline **659** → target delta tracked in the slice-history entry.
- **Pre-merge live smoke:** backend via the worker heredoc (create→submit→approve→release→assert Effective + frozen snapshot + 6.2 COVERED); FE via Chrome MCP (grant the SYSTEM overrides from s5 to the live `demo` row; `docker compose ... up -d --build` if the backend changed — `--build` goes on the raw compose command, **not** `just up s`; the owner does the Keycloak login).
- **diff-critic** on the branch diff before the PR (WORM/append-only + snapshot-cache + authz invariants). Triage the Codex PR review (disregard multi-tenant nitpicks moot under D1; fix genuine bugs).

---

## s10 · Risks / traps to carry in

- **`populate_existing` on the submit `for_update` load** — the authz resolver pre-`session.get`s the OBJ; a bare `with_for_update()` returns stale identity-map attrs. Add `.execution_options(populate_existing=True)`; prove with a two-session test.
- **Re-freeze on re-submit** — without the "freeze iff no version" guard, a request_changes→re-submit cycle would mint a duplicate identical version. Guard on `latest_version is None`.
- **Decimal-as-string in the canonical blob** — serialize Numerics as strings (never float) so the WORM bytes are exact + reproducible; rfc8785 over a float would embed a lossy repr.
- **F-3 detection** — `ObjectiveCommitmentContext` must key on `metadata_snapshot.objective_commitment` presence, not the document type (the FE may not see the type). Verify forms (`field_schema`) and ordinary docs (neither) don't collide.
- **Route ordering** — `/objectives/policy` before `/objectives/{objective_id}` (the `scorecard` precedent), else the literal is shadowed by the str-convertor param route.
- **MSW fixtures pinned to the real serializer** — copy the `_version`/`_instance`/`_objective` shapes from the api, never the mockup (the recurring #1 false-PASS).
- **Mirror** — once an OBJ is Effective, its `application/json` commitment appears in the on-disk mirror as a source-bytes-only file (`no_controlled_rendition`, R26) — correct (objectives ARE controlled documents); no garbage CONTROLLED COPY.

---

## s11 · Honest deferrals (named, not faked)

- **Commitment-revision-edit** (Effective objective → UnderRevision → edit target/threshold → re-freeze → re-approve → re-release). The `_cutover` mechanism supports it; v1 ships **first-release only** (no commitment-edit endpoint exists, so there is nothing to revise). When it lands, also switch Effective-objective reads to the frozen snapshot (F-2's deferred half).
- **Management-review approval routing** — the objective rides the generic `document_approval` workflow definition's approver pool; ISO-6.2 "approved by top management" routing is a future refinement.
- **Process-Owner `objective.manage`** (QMS-Owner-only in v1); per-process objective reads (SYSTEM-gated until owner-assignment lands); KPI trend charts (N6); the §9.3 Management-Review dashboard (its input entities mostly don't exist yet).

---

## s12 · Build sequence (for the implementation plan)

1. **Freeze core** — `_snapshot` kwarg + `_objective_commitment_dict` + `checkin_objective_commitment` + the `repository.get_quality_objective`/`latest_version` getters; unit tests (canonical shape, snapshot non-branching).
2. **Submit + release services/endpoints** — `submit_objective_for_review` + the two endpoints (`objective.manage` submit, `document.release` release); integration test the full create→submit→approve→release→Effective path + 6.2 COVERED + the SoD block.
3. **Read endpoints** — `GET /objectives/{id}/approval` + `GET /objectives/policy`; `_objective_capabilities` + the detail serializer additions; openapi entries + `/check-contracts`.
4. **FE** — types + MSW; detail-page Submit/stepper/Release; `ObjectiveCommitmentContext`; create-modal policy picker; web tests.
5. **Verify** — `/check-api`, `/check-web`, `/check-contracts`, `/check-migrations` (no-op confirm); diff-critic; live smoke (backend heredoc + Chrome MCP).

After this slice the Quality Objectives family is **lifecycle-complete** and the **6.2 ★ node is genuinely COVERED**.
