# S-risk-1b — the Risk register's controlled-document publish/freeze/release lifecycle (clause 6.1)

> Implementation note for the named S-risk-1 deferral. The model + the WORM/authz invariants are already
> specced and owner-ratified in `2026-06-19-s-risk-register-design.md` (§2 edit model, §3 row-content-as-
> source-blob + read-of-record, §4 criteria derive-and-freeze, §10 names S-risk-1b, D-4 strict
> controlled-document). This note records the two open owner decisions resolved before code + the concrete
> endpoint/flow design. **Mirror the objectives family exactly** (OBJ is a doc-backed managed document with
> a content-aware freeze + lifecycle).

## What S-risk-1 left as the deferral

The RSK register head stays **Draft** (a working register, rows edited freely while Draft); the displayed
band grades against the golden-pinned `default_criteria`. S-risk-1b makes the register a true controlled
**Effective** document: rows become version content, edited through FSM revisions, frozen into immutable
versions at publish, with the live band resolving against the **governing version's frozen criteria**.

## Owner decisions (resolved 2026-06-20)

### D-1b · Approval routing for the RSK doc_type — NO config needed (investigated, not asked)

`workflow/service.instantiate_approval` resolves the **`document_approval`** definition keyed purely by
`(org, "document_approval", WorkflowSubjectType.DOCUMENT)` — it is **doc-type-agnostic**. RSK rides the exact
same single `quality_approval` stage (`assignees={"roles":["Approver","QMS Owner"]}`, mode SEQUENTIAL,
quorum ANY, author excluded; seeded in `0009`) that ordinary documents, OBJ, and MR already ride. An empty
candidate pool → `NEEDS_ATTENTION` (the task is still created so a PEP-authorized approver can decide).
**RSK needs no routing config.** The approval decision rides the generic `POST /tasks/{id}/decision`
(DOCUMENT leg).

### D-2b · Head-steward gate = `register.manage` @ SYSTEM (owner-ratified)

The RSK head is org-wide (zero `ProcessLink`s — the L1 invariant), so start-revision + publish are
**SYSTEM-scoped** acts. Gate them on **`register.manage`** at SYSTEM scope (`require("register.manage")`'s
default `_system_scope`). The **QMS Owner** holds `register.manage` @ SYSTEM → stewards the register
directly, no override needed. A bound Process-Owner's **PROCESS** `register.manage` grant does **not** match
the org head's SYSTEM scope → 403 (a process owner contributes rows within an open revision window, never
publishes the org register unilaterally — D-4's consequence). This mirrors the OBJ **surface-key**
precedent (`api/objectives.py` gates start-revision/submit on `objective.manage`, not `document.*` — the
F-1 asymmetry: the QMS Owner holds no `document.create/edit/submit`). The spec §2's "SYSTEM-scoped
`document.*` act / rides a SYSTEM override" framing is reconciled to the OBJ-faithful surface-key gate.

**Release** stays on **`document.release`** @ SYSTEM with `sig_hook=True` + the SoD-2 release-scope overlay
(author/approver ≠ releaser) — held by no seeded role → a SYSTEM override in v1, exactly like OBJ. A
register-steward role/UI for the org-head lifecycle remains the named v1.x follow-up.

### D-3b · Fold the managed-doc generic-endpoint reservation INTO this slice (owner-ratified)

Reserve the **OBJ/MR/RSK** heads from the generic mutation endpoints that the S-risk-1 round-3 trim left
open: `PATCH /documents/{id}` (metadata), `POST|DELETE /documents/{id}/distribution`, and
`POST|DELETE /documents/{id}/links`. The existing `reject_objective_byte_path` already guards all three
subtypes (OBJ PK-probe + MR PK-probe + the RSK `document_type=RSK` check) — add the call after the
`_load_document` in each endpoint. (process-link add, checkout/checkin, generic submit-review, generic
start-revision are already reserved.) The generic **release** endpoint is intentionally left open: it drives
the same `_cutover` and can only promote a properly-frozen Approved version (the no-freeze paths to
Approved are all reserved), so it is a safe no-op-equivalent.

## The lifecycle flow (mirror OBJ)

```
first register:  add risk rows (head Draft)  →  publish  →  approve  →  release  →  Effective
                                                  │ freeze v1 (rows+criteria)       │ cutover, satellite read-only
revision:        start-revision (Effective→UnderRevision, edit lock + WD)
                 →  edit rows  →  publish (freeze vN+1, drop WD)  →  approve  →  release (supersedes)
```

- **Read-only while Effective** is already enforced by the S-risk-1 edit gate (`add_risk_row`/
  `update_risk_row` 409 unless head ∈ {Draft, UnderRevision}) → the satellite-when-Effective **equals** the
  published snapshot.
- **Single non-Obsolete head, revised in place** — `start_revision` mints a Draft version on the SAME head
  doc row; the generic `_cutover` enforces single-Effective (R25 `is_singleton`); RSK ∉ `LEADERSHIP_DOC_TYPES`
  → the `_cutover` leadership gate is a verified no-op.

## Scope / files

- **`domain/risk/register_content.py`** (pure, mirrors `domain/objectives/commitment.py`):
  `build_register(*, rows, criteria)` → `{"rows": <sorted by id>, "criteria": <map keyed by scoring_method
  value, for methods present in the rows>}`; `resolve_criteria(governing, scoring_method)` → the frozen
  per-method criteria when a governing snapshot exists (else `default_criteria(scoring_method)` —
  pre-first-release); `register_needs_freeze(*, latest_version_state, latest_register, working)` (the
  `commitment_needs_freeze` switch).
- **`services/vault/service.py`**: `_snapshot(..., risk_register=...)` (one optional kwarg, body never
  branches on kind — the `objective_commitment`/`mgmt_review_minutes` precedent; `risk_register` ∉
  `SNAPSHOT_FIELDS` → invisible to the metadata diff); `checkin_risk_register(...)` (the
  `checkin_objective_commitment` verbatim — rfc8785 JCS → staging-PUT `application/json` → `finalize_worm`
  → `metadata_snapshot.risk_register`, **FLUSH** not commit, `rendition_blob_sha256` left NULL → R26
  `no_controlled_rendition`).
- **`services/risk/lifecycle.py`** (new): `start_register_revision(...)` (head FOR UPDATE+populate_existing
  → vault `start_revision`); `publish_register(...)` (the `submit_objective_for_review` shape: gate
  Draft/UnderRevision → build working register → `register_needs_freeze` → `checkin_risk_register` → drop the
  start-revision WorkingDraft (O-4) → `submit_review` → `instantiate_approval` → `audit_transition` →
  commit → release the edit lock).
- **`services/risk/queries.py`** (new): `governing_register(session, org_id)` → the head's current Effective
  version's `metadata_snapshot["risk_register"]` (or None pre-first-release).
- **`api/risk.py`**: `POST /risks/register/start-revision` + `POST /risks/register/publish` (both
  `require("register.manage")` @ SYSTEM); `POST /risks/register/release` (`document.release` @ SYSTEM,
  sig_hook, SoD-2, calls the shared `release()` — the OBJ release endpoint shape); `GET /risks/register`
  (the head status: state + `current_effective_version_id` + `has_governing` + identifier). The `_risk`
  serializer grades the band via `resolve_criteria(governing, row.scoring_method)` (governing fetched once
  per list/get request) — replacing the S-risk-1 `default_criteria(row.scoring_method)`.
- **`api/documents.py`**: the D-3b reservation (4 endpoint families).
- **`packages/contracts/openapi.yaml`**: the new `/risks/register/*` endpoints (in-PR).

## No migration

The `risk_opportunity` model + RSK doc_type + `RISK_RESCORED` event all exist (0058). The publish lifecycle
rides existing audit events (`SUBMITTED_FOR_REVIEW`/`CHECKIN`/`REVISION_STARTED`/`RELEASED`/`SUPERSEDED`),
no new `SignatureMeaning`, no schema (the snapshot uses the existing `metadata_snapshot` JSONB). **Head stays
`0058`; catalog stays `102`; no new permission key.**

## Carry the traps

- `reject_objective_byte_path` already rejects the RSK head → the **publish path bypasses the reserved
  generic byte endpoints**, calling `checkin_risk_register` + `submit_review` + the generic `release()`/
  `_cutover` service fns directly (like objectives).
- `populate_existing` on every FOR-UPDATE locked load (the S-drift-1 stale-identity-map trap).
- ruff strips just-added imports (body-first). `-m integration` is CI-authoritative on the Windows box.
- Codex is THE authz/WORM edge-finder (8 findings + 3 rounds on S-risk-1) — expect ≥2 rounds.
