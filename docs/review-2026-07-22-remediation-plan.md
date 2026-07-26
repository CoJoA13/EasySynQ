# Remediation plan — 2026-07-22 full review (MAJOR findings)

> Working tracker for the **50 MAJOR** findings from [`review-2026-07-22.md`](./review-2026-07-22.md).
> The 3 CRITICALs are already merged (CR-1 #350, CR-3 #351, CR-2 #352). This doc groups the MAJORs
> into **PR-sized batches** and tracks progress so we can check them off across sessions.

## Conventions

- **One branch + one PR per batch** (same flow as the criticals: branch off `main` → green CI →
  adversarial review → squash-merge). Suggested branch name is listed per batch.
- Each batch's PR **checks off its own box here** (edit this file in that PR) so `main` always shows
  live status. Set the batch checkbox `[x]` and fill the **PR** column when the PR merges.
- Findings reference the code location from the review doc; open `review-2026-07-22.md` for the full
  failure scenario + fix rationale. `[C]` = CONFIRMED / CONFIRMED* (hand-verified in review), `[f]` =
  finder-only (reported, not yet independently reproduced — verify against source before fixing).
- Order is by priority: security / WORM / authz / data-integrity first, then correctness, then
  contract / infra / web / tests / docs. Pick any batch; the recommended lead is **Batch 1**.

## Status at a glance

| # | Batch | Tier | Findings | Status | PR |
|---|-------|------|:--------:|--------|----|
| 1 | Stale FOR-UPDATE reads (`populate_existing`) | 1 | 4 | ☑ in PR | [#354](https://github.com/CoJoA13/EasySynQ/pull/354) |
| 2 | Deny-wins scope-tuple completeness | 1 | 2 | ☑ in PR | [#355](https://github.com/CoJoA13/EasySynQ/pull/355) |
| 3 | System-tier authz guards (last-admin / revoke-side) | 1 | 2 | ☑ in PR | [#356](https://github.com/CoJoA13/EasySynQ/pull/356) |
| 4 | WORM erasure completeness | 1 | 2 | ☑ in PR | [#357](https://github.com/CoJoA13/EasySynQ/pull/357) |
| 5 | Disposition txn / locking integrity | 1 | 2 | ☑ in PR | [#358](https://github.com/CoJoA13/EasySynQ/pull/358) |
| 6 | Read-authorization on returned bodies | 1 | 3 | ☑ in PR | [#362](https://github.com/CoJoA13/EasySynQ/pull/362) |
| 7 | Audit signed-checkpoint verification | 1 | 1 | ☑ in PR | [#364](https://github.com/CoJoA13/EasySynQ/pull/364) |
| 8 | Document lifecycle FSM gates | 2 | 2 | ☑ in PR | [#365](https://github.com/CoJoA13/EasySynQ/pull/365) |
| 9 | Workflow approval correctness | 2 | 3 | ☑ in PR | [#366](https://github.com/CoJoA13/EasySynQ/pull/366) |
| 10 | Ingestion pipeline correctness | 2 | 3 | ☑ in PR | [#367](https://github.com/CoJoA13/EasySynQ/pull/367) |
| 11 | Notifications & operator alerting | 2 | 2 | ☐ not started | — |
| 12 | Contract & schema housekeeping | 3 | 4 | ☐ not started | — |
| 13 | Infra / deploy hardening | 3 | 3 | ☐ not started | — |
| 14 | Web correctness | 3 | 4 | ☐ not started | — |
| 15 | Web a11y & polish | 3 | 4 | ☐ not started | — |
| 16 | Test false-PASS / CI-flake | 3 | 2 | ☐ not started | — |
| 17 | Docs drift | 3 | 7 | ☐ not started | — |

**Total: 50 findings across 17 batches.**

---

## Tier 1 — Security · WORM · authz · data integrity

### ☑ Batch 1 — Stale FOR-UPDATE reads (`populate_existing`) — [#354](https://github.com/CoJoA13/EasySynQ/pull/354)
`branch: fix/major-forupdate-populate-existing` · backend + integration race tests

The recurring S-drift-1 trap: a locking load on a row already in the request session's identity map
takes the lock but returns the **stale** cached attributes, defeating FSM/one-shot guards under a race.

- [x] `services/capa/repository.py:31` — `get_capa(for_update=True)` omits `populate_existing` → duplicate signed CAPA stages / duplicate signature_events `[C]`
- [x] `services/capa/repository.py:53` — `get_ncr(for_update=True)` omits `populate_existing` → one-shot 8.7 disposition gate defeated `[C]`
- [x] `services/audits/repository.py:37` — audit FSM `get_audit`/`get_finding` lack `populate_existing` → finding added to a Closed audit `[f]`
- [x] `services/mgmt_review/compile.py:257` — `compile_inputs` uses `session.get` (no lock) → inputs replaced under the submit-freeze; lock both compile + submit paths `[C]`

Fix pattern: `.execution_options(populate_existing=True)` on each `for_update` branch; prove each with a two-session race test (prime via `session.get` on session A, commit a change via session B, locked-load on A, assert fresh state).

### ☑ Batch 2 — Deny-wins scope-tuple completeness — [#355](https://github.com/CoJoA13/EasySynQ/pull/355)
`branch: fix/major-scope-tuple-write-surfaces` · backend + integration

Sibling of merged #346: a write/dispose gate that builds a partial `ResourceContext` silently drops a FRAMEWORK/kind-scoped DENY (deny-always-wins / R3, unsafe direction).

- [x] `api/records.py:234` — `_record_scope` (all five `record.dispose` gates) builds a partial tuple → FRAMEWORK / kind / **PROCESS**-scoped dispose DENYs dropped; populate kind + framework_id **and process_ids** (via `_record_process_scope`) unless the S-records-W DENY-direction rationale is re-affirmed and documented (review doc 114-117) `[C]`
- [x] `api/documents.py:819` — `POST /documents` builds `document.create` + per-link `manage_metadata` scopes without kind/framework_id → create-surface DENY dropped `[C]`

### ☑ Batch 3 — System-tier authz guards — [#356](https://github.com/CoJoA13/EasySynQ/pull/356)
`branch: fix/major-authz-system-tier-guards` · backend + integration

- [x] `api/authz.py:309` — `revoke_user_role` has no last-System-Administrator constraint → self-hosted lockout; serialize the count+mutation under ONE org-scoped lock spanning revoke AND user-deactivation `[C]`
- [x] `api/authz.py:427` — `delete_user_override` / `revoke_user_role` apply no two-tier guard → a content-tier grantor can re-enable/strip system-domain access; route the denial through the AUDITED `_two_tier_deny` `[C]`

### ☑ Batch 4 — WORM erasure completeness — [#357](https://github.com/CoJoA13/EasySynQ/pull/357)
`branch: fix/major-worm-erasure-completeness` · backend + migration

- [x] `services/records/disposition.py:128` — DESTROY / R27 WORM-destroy never nulls `form_field_values` → structured record content survives legal erasure `[C]`
- [x] `migrations/versions/0024_records_disposition.py:179` — `disposition_event` is UPDATE/DELETE-able by the app role → REVOKE UPDATE,DELETE to match the sibling append-only tables `[f]` (fixed in new migration **0072**)

### ☑ Batch 5 — Disposition txn / locking integrity — [#358](https://github.com/CoJoA13/EasySynQ/pull/358)
`branch: fix/major-disposition-txn-integrity` · backend + migration + integration

- [x] `services/records/disposition.py:92` — two concurrent dispositions of records sharing one blob each see the peer live → shared bytes never purged; lock `blob.sha256` before the liveness check `[C]`
- [x] `services/records/disposition.py:94` — purge deletes S3 bytes before the single end-of-run commit → a failed commit orphans bytes for the whole run; purge-LAST (commit tombstone + blob-row-delete + a `pending_blob_purge` marker FIRST, then purge idempotently) + a reaper (marker table = migration **0073**; owner chose the faithful marker+reaper over the leaner no-migration variant) `[C]`

### ☑ Batch 6 — Read-authorization on returned bodies — [#362](https://github.com/CoJoA13/EasySynQ/pull/362)
`branch: fix/major-read-auth-returned-bodies` · backend + integration

A create-gated (or token-gated) endpoint returns a resource body the caller cannot actually read.

- [x] `api/capa.py:614` — spawn-capa idempotent replay returns another process's CAPA header to a caller gated only on a caller-chosen `capa.create` scope; re-authorize the returned CAPA with `capa.read` at its own scope `[C]` (fixed: `capa.read` enforce on the `created=False` replay branch at the CAPA's own PROCESS scope)
- [x] `services/packs/build.py:304` — pack FINDING/CAPA subjects are serialized with NO subject read-check (`record.read` gates only the evidence candidates) → R28 bypass; ADD a per-subject `capa.read`/`finding.read` gate at the subject's own scope (not via the evidence classifier) and refuse/exclude when unreadable `[f]` (fixed: `_authorize_pack_subjects` refuse-any 403 at create — build is worker-async — mirroring each subject's own read surface)
- [x] `services/packs/service.py:620` — public pack share survives a WORM destroy (cached portfolio PDF keeps serving); disposition must invalidate share tokens + purge derived artifacts + fail-closed on disposition state `[f]` (fixed: serve-time `pack_has_destroyed_member` fail-closed on `resolve_share_token` [public 403] + the authenticated download [409]; the **physical purge / share-token invalidation** of the derived ZIP/portfolio artifacts is a genuine R27-vs-doc-06-§7.4 policy call deferred to fast-follow **[#361](https://github.com/CoJoA13/EasySynQ/issues/361)**)

### ☑ Batch 7 — Audit signed-checkpoint verification — [#364](https://github.com/CoJoA13/EasySynQ/pull/364)
`branch: fix/major-audit-checkpoint-verify` · backend + integration · **heaviest of the tier**

- [x] `tasks/audit.py:59` — nightly/on-demand `verify_chain` never verifies the Ed25519 signature on the checkpoint nor does an independent off-host read → a privileged DB owner who rewrites both the chain and the checkpoint row is undetected; verify signature first (separately-trusted key), add an out-of-band off-host verifier, extend the restore drill `[C]` (fixed: `verify_chain(verify_key=…)` Ed25519-verifies the newest checkpoint + compares `latest_row_hash`; the anchor exports a separately-trusted public key; `verify_offhost_checkpoint` reads the off-host copy back with **separate read creds** [beat + a `verify-offhost` CLI]; the restore drill attests the bundled checkpoint's signature+hash. The `integrity.alarm` **notification** emitter stays Batch 11 on top of this `CHAIN_VERIFY_FAIL` detection signal — owner decision)

---

## Tier 2 — Correctness · lifecycle · workflow

### ☑ Batch 8 — Document lifecycle FSM gates — [#365](https://github.com/CoJoA13/EasySynQ/pull/365)
`branch: fix/major-doc-lifecycle-gates` · backend + integration

- [x] `services/vault/service.py:396` — `checkout`/`checkin` are not FSM-gated → a check-in during InReview permanently bricks the doc + its approval task; gate on `current_state in {Draft, UnderRevision}` `[C]` (fixed: `_require_editable_state(doc)` gates the generic `checkout()`/`checkin()` on `_EDITABLE_STATES` = {Draft, UnderRevision} → 409 `not_editable`, after `reject_objective_byte_path` so managed subtypes 422 first)
- [x] `api/documents.py:1703` — generic `POST /documents/{id}/release` skips the managed-subtype hooks → a generically-released MR is permanently unclosable (and OBJ unit-reset skipped); route managed subtypes through their post-release chain `[C]` (fixed: `release_endpoint` now calls `reject_objective_byte_path(session, doc)` → a managed subtype 422s toward its own `/management-reviews` / `/objectives` release, which runs the post-release chain [MR `spawn_mr_actions` + `close_state=ActionsTracked`])

### ☑ Batch 9 — Workflow approval correctness — [#366](https://github.com/CoJoA13/EasySynQ/pull/366)
`branch: fix/major-workflow-approval-correctness` · backend + integration

- [x] `services/capa/service.py:641` — `decide_capa_action_plan` has no outcome allow-list → a non-`approve` positive outcome mints a false WORM `signature_event(meaning='approval')`; add `_ALLOWED_CAPA_OUTCOMES` + 422 before `engine.decide` `[C]` (fixed: `_ALLOWED_CAPA_OUTCOMES = {approve, reject, changes_requested}` + a 422 `unsupported_outcome` before `engine.decide`, so `verify`/`complete`/`acknowledge` can no longer drive the ANY quorum to MET and seal a false approval signature into the append-only `capa_stage`)
- [x] `services/dcr/service.py:713` — `decide_dcr_approval` passes non-approve positive outcomes through → permanently bricks the DCR; add `_ALLOWED_DCR_OUTCOMES` + 422 `[C]` (fixed: same allow-list shape; the stale comment claiming the engine's `TaskOutcomeKind` check covered this is corrected — `verify` IS a legal kind, so it completed the instance while matching neither FSM branch and left the DCR stuck `InApproval`)
- [x] `services/workflow/engine.py:508` — stage advance drops the definition `default_sla` → 2nd-tier approval tasks get `due_at=null` (no reminders/overdue/escalation); load the definition and pass `default_sla` to `_enter_stage` `[C]` (fixed: `decide` loads `WorkflowDefinition` by `instance.definition_id` and threads `default_sla` into `_enter_stage`, matching `instantiate`; a MAJOR DCR's 2nd-tier QMS-Owner task now inherits the definition's 120h SLA)

### ☑ Batch 10 — Ingestion pipeline correctness — [#367](https://github.com/CoJoA13/EasySynQ/pull/367)
`branch: fix/major-ingestion-correctness` · backend + integration

- [x] `services/ingestion/commit.py:185` — `reconstruct_revision_chain` opt-in (R10) never consumed at commit → silently ignored; implement or reject/warn honestly `[C]` (fixed **honestly, not implemented**: commit logs a structured warning naming the opted-in families and the §12.1 Import Report grows a "Deferred — revision-chain reconstruction (R10)" section stating plainly that members imported as individual Effective documents. Materialization stays a future slice; the opt-in remains recorded on `import_version_family`)
- [x] `services/ingestion/commit.py:296` — `{TYPE}-<new>` sentinel persisted as `legacy_identifier` on freshly-allocated imports → search pollution + collisions; guard on `identifier_source` `[C]` (fixed: the preservation is guarded on `identifier_source not in (None, "suggested_default")`. Reaching that branch means the identifier was non-collidable, and `identifier_source is None ⟹ identifier is None`, so the sentinel was the ONLY value it ever wrote)
- [x] `services/ingestion/service.py:673` — `reap_stalled_runs` FAILs a live, heartbeating pipeline after 6h → large OCR import can never complete; anchor the backstop on stage progress `[C]` (fixed: new `repo.max_stage_progress` (newest `import_extract`/`import_classification` row) anchors the backstop, taking the GREATEST of it and `scan_started_at` — mirroring `reap_stalled_commits`' progress-liveness. Also corrects the docstring that named only 4 of the reaped states)

### ☑ Batch 11 — Notifications & operator alerting
`branch: fix/major-notify-and-alerting` · backend + **migration 0074** (head `0073` → `0074`)

- [x] `services/notifications/render.py:61` — `html.escape` in `_substitute` feeds two PLAIN-TEXT sinks → titles with `& ' < >` garbled in email + double-escaped in the SPA; **drop the escape** (both sinks are plain text; the whitelist regex already blocks slot injection), reserving sink-level escaping only for a future HTML sink `[C]` (fixed: the escape is gone. Slot injection stays impossible for a reason independent of escaping — only whitelisted names substitute at all, and `re.sub` walks the ORIGINAL string, so a value that itself looks like a token is emitted literally and never re-scanned; now pinned by a test)
- [x] `services/backup/service.py:110` — nightly backup failures + chain-verify breaks never notify admins (`system.backup_failed` / `integrity.alarm` class-mapped, no emitter); wire the in-DB path AND an out-of-band operator channel (SMTP/syslog/webhook) for the DB-down mode `[C]` (fixed: **in-DB** `services/notifications/ops_events.py` emits both to the org's System Administrators through the normal delivery machinery — per-class digest mode, quiet hours [`integrity.alarm` is CRITICAL → pierces], the org flag, the per-user opt-out — plus a durable `BACKUP_FAILED` audit row; savepoint-wrapped so a broken notification can never roll back the caller's `CHAIN_VERIFY_FAIL` row. **Out-of-band** `services/notifications/ops_channel.py` adds syslog / SMTP-to-an-ops-mailbox / webhook, opt-in via `OPS_ALERT_CHANNELS` and deliberately DB-free; `run_scheduled_backups`' policy read now alerts out-of-band and re-raises, covering the DB-down mode the in-DB path structurally cannot report. Also **closes the "Audit integrity alarm policy" residual in full** (owner-approved): `AUDIT_WITNESS_REQUIRED` is held in the ENVIRONMENT, not the DB — the row a privileged DB owner deletes to go dark IS `audit_checkpoint_sink` — and `audit_checkpoint_sink.enabled_at` + a grace window make an enabled-but-never-anchoring sink alarm as a dead witness. A missing verify key alarms too, without writing `CHAIN_VERIFY_FAIL` [that event means a tamper was DETECTED; recording a misconfiguration as one would poison the signal])

> ⚠ **Carried forward, owner-acknowledged:** `_run_verify_chain` has no orchestrator-level test coverage (pre-existing since S6 — zero tests — but this batch added the `dirty` commit decision, the missing-verify-key alarm and the engine-inside-`try` move to it). The individual pieces are covered; the orchestration is not, because the task builds its own engine while the integration harness only repoints `get_sessionmaker()`. Full entry + what closing it needs is in `docs/slice-history.md` ⚠ OPEN RESIDUALS.

---

## Tier 3 — Contract · infra · web · tests · docs

### ☑ Batch 12 — Contract & schema housekeeping
`branch: fix/major-contract-schema-housekeeping` · openapi + **migration 0075** (head `0074` → `0075`)

> ⚠ The line numbers recorded below had all drifted since the review (9844→9877, 7743→7769, 7699→7725); each item was re-located and re-confirmed against source before the fix.

- [x] `packages/contracts/openapi.yaml:9877` — `ImportRunStatus` enum missing 4 live states (Reviewing, Committing, Completed, PartiallyCommitted) `[C]` (fixed: all 15 `ImportRunStatus` members are now published in declaration order. The stale "later slices add the commit/complete stages additively" prose is replaced with what actually shipped — S-ing-4's lock-free `Reviewing` rest state, and S-ing-5's `Reviewing → Committing → Completed`, or `PartiallyCommitted` when some items failed, per `services/ingestion/commit.py:602`)
- [x] `packages/contracts/openapi.yaml:7769` — `AuditEvent.object_type` closed enum missing 8 of 16 values `[C]` (fixed: all 16 `AuditObjectType` members published. **Also closed the recurrence class**, at the owner's call: `tests/unit/test_openapi_enum_parity.py` reads the REAL `openapi.yaml` and the REAL Python enums and fails on any divergence, so the ninth `ALTER TYPE … ADD VALUE` reds CI instead of silently re-opening the gap. It also rejects duplicate values, which a plain set-comparison would hide. Mutation-verified three ways — dropping a value from either enum, and duplicating one, each red the guard. This is the only server-truth check the contract has: `redocly lint` only proves the document is well-formed, and the declared `contract`/schemathesis marker is unused)
- [x] `packages/contracts/openapi.yaml:7725` — `DecisionResult` (additionalProperties:false) omits `capa_close_state` / `dcr_state` `[C]` (fixed: both added as `[string, "null"]`, the same `additionalProperties:false` gap S-ack-1 closed for the engine's four always-emitted fields. They are simply ABSENT on decisions of other subject types, which is legal since neither is `required`; emission sites are `services/capa/service.py:615,740` and `services/dcr/service.py:561,735`. Neither is null in practice — `Capa.close_state` is `nullable=False` with a `'Raised'` server_default — but the `"null"` branch is kept as a deliberate forward-compatible superset)
  - **⚠ ALSO fixed, and this line was very nearly a false-PASS:** the `diff-critic` pass found `DecisionResult` still rejected a **reachable 200**. `engine.decide()` has a second early-return that `_response()` never builds — the `_QUORUM_SKIP` arm (`services/workflow/engine.py:429-442`) returning `stage_state: "ALREADY_SATISFIED"` with `outcome: null` and NO `decided_at`/`decided_by`. Against `required: [task_id, instance_id, stage_key, outcome, decided_at, decided_by]` plus a non-nullable `outcome` enum, that is **four** violations on a path any quorum stage reaches: `_materialize_stage` creates one task per candidate, the first decision closes the quorum and marks the siblings SKIPPED, and a sibling approver's POST then returns 200 with that body (asserted today by `tests/integration/test_workflow_engine.py`). **The original source review DID name this** — `docs/review-2026-07-22.md:454`, marked `finder-only` — but it never made it into this batch's checklist, so ticking the line for the two missing *properties* would have recorded the class as closed while the reachable *shape* stayed unrepresentable. Fixed contract-side (the honest direction — no decision was made, so the server must not invent `decided_*`): `required` narrowed to `[task_id, instance_id, stage_key]` and `outcome` made `type: [string, "null"]` with `null` added to the enum (OpenAPI 3.1 requires it explicitly; a nullable type alone does not exempt the enum).
- [x] `db/models/audit_event.py:51` — `scope_ref` (per-document history access path) carries no index; add partitioned btree `(org_id, scope_ref, id)` `[C]` (fixed: migration **0075** creates `ix_audit_event_org_id_scope_ref_id` on the PARTITIONED PARENT, mirrored in `AuditEvent.__table_args__`. Column order is driven by the actual query at `api/audit.py:218` — `WHERE org_id = ? AND scope_ref = ? AND id < :cursor ORDER BY id DESC` — so the two equality columns lead and `id` serves both the keyset range and the ordering; the plan is `Limit → Merge Append → Index Scan Backward` with no Sort. Verified empirically on a throwaway PG16, not assumed: all three existing partitions inherited a child index, and a partition created through `easysynq_create_audit_partition` — what the `roll_partitions` Beat calls — inherited one too, so future months need no rotation-code change. The auto-named children `audit_event_YYYY_MM_*_idx` are already excluded from autogenerate by `env.py::_include_object`'s `audit_event_` prefix rule, while the `ix_`-prefixed parent is not — hence the ORM mirror, and hence **no `_MIGRATION_MANAGED_INDEXES` entry is needed**. Deliberately NOT `CONCURRENTLY` (PostgreSQL rejects it on a partitioned table, and Alembic runs in-transaction) and NOT partial (a `WHERE scope_ref IS NOT NULL` index would be smaller but would have to leave the ORM and join the env.py registry). Round-trip clean: `downgrade -1` leaves zero `scope_ref` indexes, and full `downgrade base` → `upgrade head` → `alembic check` is clean)

> **From the `migration-reviewer` pass** (verdict: migration correct; it independently re-ran the tree on an operational-shaped 7-partition DB, exercised the SECURITY DEFINER path as the non-owner app role, and round-tripped `pg_dump -Fc`/`pg_restore` to confirm no restore hazard). Three things it caught, all folded in:
> - **The docstring named the wrong lock mode.** It claimed `AccessExclusiveLock` on the parent and partitions. Measured from inside the building transaction — and re-measured independently before correcting — it is **`ShareLock`** on the parent and every partition, with `AccessExclusiveLock` only on the brand-new, not-yet-visible index relations. That difference is operationally load-bearing: **writes block, reads do not**, so the original wording would have pushed an operator into an unnecessary full outage. Confirmed end-to-end (a `SELECT` returned 50k rows while a `SHARE MODE` lock was held; an `INSERT` blocked to timeout). The **downgrade** is the strict one — `DROP INDEX` takes `AccessExclusiveLock` on parent and all partitions, but catalog-only and instant.
> - **The in-place upgrade path has no maintenance-window guidance** (MAJOR, CI-blind). `easysynq upgrade` runs on a one-off worker **while api/worker/beat stay up**, no `lock_timeout` is configured anywhere, and `env.py` wraps the run in ONE transaction — so an index build queued behind an open writer convoys the entire write path (nearly every mutating request writes an `audit_event`) until all of `upgrade head` commits. CI cannot see this: the `migrations` job round-trips an empty single-connection DB. Documented in `docs/runbooks/backup-restore.md` § Upgrade with stop/start steps and a row-count pre-check. It also corrected a wrong premise of mine — migrations do **not** auto-apply on api startup; the compose `migrate` one-shot completes before api/worker/beat start, so the fresh-boot path was never at risk. **A global `lock_timeout` on the alembic connection remains an open option — deliberately not taken here, as it changes every migration's failure mode and is Batch 13 (infra) territory.**
> - **The index name deviated from `NAMING_CONVENTION`.** It was `ix_audit_event_scope_ref`, which reads as a single-column index and would mislead a later reader into thinking a bare `WHERE scope_ref = ?` is served — it is not, `org_id` leads. Renamed to the canonical `ix_audit_event_org_id_scope_ref_id` (`ix_%(table_name)s_%(column_0_N_name)s`, `db/base.py`) and the full round-trip re-verified after the rename.

> **From the Codex review round 1 (P1, real — verified before acting).** Indexing `scope_ref` made a **pre-existing latent bug fatal**: a PostgreSQL btree tuple cannot exceed 2704 bytes, but `scope_ref` is unbounded `Text` built from caller-supplied values — `pep._scope_ref` interpolates `resource.folder_path`, and neither `DocumentCreate.folder_path` nor `MetadataUpdate.folder_path` carries any length limit (nor does anything truncate downstream). Confirmed empirically on PG16: ~2600 incompressible chars insert fine, ~2700 fails with `index row size 2768 exceeds btree version 4 maximum 2704`. ⚠ **A first probe using `repeat('a', 10000)` wrongly suggested there was no problem — PostgreSQL compresses before indexing, so the value must be INCOMPRESSIBLE to reproduce.** The consequence is worse than a failed insert: `DbAuthzAuditSink.record` commits in its OWN transaction, so the error escapes `pep.enforce` and the caller gets a **500 instead of a 403 with the denial never recorded** — an audit-integrity gap. Two things Codex did not name but which the fix had to cover: `documented_information.identifier`/`legacy_identifier` are *also* unbounded `Text` (so the vault path is exposed too, not just authz), and `scope_ref` is an input to the **audit hash chain**, so any cap must be deterministic and applies to new rows only.
>
> **Fixed centrally on the model** (owner-chosen): `AuditEvent._bound_scope_ref`, a `@validates("scope_ref")` hook capping at 512 characters — a readable prefix plus a sha256 digest **of the full original**, so two over-long values sharing a prefix stay distinguishable instead of collapsing. On the model rather than at any producer because `scope_ref` is written from ~56 call sites; a bound at one would silently not apply to the other 55, nor to the next one added. 512 *characters* (not bytes) is deliberate: worst-case UTF-8 is 4 bytes/char → ≤2048 bytes, comfortably under the limit, and a char cap cannot split a multi-byte sequence. Mutation-verified: a naive `value[:512]` reds exactly the prefix-collision test, and removing the cap reds three.
>
> **Migration guard**: 0075 translates the btree size failure into an actionable message, because a row predating the cap would otherwise abort the upgrade with a raw PostgreSQL error. It deliberately does **not** rewrite such rows: `audit_event` is append-only and hash-chained, so correcting a legitimate audit record is an operator decision under a signed-off correction, never something a migration does silently. (Round 1 implemented this as an `octet_length > 2000` pre-flight `SELECT`; round 2 replaced it — see below.)
>
> **From the Codex review round 2 (2 P1 + 2 P2 — all verified before triage; the two P1s were regressions THIS batch introduced).**
> - **P1 — the cap silently broke the very endpoint this batch set out to index.** `document_audit_events` filters `scope_ref == doc.identifier` using the RAW identifier, while the new cap stores the capped form. `documented_information.identifier` is unbounded `Text` and an import can preserve an arbitrarily long legacy identifier, so for such a document the per-document trail would return **an incomplete history with no error at all** — on an audit surface, strictly worse than failing. Fixed by extracting `bound_scope_ref` as a module-level function and routing the search key through it (`api/audit.py::document_scope_match`), searching **both** the raw and capped forms when they differ so rows written before the cap are still found. The helper was extracted from the handler specifically so it is unit-testable: a regression to raw equality is invisible to any test using short identifiers, where the cap is the identity function. Mutation-verified — reverting to raw equality reds it, and so does searching only the capped key.
> - **P1 — the round-1 pre-flight rejected rows that are perfectly indexable.** Its fixed `octet_length > 2000` bound contradicted the model's own cap: 512 four-byte characters is 2048 bytes, so a value the cap explicitly permits would have blocked the upgrade. Raw length cannot decide indexability at all — PostgreSQL compresses before indexing, so a long compressible value indexes fine while a shorter incompressible one does not. Replaced with a `try/except` around the DDL that translates only the `index row size` failure: **PostgreSQL itself is the exact oracle**, so there are no false positives and no data read. Proven on PG16 — a 2048-byte row now upgrades cleanly where the old bound refused it, and a genuinely unindexable row still produces the actionable error.
> - **P2 — offline `--sql` generation crashed.** Under `alembic upgrade X:Y --sql` the offline bind emits the statement and returns `None`, so the pre-flight's `.scalar_one()` raised `AttributeError` and **no SQL was produced at all**. Reproduced exactly; fixed for free by the `try/except` above (no data read ⇒ nothing to break offline), and now verified to emit `CREATE INDEX …`. ⚠ Codex's rationale overstated one thing — the repo documents no `--sql` path (`install-airgapped.md`'s "offline" means air-gapped image bundles) — but the regression was real regardless: this broke a standard Alembic capability that worked before.
> - **P2 — `outcome` should stay `required`.** Real but non-breaking (the published schema is a permissive superset), so **filed as [#370](https://github.com/CoJoA13/EasySynQ/issues/370)** rather than fixed in-branch, per the owner's triage rule for this round. `_response()` always emits `outcome` and the `ALREADY_SATISFIED` literal includes it as `null`, so nullable-but-required is the precise shape. Closed in Batch 12.5 below with a structural guard (a live response alone cannot reveal an over-permissive superset).
>
> **From the Codex review round 3 (1 P1 — an aliasing flaw in the round-1/2 cap itself; fixed, since a merged audit trail breaks the product's core promise).** The capped key could **collide with another document's untouched identifier**. `bound_scope_ref` emitted exactly `_SCOPE_REF_MAX_CHARS` characters, so a value of that length passed through unchanged *and* was a possible cap output: document B with a 4000-char identifier capped to a 512-char key, while document A whose identifier **was** that exact 512-char string stored it verbatim — both documents' audit events landing on ONE `scope_ref`, and `document_scope_match` then silently merging two documents' histories. Reproduced directly (`bound(bound(x)) == bound(x)` returned `True`). Identifiers are arbitrary strings on the import path and the uniqueness constraint is on the raw value, so both rows can coexist: constructible, not theoretical.
>
> ⚠ **The irony worth recording: the round-2 test suite *asserted* the property that made this possible.** `test_bounding_is_idempotent` pinned `bound(bound(x)) == bound(x)` as desirable — and idempotence is exactly equivalent to "a capped value passes through unchanged", i.e. the alias. A green, deliberately-written test encoded the bug.
>
> **Fix**: the emitted length (`_SCOPE_REF_CAPPED_CHARS = 544`) is now strictly greater than the pass-through threshold (`_SCOPE_REF_MAX_CHARS = 512`), so untouched values (≤512 chars) and capped values (exactly 544) are **disjoint by length** — no capped key can ever equal an unmodified one. The digest also went 64→128 bits, so two distinct long values collide only on a full prefix match *and* a 128-bit digest match. Worst-case UTF-8 is 2058 bytes, still far under the 2704 btree limit (measured, not estimated). **Idempotence is deliberately surrendered** — it is provably incompatible with disjointness — which is safe only because every caller applies the function exactly once to a RAW value (`@validates` on write, `document_scope_match` on read); the constants carry a warning never to re-apply it to a value read back from the database. The idempotence test is replaced by `test_a_capped_key_can_never_alias_another_rows_untouched_value`, mutation-verified: setting the emitted length equal to *or* below the threshold reds it.

> **From the Codex review round 4 (1 P1 + 1 P2).** **Separating the WRITE keys was not enough — the READ path reopened the identical merge.** `document_scope_match` still searched the raw identifier as a backward-compatibility operand, so if document B's long identifier caps to `K` and document A's raw identifier *is* `K`, then A's events live under `bound(K)` while `K` itself is B's key — and searching both returned **B's audit events under A's history**. Reproduced directly. The lesson generalises: round 3 fixed the write side and I treated the class as closed; the same aliasing simply re-entered through the other side of the same key space.
>
> **Fix**: exactly ONE key is searched — `scope_ref == bound_scope_ref(identifier)`. A value of exactly `_SCOPE_REF_CAPPED_CHARS` characters is irreducibly ambiguous (this document's own pre-cap row, or another document's post-cap key) and nothing in the row distinguishes them, so no predicate over that value is sound. Mutation-verified against **both** prior bugs: restoring the raw operand reds the new `test_history_query_never_searches_another_documents_key`, and reverting to raw-only equality reds two tests. ⚠ The cost is a **named residual** (now in `docs/slice-history.md` ⚠ OPEN RESIDUALS): pre-cap rows for a document with a >512-char identifier are no longer reachable. Nil blast radius for normal documents — below the threshold the cap is the identity function — and a completeness gap on a pathological imported identifier is strictly preferable to a cross-document leak. Closing it properly needs a discriminator separating legacy raw keys from capped keys, i.e. a migration over append-only hash-chained rows: its own slice.
>
> **P2 — `capa_close_state` / `dcr_state` should `$ref` the existing `CapaCloseState` / `DcrState` enums** instead of being bare nullable strings, so drift cannot validate and generated clients keep state-union exhaustiveness. Real but non-breaking (permissive superset) → tracked on [#370](https://github.com/CoJoA13/EasySynQ/issues/370) alongside the `outcome`-required fix, since it is the same schema and the same pass; closed together in Batch 12.5.

### ☑ Batch 12.5 — Close the gates that never ran (INSERTED between 12 and 13, owner-scheduled)
`branch: feat/contract-response-validation` ·
[PR #371](https://github.com/CoJoA13/EasySynQ/pull/371) · tests + contract + CI only
(no migration, no new key)

Two separate gates are **declared but never invoked**. Both are the same failure shape, and it is the
shape that produced Batch 12 in the first place: a check that does not run reads exactly like a check
that passes.

**(a) No RESPONSE is ever validated against its published schema.** `redocly lint` proves only that
the document is well-formed, and `apps/api/pyproject.toml:100` declares a `contract` marker plus
`schemathesis>=4.24.0` that **nothing invokes**. That is how `AuditEvent.object_type` sat 8 values
stale, and how `DecisionResult` kept rejecting the reachable quorum `ALREADY_SATISFIED` 200 through a
source review *and* a first fix pass. Batch 12 closed the *enum* half of the class
(`tests/unit/test_openapi_enum_parity.py`); this closes the response-shape half.

Scope: 230 paths / 282 operations, 360 responses with a content schema. Three non-negotiables (each
a real hazard — full rationale in `docs/slice-history.md` ⚠ OPEN RESIDUALS):

- drive it through an **authenticated, post-setup** fixture — otherwise the 423 latch and bearer-auth
  turn the run into a wall of 423/401s and a **meaningless green**;
- run it ONLY against the disposable `app_under_test` testcontainers — 149 of 282 operations mutate,
  some into WORM/append-only and disposition paths that are irreversible by design;
- land it **advisory / non-gating first**; a 360-response sweep will surface pre-existing violations,
  and making it required on day one would red CI on old debt and block Batch 13.

**(b) The `/migrations` tree is never linted or format-checked by CI.** The `api` job runs
`ruff check . && ruff format --check --diff .` with `working-directory: apps/api`, so the repo-root
`migrations/` tree (76 files) is outside its scope entirely — despite being, by the project's own
account, the most error-prone area in the codebase. Measured on this branch:

- a default-config `ruff check` over `migrations/` is clean, but re-running with the API's actual
  strict selection exposes **357 existing findings** in five classes (mostly E501/S608/I001) — the
  original "lint is not the problem" measurement was itself another consequence of config drift;
- `ruff format --check` reports **29 of 76 files** unformatted under the project's config. (A naive
  run reports 61 — see the config trap below.) `0075` is clean.

⚠ **The config trap, which must be fixed first or the numbers lie.** There is **no ruff config at the
repo root**, and `[tool.ruff]` lives in `apps/api/pyproject.toml`. Ruff discovers config by walking up
from each file, so anything under `migrations/` gets ruff's **defaults — line-length 88, not the
project's 100**. Any `ruff` invocation from the repo root therefore silently applies different rules
than CI does. Closing this needs a root `ruff.toml` (or an explicit `--config`) so one rule set governs
both trees, *then* a formatting sweep of the 29, *then* the CI step widened. Same discipline as (a):
land the sweep separately from the gate so the gate turns on green rather than red.

- [x] `tests/integration/test_contract_response_schemas.py` — invoke the declared Schemathesis gate
  over all **282 operations** against the published file, with one deterministic positive,
  stateless case per operation and response status/header/content-type/schema checks (fixed:
  the module uses only the disposable app fixture, maps the two unversioned health routes honestly,
  disconnects the long-lived SSE route after its first real body frame, and asserts that every
  selected operation actually returned a response; the explicit contract marker is excluded from
  the four required integration shards)
- [x] authenticated + post-setup proof — prevent a 401/423 wall from reading green (fixed:
  each case gets a real signed test JWT for an ACTIVE disposable principal with SYSTEM-scope
  permission coverage; `/me` and the non-exempt `/me/permissions` must both return 200 before the
  generated request runs, and the run-level guard fails when 401+423 exceed 20% of observed
  responses. The live baseline stayed below that guard with no 423 wall)
- [x] `.github/workflows/ci.yml` — invoke the contract marker without blocking Batch 13 (fixed:
  the dedicated `contract-responses` job runs the testcontainers sweep with
  `continue-on-error: true`; a harness/setup failure still makes that advisory job visible rather
  than fabricating a pass)
- [x] root Ruff config + migration sweep + CI (fixed: `ruff.toml` now owns the shared Python
  formatting and strict lint selection; the API adds only its test-credential exceptions, while
  five measured migration-debt classes are explicitly scoped and every other selected rule is
  live. Exactly **29 of 76** migrations were formatter-only changes in their own commit; an AST
  comparison proved all 29 equivalent, including every SQL string and constant. Root and explicit
  config formatting checks now agree at 76/76, and the separately-landed CI step runs both migration
  lint and format checks from the repository root)
- [x] issue [#370](https://github.com/CoJoA13/EasySynQ/issues/370) fold-in (fixed:
  `DecisionResult.outcome` is nullable-but-required, and nullable
  `capa_close_state`/`dcr_state` now reference the confirmed `CapaCloseState`/`DcrState`
  components. A structural unit guard pins this precision because live-response validation alone
  cannot detect an over-permissive published superset)

> **Advisory baseline (2026-07-26): 274 operations passed / eight pre-existing violations, named
> rather than fixed inline.**
> Two setup responses emit problem codes absent from `Problem.code`
> (`backup_not_configured`, `auth_unavailable`); `POST /records:init-upload` accepts an empty
> contract-generated SHA then raises an unhandled botocore validation error; the static
> `GET /audit-events/export` route is shadowed by `/{event_id}` and returns an undocumented 422; and
> three requests valid under the published input schemas return undocumented 422s (empty CAPA
> containment/root-cause content blocks and an empty complaint description); finally, the
> notification stream publishes SSE `{event,data}` frames while its content schema declares each
> event as a plain string. These are triaged in `docs/slice-history.md` ⚠ OPEN RESIDUALS; ratchet the
> job to required only after that baseline is fixed in its own follow-up batch.

### ☐ Batch 13 — Infra / deploy hardening
`branch: fix/major-infra-deploy` · infra (verify on the live/appliance path)

- [ ] `infra/appliance/provision/easysynq-provision.sh:127` — provisioner never sets `PUBLIC_BASE_URL`/`APP_BASE_URL` → verify QR / share links / deep links all point at `http://localhost` `[f]`
- [ ] `.env.example:34` — online install ships `S3_PUBLIC_ENDPOINT=http://localhost:9000` → presigned upload/download broken for remote browsers; the `s` profile also exposes plaintext MinIO on 0.0.0.0 `[C]`
- [ ] `infra/compose/compose.yml:74` — Keycloak `start-dev` with no persistent volume → any container recreation wipes all accounts + client edits; move to a real store `[C]`

### ☐ Batch 14 — Web correctness
`branch: fix/major-web-correctness` · apps/web + vitest

- [ ] `apps/web/src/features/authoring/CheckInPanel.tsx:41` — checked-out flag / file / reason not keyed on `documentId` → survives a doc-to-doc nav → wrong-content controlled-doc version; `key={doc.id}` or effect-reset `[C]`
- [ ] `apps/web/src/lib/auth.tsx:86` — no token-renewal wiring → token goes stale at expiry, every call 401s until manual reload; subscribe to `addUserLoaded` and push the renewed token (do NOT unmount+redirect) `[C]`
- [ ] `apps/web/src/features/ingestion/PreCommitChecklist.tsx:127` — ★-coverage reads `star_coverage.satisfied/.total` (never sent) → feature never displays; read the real projected shape + fix the fabricated MSW fixture `[C]`
- [ ] `apps/web/src/features/ingestion/ReviewCockpit.tsx:59` — all import-review write actions fail silently → thread mutation errors into visible UI `[C]`

### ☐ Batch 15 — Web a11y & polish
`branch: fix/major-web-a11y-polish` · apps/web + vitest/jest-axe

- [ ] `apps/web/src/app/shell/DetailDrawer.tsx:32` — app-wide unlabeled Modal/Drawer close buttons; default `closeButtonProps` on the Modal/Drawer theme components (NOT the shared `CloseButton`) `[C]`
- [ ] `apps/web/src/features/notifications/NotificationBell.tsx:48` — bell Popover has interactive content but no `trapFocus` → broken keyboard focus order `[C]`
- [ ] `apps/web/src/features/review/TasksInbox.tsx:169` — due/effective dates rendered as UTC-truncated ISO disagree with the org-tz dates notifications + the register report show; use one org-tz-aware helper `[f]`
- [ ] `apps/web/src/features/context/ContextScorecardBand.tsx:20` — scorecard/hero bands hardcode a light bg → illegible in dark mode across 5 register surfaces `[C]`

### ☐ Batch 16 — Test false-PASS / CI-flake
`branch: fix/major-test-ci-flake` · integration test hygiene · **quick; protects every later PR's CI**

- [ ] `apps/api/tests/integration/test_notification_dispatch.py:356` — commits a second Organization with no cleanup → `scalar_one()` MultipleResultsFound when the shard boundary shifts `[f]`
- [ ] `apps/api/tests/integration/test_mgmt_review_pack.py:112` — commits a second Organization (strands the MR) with no cleanup → same shard-flake `[f]`

### ☐ Batch 17 — Docs drift
`branch: fix/major-docs-drift` · docs-only

- [ ] `docs/07-authorization-model.md:89` — catalog omits `document.distribute` (+ `retention`/`drift` rows in §3.10) `[f]`
- [ ] `docs/15-api-design.md:338` — `PATCH /documents/{id}` documented `document.edit`; implemented `document.manage_metadata` `[f]`
- [ ] `docs/15-api-design.md:626` — documented `POST /audits/{id}/transition` does not exist (six verb endpoints) `[C]`
- [ ] `docs/15-api-design.md:627` — §8.12 wrong audit permission keys (`audit.record_finding`/`audit.plan` → `finding.create`/`audit.create`) `[f]`
- [ ] `docs/15-api-design.md:523` — NCR edit documented `PATCH /ncrs/{id}` `ncr.update`; implemented `PATCH /ncrs/{id}/disposition` `ncr.record_correction` `[f]`
- [ ] `docs/15-api-design.md:191` — notification inbox documented `/me/notifications`; shipped `/notifications*` `[C]`
- [ ] `docs/15-api-design.md:490` — documented `GET /records/{id}/download` does not exist (per-blob `/records/{id}/evidence/{sha256}/download`) `[f]`

---

_See [`review-2026-07-22.md`](./review-2026-07-22.md) for the full failure scenario and fix rationale
behind every line above. MINOR (104) and NIT (35) findings are tracked in that doc and not scheduled
here._
