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
| 8 | Document lifecycle FSM gates | 2 | 2 | ☐ not started | — |
| 9 | Workflow approval correctness | 2 | 3 | ☐ not started | — |
| 10 | Ingestion pipeline correctness | 2 | 3 | ☐ not started | — |
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

### ☐ Batch 8 — Document lifecycle FSM gates
`branch: fix/major-doc-lifecycle-gates` · backend + integration

- [ ] `services/vault/service.py:396` — `checkout`/`checkin` are not FSM-gated → a check-in during InReview permanently bricks the doc + its approval task; gate on `current_state in {Draft, UnderRevision}` `[C]`
- [ ] `api/documents.py:1703` — generic `POST /documents/{id}/release` skips the managed-subtype hooks → a generically-released MR is permanently unclosable (and OBJ unit-reset skipped); route managed subtypes through their post-release chain `[C]`

### ☐ Batch 9 — Workflow approval correctness
`branch: fix/major-workflow-approval-correctness` · backend + integration

- [ ] `services/capa/service.py:641` — `decide_capa_action_plan` has no outcome allow-list → a non-`approve` positive outcome mints a false WORM `signature_event(meaning='approval')`; add `_ALLOWED_CAPA_OUTCOMES` + 422 before `engine.decide` `[C]`
- [ ] `services/dcr/service.py:713` — `decide_dcr_approval` passes non-approve positive outcomes through → permanently bricks the DCR; add `_ALLOWED_DCR_OUTCOMES` + 422 `[C]`
- [ ] `services/workflow/engine.py:508` — stage advance drops the definition `default_sla` → 2nd-tier approval tasks get `due_at=null` (no reminders/overdue/escalation); load the definition and pass `default_sla` to `_enter_stage` `[C]`

### ☐ Batch 10 — Ingestion pipeline correctness
`branch: fix/major-ingestion-correctness` · backend + integration

- [ ] `services/ingestion/commit.py:185` — `reconstruct_revision_chain` opt-in (R10) never consumed at commit → silently ignored; implement or reject/warn honestly `[C]`
- [ ] `services/ingestion/commit.py:296` — `{TYPE}-<new>` sentinel persisted as `legacy_identifier` on freshly-allocated imports → search pollution + collisions; guard on `identifier_source` `[C]`
- [ ] `services/ingestion/service.py:673` — `reap_stalled_runs` FAILs a live, heartbeating pipeline after 6h → large OCR import can never complete; anchor the backstop on stage progress `[C]`

### ☐ Batch 11 — Notifications & operator alerting
`branch: fix/major-notify-and-alerting` · backend (+ small test updates)

- [ ] `services/notifications/render.py:61` — `html.escape` in `_substitute` feeds two PLAIN-TEXT sinks → titles with `& ' < >` garbled in email + double-escaped in the SPA; **drop the escape** (both sinks are plain text; the whitelist regex already blocks slot injection), reserving sink-level escaping only for a future HTML sink `[C]`
- [ ] `services/backup/service.py:110` — nightly backup failures + chain-verify breaks never notify admins (`system.backup_failed` / `integrity.alarm` class-mapped, no emitter); wire the in-DB path AND an out-of-band operator channel (SMTP/syslog/webhook) for the DB-down mode `[C]`

---

## Tier 3 — Contract · infra · web · tests · docs

### ☐ Batch 12 — Contract & schema housekeeping
`branch: fix/major-contract-schema-housekeeping` · openapi + migration

- [ ] `packages/contracts/openapi.yaml:9844` — `ImportRunStatus` enum missing 4 live states (Reviewing, Committing, Completed, PartiallyCommitted) `[C]`
- [ ] `packages/contracts/openapi.yaml:7743` — `AuditEvent.object_type` closed enum missing 8 of 16 values `[C]`
- [ ] `packages/contracts/openapi.yaml:7699` — `DecisionResult` (additionalProperties:false) omits `capa_close_state` / `dcr_state` `[C]`
- [ ] `db/models/audit_event.py:51` — `scope_ref` (per-document history access path) carries no index; add partitioned btree `(org_id, scope_ref, id)` `[C]`

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
