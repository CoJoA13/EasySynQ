# Remediation plan — 2026-07-22 full review (MINOR findings)

> Working tracker for the **104 MINOR** findings in
> [`review-2026-07-22.md`](./review-2026-07-22.md). The source review grouped findings only by
> broad subsystem; this document regroups them into implementation-sized batches and records their
> current disposition.

## Inventory and conventions

- The historic denominator remains **104**. One finding was already closed while the MAJOR work was
  underway, leaving **103** to schedule here. Batch M1 closed 4; M2 closed 2; M3 closed 2; M4
  closed 4; M5 closed 3; M6 closed 4; M7 closed 3; M8 closed 2; M9 closed 4; M10 closed 4;
  M11 closed 3; M12 closed 5; M13 closed 4; M14 closed 6; M15 closed 4; M16 closed 7;
  M17 closed 3; M18 closed 9; M19 closed 9; M20 closed 6; M21 closed 8; M22 resolves 7;
  **no findings remain queued** after M22.
- A queued finding is not assumed to still be live. Every batch must re-locate and revalidate its
  findings against then-current `main` before implementation; close, re-scope, or reject it with
  evidence rather than mechanically applying the 2026-07-22 suggestion.
- One branch and one ready (non-draft) PR per implementation batch. Each PR updates its own status,
  records the verified fix, and links the PR. Squash-merge remains owner-controlled.
- `[C]` means confirmed and corrected against current source, `[R]` means rejected after
  revalidation with the recorded evidence, and `[f]` preserves the source review's finder-only
  status pending batch-time revalidation. Line numbers are historical pointers and may drift.
- M0 is an accounting bucket, not a new PR. It prevents an already-closed finding from silently
  disappearing or being fixed twice.

## Status at a glance

| Batch | Group | Findings | Status | PR |
|---|---|:---:|---|---|
| M0 | Preclosed during MAJOR remediation | 1 | ☑ merged | [#369](https://github.com/CoJoA13/EasySynQ/pull/369), precision follow-up [#371](https://github.com/CoJoA13/EasySynQ/pull/371) |
| M1 | Auth boundaries and public diagnostics | 4 | ☑ merged | [#381](https://github.com/CoJoA13/EasySynQ/pull/381) |
| M2 | Scoped authorization and reporting | 2 | ☑ merged | [#382](https://github.com/CoJoA13/EasySynQ/pull/382) |
| M3 | Notification reliability | 2 | ☑ merged | [#383](https://github.com/CoJoA13/EasySynQ/pull/383) |
| M4 | Lifecycle and workflow guards | 4 | ☑ merged | [#384](https://github.com/CoJoA13/EasySynQ/pull/384) |
| M5 | Ingestion commit integrity | 3 | ☑ merged | [#385](https://github.com/CoJoA13/EasySynQ/pull/385) |
| M6 | Ingestion review and family integrity | 4 | ☑ merged | [#386](https://github.com/CoJoA13/EasySynQ/pull/386) |
| M7 | Records and rendering resilience | 3 | ☑ merged | [#387](https://github.com/CoJoA13/EasySynQ/pull/387) |
| M8 | Vault and retention input guards | 2 | ☑ merged | [#388](https://github.com/CoJoA13/EasySynQ/pull/388) |
| M9 | Migration and ORM coherence | 4 | ☑ merged | [#389](https://github.com/CoJoA13/EasySynQ/pull/389) |
| M10 | Schema and index design | 4 | ☑ merged | [#390](https://github.com/CoJoA13/EasySynQ/pull/390) |
| M11 | Organization time and audit scalability | 3 | ☑ merged | [#391](https://github.com/CoJoA13/EasySynQ/pull/391) |
| M12 | Data-model documentation drift | 5 | ☑ merged | [#392](https://github.com/CoJoA13/EasySynQ/pull/392) |
| M13 | API documentation drift | 4 | ☑ merged | [#393](https://github.com/CoJoA13/EasySynQ/pull/393) |
| M14 | Infrastructure, deploy, and public edge | 6 | ☑ merged | [#394](https://github.com/CoJoA13/EasySynQ/pull/394) |
| M15 | Cross-stack naming | 4 | ☑ merged | [#395](https://github.com/CoJoA13/EasySynQ/pull/395) |
| M16 | UI copy and terminology | 7 | ☑ merged | [#396](https://github.com/CoJoA13/EasySynQ/pull/396) |
| M17 | Test false-PASS traps | 3 | ☑ merged | [#397](https://github.com/CoJoA13/EasySynQ/pull/397) |
| M18 | Web shell, layout, and error consistency | 9 | ☑ merged | [#398](https://github.com/CoJoA13/EasySynQ/pull/398) |
| M19 | Web state and badge consistency | 9 | ☑ merged | [#399](https://github.com/CoJoA13/EasySynQ/pull/399) |
| M20 | Web visual tokens and semantics | 6 | ☑ merged | [#400](https://github.com/CoJoA13/EasySynQ/pull/400) |
| M21 | Web async and error UX | 8 | ☑ merged | [#401](https://github.com/CoJoA13/EasySynQ/pull/401) |
| M22 | Web accessibility | 7 | ☑ ready for PR | — |

**Accounting: 1 preclosed + 96 merged + 7 ready for PR = 104 original findings; 0 queued.**

---

## Closed before this tracker

### ☑ M0 — Decision-result response shape

- [x] `packages/contracts/openapi.yaml:7702` — quorum `ALREADY_SATISFIED` can return
  `outcome: null` without `decided_at`/`decided_by`, which the old `DecisionResult` rejected `[C]`
  (closed in #369; #371 restored the more precise nullable-but-required `outcome` and enum refs).

---

## Backend security and correctness

### ☑ M1 — Auth boundaries and public diagnostics — [#381](https://github.com/CoJoA13/EasySynQ/pull/381)

`branch: fix/minor-auth-boundaries` · API + OpenAPI + docs + unit/integration tests · no migration
and no new permission key

- [x] `apps/api/src/easysynq_api/api/setup.py:83` — sensitive `GET /setup` detail was available to
  every authenticated user `[C]` (fixed: require the existing SYSTEM `config.read`; keep only
  `/setup/state` public and bootstrap outside the PEP).
- [x] `apps/api/src/easysynq_api/auth/dependencies.py:60` — concurrent first-login JIT inserts
  could race into a unique-constraint 500 `[C]` (fixed: PostgreSQL `ON CONFLICT DO NOTHING` and
  resolve the winning identity; two-session integration coverage pins convergence).
- [x] `apps/api/src/easysynq_api/auth/jwks.py:37` — signing keys were cached forever, unknown kids
  fetched without a bound, and fetch errors escaped as 500 `[C]` (fixed: bounded TTL replacement,
  single-flight refresh, miss/outage cooldown, fail-closed sanitized 503).
- [x] `apps/api/src/easysynq_api/readiness.py:54` — public `/readyz` exposed raw dependency
  exception strings `[C]` (fixed: expose names and readiness booleans only while retaining internal
  diagnostics for trusted workflows).

### ☑ M2 — Scoped authorization and reporting — [#382](https://github.com/CoJoA13/EasySynQ/pull/382)

`branch: fix/minor-scoped-authz-reporting` · API + integration · no migration and no new permission
key

- [x] `apps/api/src/easysynq_api/services/ack/decide.py:166` — acknowledgment authorization used a
  raw `ProcessLink` query instead of the canonical satellite-aware scope loader `[C]` (fixed:
  converge on `vault_repo.process_ids_for_doc`; a mutation-distinguishing deny-wins integration
  proof verifies the decision gate consumes it, while the existing objective-satellite proof pins
  the loader's real union semantics).
- [x] `apps/api/src/easysynq_api/api/reports.py:131` — register surface accepted a
  scope-unsatisfiable PROCESS `report.read` allow and returned a misleading org-wide empty result
  `[C]` (fixed: centralize satisfiable-ALLOW selection for both the request gate and snapshot
  provenance; a PROCESS selector must contain at least one real process in the caller's org. Empty,
  malformed, nonexistent, and cross-org-only selectors now yield an honest 403 when they are the
  sole allow. A valid in-org process remains satisfiable even when it currently has no linked
  documents, because that is an accurate process-scoped empty register rather than an impossible
  authorization scope).

### ☑ M3 — Notification reliability — [#383](https://github.com/CoJoA13/EasySynQ/pull/383)

`branch: fix/minor-notification-reliability` · API + OpenAPI + integration · no migration and no
new permission key

- [x] `apps/api/src/easysynq_api/api/notifications.py:69` — a negative notification-list limit
  reached PostgreSQL as an invalid negative `LIMIT` and returned 500 `[C]` (fixed: reject values
  below zero at FastAPI request validation while preserving the existing zero-row request and
  server cap above 200; OpenAPI now declares the non-negative bound and 422 response).
- [x] `apps/api/src/easysynq_api/services/notifications/digest.py:151` — a missing `digest.daily`
  template still stamped `digested_at`, silently dropping retryable items `[C]` (fixed: an eligible
  template miss logs and rolls back without stamping, so a restored template creates exactly one
  digest on retry; genuinely ineligible users retain the intentional terminal stamp/no-email
  behavior).

### ☑ M4 — Lifecycle and workflow guards — [#384](https://github.com/CoJoA13/EasySynQ/pull/384)

`branch: fix/minor-lifecycle-workflow-guards` · API + OpenAPI + integration · no migration and no
new permission key

- [x] `apps/api/src/easysynq_api/services/improvement/service.py:219` — terminal Closed/Cancelled
  initiatives remain editable `[C]` (fixed: the locked update path rejects every terminal metadata
  edit with 409 `improvement_not_editable`, preserving both fields and audit history).
- [x] `apps/api/src/easysynq_api/services/dcr/repository.py:85` — same-transaction DCR stage events
  can tie on `occurred_at` and render out of order without a stable tiebreaker `[C]` (fixed: the
  atomic Assessed→Routed→InApproval pair receives strictly increasing causal timestamps, and reads
  reconstruct legacy equal-timestamp rows through their from-state→to-state chain, using UUID only
  as a deterministic last resort for disconnected/malformed history).
- [x] `apps/api/src/easysynq_api/services/dcr/service.py:397` — impact annotations remain editable
  on terminal DCRs `[C]` (fixed: Closed/Cancelled/Rejected requests return 409
  `dcr_impact_not_editable` before any annotation or audit mutation).
- [x] `apps/api/src/easysynq_api/services/workflow/engine.py:521` — an unresolvable quorum
  conditional is overwritten from `NEEDS_ATTENTION` to a plausible `REJECTED` terminal `[C]`
  (fixed: the dedicated fail-closed branch preserves `NEEDS_ATTENTION`, retires sibling work under a
  distinct failed-stage marker whose retry response remains `FAILED`, and records
  `reason=unresolvable_quorum` instead of taking a normal business-rejection transition).

### ☑ M5 — Ingestion commit integrity — [#385](https://github.com/CoJoA13/EasySynQ/pull/385)

`branch: fix/minor-ingestion-commit-integrity` · API + Web + OpenAPI + unit/integration · migration
`0076` · no new permission key

- [x] `apps/api/src/easysynq_api/services/ingestion/commit.py:338` — the validated and audited
  folded owner decision was ignored during commit `[C]` (fixed: carry whether the owner came from a
  human decision, resolve the reviewed reference against active non-guest users in the item's org,
  and materialize it on both imported Documents and Records; the bulk review UI now selects an
  actual directory user and submits its stable ID, while legacy exact identities remain accepted
  even when a subject is UUID-shaped; the checklist revalidates persisted human-owner references
  before the run leaves its editable review state, so the old `Quality Manager` placeholder can be
  corrected instead of stranding the run as partially committed; commit then resolves the owner
  again under a directory-row lock and stores the validated ID in migration `0076`'s internal
  run-level snapshot, leaving the human decision history and checklist counts unchanged; both
  initial commit and partial resume snapshot remaining owners, exact stable IDs survive a later
  disable/retire, and unresolved legacy labels can receive a per-file correction while successful
  items remain immutable; duplicate display names are disambiguated by a stable ID suffix in the
  picker; the worker remains fail-closed for a missing/guest/cross-org snapshot, while
  authorship/capture/signature attribution remains with the committer).
- [x] `apps/api/src/easysynq_api/services/ingestion/commit.py:530` — `_record_failed` could commit a
  false failure audit after a peer successfully committed the item `[C]` (fixed: the conditional
  failure UPSERT now returns whether it won; only a won `failed` ledger write can emit the failure
  audit/log, while a concurrent `success`/`noop` suppresses both atomically).
- [x] `apps/api/src/easysynq_api/services/ingestion/review.py:253` — an empty identifier survived
  validation and committed a vault document with `identifier=''` `[C]` (fixed: reject empty and
  whitespace-only identifier corrections with a 422 before recording the decision, surface
  persisted legacy blanks as checklist/resume blockers before status transition, and revalidate the
  folded identifier at the commit boundary so a bypassed legacy decision still cannot write a
  blank vault identifier; the OpenAPI schema advertises the non-blank constraint).

### ☑ M6 — Ingestion review and family integrity — [#386](https://github.com/CoJoA13/EasySynQ/pull/386)

`branch: fix/minor-ingestion-review-integrity` · API + integration + docs · no migration and no new
permission key

- [x] `apps/api/src/easysynq_api/services/ingestion/review.py:477` — bulk “accept all HIGH”
  silently superseded a prior explicit EXCLUDE `[C]` (fixed: selector-based bulk review now treats
  any existing per-file decision as more-specific human intent, skips those files, and reports the
  skipped count; an all-skipped selection is a calm no-op with no fabricated decision/event, while
  explicit `file_ids` remain the deliberate overwrite path).
- [x] `apps/api/src/easysynq_api/services/ingestion/review.py:487` — the bulk-selector path skipped
  the `included_candidate` guard used by the other paths `[C]` (fixed: validate every selector
  match before inserting anything and reject the entire request with 422 if even one match is not
  an included candidate).
- [x] `apps/api/src/easysynq_api/services/ingestion/review.py:721` — splitting a version family
  reset a human-selected effective member to the total-order pick `[C]` (fixed: recompute the
  structural member order but preserve the prior effective member when it survives; fall back to
  the total-order pick only when that member was separated, while retaining the family
  reconstruction flag and recording the effective choice in the structural audit).
- [x] `apps/api/src/easysynq_api/services/ingestion/review.py:484` — bulk selectors silently
  stopped at 5,000 matches while oversized explicit `file_ids` correctly failed `[C]` (fixed: query
  at most configured-max-plus-one as an overflow probe and return 422 without writes instead of
  applying a truncated prefix).

### ☑ M7 — Records and rendering resilience — [#387](https://github.com/CoJoA13/EasySynQ/pull/387)

`branch: fix/minor-record-render-resilience` · API + workers + migrations `0077`/`0078` + docs +
unit/integration tests · no new permission key

- [x] `apps/api/src/easysynq_api/services/packs/build.py:358` — sealed packs used the mutable System
  Default retention policy rather than a guaranteed permanent policy `[C]` (fixed: packs now pin a
  reserved, API-immutable `PERMANENT` + `RETAIN_PERMANENT` policy; migration `0077` seeds it and
  re-pins existing pack records, preserving and renaming any pre-existing same-name user policy;
  the build path normalizes/lazily creates the distinct stable-id policy for future organizations).
- [x] `apps/api/src/easysynq_api/services/records/render.py:144` — a delayed render could recreate a
  destroyed record's rendition without a remaining purge path `[C]` (fixed: the row-locked builder
  and redrive selector reject a `DESTROY` tombstone while allowing content-preserving
  `ARCHIVE_COLD`/`TRANSFER`; integration proves a post-destroy delayed build leaves the pointer,
  blob row, and rendition object absent).
- [x] `apps/api/src/easysynq_api/services/records/service.py:308` — a failed structured-record PDF
  build/enqueue had no re-drive and left rendition retrieval at 409 forever `[C]` (fixed: an hourly
  bounded Beat task re-enqueues pinned-form records with a missing pointer, isolates per-record
  publish failures for the next tick, and keeps GET as a pure poll; omitted optional forms normalize
  to `{}`, legacy `NULL` forms are recognized from the pinned schema, and ad-hoc JSON records such as
  `KPI_READING` are excluded from enqueue/build/redrive. The scan keyset-pages beyond malformed
  legacy schema snapshots, so an invalid oldest page cannot starve later valid renditions.
  Migration `0078` marks historical record seals as v1 and new captures as v2, preserving the
  legacy empty-form preimage while v2 hashes `{}` distinctly from the `NULL` ad-hoc sentinel;
  exported pack manifests and dossier subjects carry that version beside every record hash).

### ☑ M8 — Vault and retention input guards — [#388](https://github.com/CoJoA13/EasySynQ/pull/388)

`branch: fix/minor-vault-retention-guards` · API + integration

- [x] `apps/api/src/easysynq_api/services/records/retention_policies.py:232` — explicit JSON nulls
  in a retention-policy patch reached `.strip()`/NOT NULL failures as 500s `[f]` (fixed: PATCH now
  distinguishes omission from explicit null, rejects null for the five required policy fields with
  a field-specific `422 validation_error`, and preserves deliberate null-clearing for nullable
  `applies_to` and `worm_lock_period`; the OpenAPI contract documents the same split).
- [x] `apps/api/src/easysynq_api/services/vault/service.py:441` — `init_upload` could overwrite the
  lock holder's scratch pointer without proving the caller owns the checkout `[f]` (fixed:
  init-upload row-locks the working-draft mirror, requires `checked_out_by` to match the caller,
  and CAS-refreshes its token against authoritative Redis before presigning, before committing
  scratch, and immediately before returning. Break-lock takes the document + same draft-row locks
  before clearing Redis, so scratch commit and invalidation cannot interleave; a mismatched user,
  lapsed lock, or concurrent invalidation returns the contract-declared `409 lock_conflict` without
  leaking an upload URL. Regression coverage proves the recovery pointer follows that serialized
  order).

### ☑ M9 — Migration and ORM coherence — [#389](https://github.com/CoJoA13/EasySynQ/pull/389)

`branch: fix/minor-migration-orm-coherence` · migration `0079` + historical migration corrections +
populated migration tests · no new permission key

- [x] `migrations/versions/0004_seed_authz.py:487` — downgrade seed deletes abort once role
  assignments or permission overrides reference the rows `[C]` (fixed: remove grants and roles only
  when the seeded role has no live assignment, then retain every permission referenced by a
  preserved grant or direct override; an assigned role keeps its complete authority bundle rather
  than surviving as an empty shell).
- [x] `migrations/versions/0018_seed_clauses.py:105` — downgrade's unguarded clause delete aborts
  once mappings exist `[C]` (fixed: clear the self-parent links, delete only clauses with no
  `clause_mapping`, and let re-upgrade idempotently restore the complete catalog and parent tree).
- [x] `migrations/versions/0028_retention_policy_crud.py:167` — retention grants are skipped after
  an operational install renames the organization short code `[C]` (fixed: resolve every matching
  org-scoped QMS Owner/Internal Auditor role without consulting the bootstrap code; migration
  `0079` idempotently backfills installations that already crossed the faulty upgrade).
- [x] `migrations/versions/0019_process_ia.py:179` — the live `process_edge` CHECK name is doubled
  while the ORM expects the canonical undoubled name `[C]` (fixed: the historical create now passes
  the bare naming-convention token, while `0079` renames the deployed doubled constraint, tolerates
  an already-canonical database, and removes a duplicate legacy copy if both exist).

### ☑ M10 — Schema and index design — [#390](https://github.com/CoJoA13/EasySynQ/pull/390)

`branch: fix/minor-schema-index-design` · schema decision + migration `0080` + populated migration
tests + docs · no new permission key

- [x] `apps/api/src/easysynq_api/db/models/blob.py:27` — a global SHA-256 primary key paired with
  one bucket/org cannot represent identical bytes in two buckets or organizations `[R]` (rejected:
  D1 is explicitly single-organization and the documented/modelled blob identity is intentionally
  the global content hash with one canonical storage placement. Existing vault, ingestion, and
  records guards reject a digest collision across storage domains instead of mutating WORM
  provenance; the foreign-bucket check-in integration test pins that distinction. PR review found
  that the ingestion and records first-writer paths still trusted a stale pre-insert `None` after
  losing their conflict-tolerant insert. Both now re-read and validate the authoritative winner,
  with regressions that force the stale observation and prove no foreign-domain reference is
  created. Supporting multiple physical placements for one digest would require a separate
  placement schema and coordinated backup, verification, retrieval, disposition, and purge
  changes—not a minor PK adjustment. The model docs now state the single-placement contract
  explicitly).
- [x] `apps/api/src/easysynq_api/db/models/document_version.py:73` — `change_summary` is dead and
  nullable while the data-model contract calls it mandatory at check-in `[C]` (fixed: the API and
  every check-in path already use mandatory `change_reason` plus `change_significance` for INV-3;
  migration `0080` removes the unused nullable field and fails closed if unsupported manually
  inserted summary data exists, while downgrade restores the nullable compatibility column. The
  contradictory document-control and data-model prose now names the fields actually enforced).
- [x] `apps/api/src/easysynq_api/db/models/record.py:63` — `record.source_document_id` is unindexed
  despite backing real filters and joins `[C]` (fixed: add the plain
  `ix_record_source_document_id` index in migration `0080` and matching ORM metadata).
- [x] `apps/api/src/easysynq_api/db/models/role.py:64` — `role_assignment.user_id` has no index for
  the per-request grant-resolution path; do not add the intentionally-absent blanket uniqueness
  constraint `[C]` (fixed: add only the plain `ix_role_assignment_user_id` index in migration
  `0080` and matching ORM metadata, preserving all existing assignment cardinality).

### ☑ M11 — Organization time and audit scalability — [#391](https://github.com/CoJoA13/EasySynQ/pull/391)

`branch: fix/minor-org-time-audit-scale` · API + unit/integration + docs · no migration and no new
permission key

- [x] `apps/api/src/easysynq_api/api/objectives.py:416` — objective attainment uses server-local
  `date.today()` instead of organization-time `today_org()` `[C]` (fixed: route every objective
  serializer through the canonical request-scoped R56 organization clock; a pinned post-due
  organization date proves the old host-local implementation returns the opposite attainment
  band).
- [x] `apps/api/src/easysynq_api/services/audit/verify.py:64` — on-demand chain verification loads
  the entire organization chain as ORM rows in memory `[C]` (fixed: consume the ordered chain
  through a server-side scalar stream in 500-row batches, retaining only the current row, running
  hash, count, and reported breaks. Bounded windows still seed from the immediately preceding
  linked hash; checkpoint attestation, pending-tail accounting, and the response shape are
  unchanged).
- [x] `apps/api/src/easysynq_api/services/audits/service.py:608` — audit start/completion dates use
  the UTC calendar day instead of the organization calendar day `[C]` (fixed: stamp
  `started_at`/`completed_at` from `today_org()` while keeping `audit_event.occurred_at` on the
  authoritative UTC instant clock).

---

## Documentation, infrastructure, and naming

### ☑ M12 — Data-model documentation drift — [#392](https://github.com/CoJoA13/EasySynQ/pull/392)

`branch: fix/minor-data-model-docs` · docs only unless revalidation finds missing implementation

- [x] `docs/14-data-model.md:286` — documents a `rendition` table although renditions are pointer
  columns `[C]` (fixed: remove the entity/relationship and document the shipped
  `document_version.rendition_blob_sha256` FK plus deliberately non-FK
  `record.structured_pdf_blob_sha256` cache pointer).
- [x] `docs/14-data-model.md:312` — documents an unshipped `requirement_link` satellite table
  `[C]` (fixed: remove it and identify `evidence_for_link(target_type=clause)` as the shipped
  direct record-to-requirement traceability edge).
- [x] `docs/14-data-model.md:126` — documents an unshipped `delegation` table (and doc 15 names
  nonexistent delegation permission keys) `[C]` (fixed: migration `0003` confirms delegation is
  v1.x-deferred; remove the table/API/resolution claims and `delegation.read/create/revoke` keys,
  while retaining `delegation.administer` and `on_behalf_of` only as explicit reserved hooks).
- [x] `docs/14-data-model.md:80` — documents seven config tables that were never created `[C]`
  (fixed: replace the ERD/table with the shipped `organization`, `system_config`,
  `storage_config`, `backup_policy`, `numbering_counter`, `mirror_build`, and `drift_scan` shape,
  map each former domain concept to its real v1 storage/configuration owner, and reconcile doc
  15's setup/backup inventory with the shipped endpoints and backing rows).
- [x] `docs/14-data-model.md:241` — depicts class-table document/record inheritance despite the
  shipped single kind-discriminated base and contradicts the same document `[C]` (fixed: establish
  `documented_information` as the one `kind=DOCUMENT|RECORD` root, keep only `record` as the
  retained shared-PK satellite, and point document versions/drafts/links/distribution directly to
  the root throughout the ERD and API inventory).

### ☑ M13 — API documentation drift — [#393](https://github.com/CoJoA13/EasySynQ/pull/393)

`branch: fix/minor-api-docs` · docs + OpenAPI prose

- [x] `docs/15-api-design.md:524` — documents unshipped NC/complaint promotion endpoints `[C]`
  (fixed: preserve the shipped, idempotent complaint→CAPA route and remove
  `/complaints/{id}/spawn-ncr` plus `/ncrs/{id}/promote-capa`; align the neighboring list/detail
  filters, expansions, request shapes, and the replay-time `capa.read` gate with the current CAPA
  router and OpenAPI).
- [x] `docs/15-api-design.md:184` — documents unshipped auth-session and `/me/actions` endpoints
  `[C]` (fixed: document the SPA's direct OIDC/PKCE relationship with Keycloak, its memory-only
  user/token store and redirect-request state in browser `localStorage`, JIT on the first valid bearer
  request, the absence of API-owned session/refresh/logout routes, the reserved non-enforcing
  step-up seam, and `/tasks` as the canonical self-scoped work inbox).
- [x] `docs/15-api-design.md:457` — documents unshipped task claim/reassign/escalate and workflow
  definition endpoints `[C]` (fixed: inventory only task list/detail/decision plus workflow
  instance/approval reads, state their real self/candidate or subject-derived gates, identify
  `assignee` as an ignored compatibility parameter, preserve the key-independent sentinel replay
  contract for auto-skipped tasks, keep notification preferences in the resource inventory, and
  record definitions and manual task mutations as non-public/deferred. PR review also moved the
  future-only `/dashboards/my-work` aggregation—and its nonexistent `dashboard.read` key—out of
  the shipped endpoint inventory; `/tasks` remains the shipped work queue).
- [x] `docs/15-api-design.md:339` — names absent permission keys on never-built delete/folder
  routes `[C]` (fixed: identify `folder_path` as a logical document scope value with no entity,
  router, or `folder.*` keys; remove the nonexistent document DELETE/`document.delete`, while
  retaining `document.delete_draft` only as a seeded route-less policy seam, and correct both
  duplicated check-in inventory rows to the handler's `document.edit` gate).

### ☑ M14 — Infrastructure, deploy, and public edge — [#394](https://github.com/CoJoA13/EasySynQ/pull/394)

`branch: fix/minor-infra-public-edge` · env + container build + compose + runbook + Caddy

- [x] `.env.example:15` — omits required PostgreSQL and public-site variables from the documented
  copy-and-run path `[R]` (rejected on current `main`: the template now declares
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `SITE_ADDRESS`, `PUBLIC_BASE_URL`, and
  `APP_BASE_URL`; the runbook's supported path invokes `install.sh`, which copies that template,
  generates the secrets, and fills the browser origins before Compose starts).
- [x] `apps/web/Dockerfile:6` — `npm ci || npm install` tolerates lock drift and the missing
  `.dockerignore` admits host artifacts into build context `[C]` (fixed: require the committed
  lockfile and a successful `npm ci`, and add an app-context `.dockerignore` that excludes
  `node_modules`, build/test output, caches, logs, and local env files).
- [x] `docs/runbooks/install-online.md:16` — says the installer uses the owner DSN although it now
  provisions the non-owner app role `[R]` (rejected on current `main`: step 2 already says
  role-separated credentials; `install.sh` gives `DATABASE_URL` to `easysynq_app` and reserves the
  owner for `DATABASE_URL_SYNC`).
- [x] `infra/compose/compose.yml:64` — `minio-init` does not receive audit-sink
  credential/retention settings and falls back to mismatched hard-coded values `[C]` (revalidation
  found the four write/read credential passthroughs already present; the surviving omission is
  fixed by declaring `WORM_RETENTION=30d` in the template and passing the selected value to
  `minio-init`).
- [x] `infra/compose/compose.yml:167` — the default import source resolves under `infra/compose`
  rather than the documented repository root `[C]` (fixed: both the Compose fallback and generated
  env template use `../../.import-source`, which resolves to the repository root; the runbook now
  recommends an absolute path for an operator-selected tree).
- [x] `infra/compose/caddy/Caddyfile:40` — public API-served HTML lacks CSP and frame protection
  `[C]` (fixed: route the exact verify and evidence-pack landing paths through a dedicated,
  script-free CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, and a no-referrer policy
  before the generic API handler).

### ☑ M15 — Cross-stack naming — [#395](https://github.com/CoJoA13/EasySynQ/pull/395)

`branch: fix/minor-cross-stack-naming` · API/web/docs; stable persisted and public contracts retained

- [x] `apps/api/src/easysynq_api/api/mgmt_review.py:88` — Management Review uses four naming
  dialects across API, code, and events `[C]` (fixed at the avoidable Python layer: API, domain,
  service, and task modules now use `management_review`, as do router aliases, helpers, task
  functions, and ORM configuration attributes. Stable contracts remain deliberately unchanged:
  `mgmtReview.*` permission keys, `mr.*` event topics, `mgmt_review` enum/metadata values, physical
  database columns, and the registered Celery task name).
- [x] `apps/api/src/easysynq_api/api/risk.py:205` — singular/plural module and feature naming
  diverges across the cloned register families `[R]` (rejected on current source: `risk`,
  `context`, and `interested_parties` are grammatical bounded-context names, while their shipped
  routes correctly use the collection/domain forms `/risks`, `/context`, and
  `/interested-parties`; each router's list/get/create/update names agree with its resource.
  Forcing one number across all three would create worse names and churn stable public paths
  without removing ambiguity).
- [x] `apps/api/src/easysynq_api/db/models/workflow.py:69` — the same active/effective flag pattern
  ships under inconsistent column spellings `[C]` (fixed: `WorkflowDefinition.is_effective`,
  `RetentionPolicy.is_active`, and `SlaPolicy.is_active` now follow the predicate-style ORM
  convention. Explicit `mapped_column("effective" | "active", ...)` names preserve the deployed
  schema, partial index, and API `active` response field, so no migration or payload change occurs).
- [x] `apps/web/src/app/shell/LeftRail.tsx:49` — the web UI calls the domain “ingestion” while API,
  permission, table, and navigation contracts call it “import” `[C]` (fixed: `/imports` and
  `/imports/{runId}` are canonical, and navigation, breadcrumbs, browser titles, links, and tests
  consistently say Import. Redirect-only `/ingestion` compatibility preserves run ids and query
  strings for bookmarks; the technical `features/ingestion` package remains named for the pipeline
  activity and that layering is documented).

### ☑ M16 — UI copy and terminology — [#396](https://github.com/CoJoA13/EasySynQ/pull/396)

`branch: fix/minor-ui-copy-terminology` · API problem copy + web + focused tests

- [x] `apps/api/src/easysynq_api/services/risk/lifecycle.py:151` — lowercase, unpunctuated problem
  details are rendered verbatim in user alerts `[C]` (fixed: every risk-lifecycle problem detail
  now begins with a capital and ends with punctuation; a focused unit proof pins the empty-register
  detail that the publish modal renders verbatim).
- [x] `apps/web/src/admin/ConfigAdmin.tsx:65` — user copy mixes “organisation” and “organization”
  `[C]` (fixed: notification configuration, delivery health, and personal notification settings
  consistently use the product's existing en-US “organization” spelling).
- [x] `apps/web/src/features/audits/AuditsListPage.tsx:148` — heading capitalization conflicts
  with the dominant sentence-case convention `[C]` (fixed: the audit list states, audit-detail
  breadcrumb, and programme page use sentence-case “Internal audit” / “Audit programme”; proper
  role names and operator-entered programme titles remain unchanged).
- [x] `apps/web/src/features/capa/SpawnCapaModal.tsx:36` — “Spawn CAPA” exposes developer jargon
  where sibling surfaces use “Raise” `[C]` (fixed: complaint and risk user actions, modal title,
  submit label, and fallback copy now say “Raise”; stable `spawn-capa` routes, response fields,
  hooks, and technical component names retain their contract terminology).
- [x] `apps/web/src/features/document/DocumentDetailPage.tsx:98` — error copy mixes incompatible
  contraction and retry conventions `[C]` (fixed: a generic detail-load failure uses the shared
  “Couldn't load…” / “Please try again.” `ErrorState` with a working retry action, while the
  distinct 403/404 messages and Library return path remain intact).
- [x] `apps/web/src/features/library/FacetBar.tsx:27` — lifecycle filters/chips expose raw enum
  values instead of canonical labels `[C]` (fixed: the filter and removable chip reuse the
  document badge's canonical label mapping, so `InReview` and `UnderRevision` render as “In
  review” and “Under revision” without changing stable URL values).
- [x] `apps/web/src/features/risk/RisksRegisterPage.tsx:148` — the risk register has four
  user-facing names `[C]` (fixed: navigation, page states, lifecycle confirmation, and the Home
  unpublished summary consistently call it the “Risk & opportunity register”, with normal
  sentence-context capitalization).

### ☑ M17 — Test false-PASS traps — [#397](https://github.com/CoJoA13/EasySynQ/pull/397)

`branch: test/minor-false-pass-traps` · API integration + web unit tests · no production change,
migration, or new permission key

- [x] `apps/api/tests/integration/test_backfill_review_dates.py:37` — skips unless another test
  happens to leave a suitable document behind `[C]` (fixed: provision a unique Effective document
  through the real lifecycle harness, corrupt that exact row, require its precise old/new tuple,
  restore the canonical date, and prove a second backfill omits it; a clean shard now passes instead
  of skipping).
- [x] `apps/api/tests/integration/test_org_clock.py:141` — detects its timezone regression during
  only 14 of 24 UTC hours `[C]` (fixed: freeze the serializer at an instant whose Kiritimati date is
  one day ahead of UTC, seed a known-wrong UTC request context, and require authentication to
  replace it. Removing `set_request_org_tz` now deterministically yields `due_soon` instead of
  `overdue` at every wall-clock hour).
- [x] `apps/web/src/features/objectives/hooks.test.tsx:19` — the only web test file relying on
  implicit Vitest globals remains green only because the environment masks the deviation `[C]`
  (fixed: import `expect` and `it` from Vitest explicitly; the focused file passes with globals
  disabled as well as under strict TypeScript and the full web suite).

---

## Web consistency and usability

### ☑ M18 — Web shell, layout, and error consistency — [#398](https://github.com/CoJoA13/EasySynQ/pull/398)

`branch: fix/minor-web-shell-layout` · web + vitest

- [x] `apps/web/src/admin/RolesAdmin.tsx:67` — admin drawers use bare unnamed loaders `[C]`
  (fixed: role details and process owners now render compact, explicitly named `LoadingState`
  regions whose labels identify the role or process).
- [x] `apps/web/src/app/shell/Breadcrumb.tsx:47` — global breadcrumbs expose raw UUIDs/slugs `[C]`
  (fixed: every current static route segment has product copy, every non-document detail family
  receives a generic entity label, unknown slugs are humanized, and document identifiers retain
  their reactive cache-backed label).
- [x] `apps/web/src/features/audits/AuditDetailPage.tsx:92` — audit detail duplicates the global
  breadcrumb `[C]` (fixed: remove the local breadcrumb while preserving the audit identifier in
  the page header).
- [x] `apps/web/src/features/audits/AuditsListPage.tsx:149` — equivalent page titles use different
  heading levels `[C]` (fixed: loaded, forbidden, and error states consistently render the audit
  page title at the register convention's level 2).
- [x] `apps/web/src/features/audits/AuditsListPage.tsx:150` — only three feature families prefix
  create buttons with a fullwidth plus `[C]` (fixed: remove the prefix from every remaining action
  label across library, authoring, ingestion, audit, and CAPA surfaces).
- [x] `apps/web/src/features/capa/CapaLayout.tsx:24` — CAPA navigation and content widths do not
  align `[C]` (fixed: the shared tab strip follows the active face's existing width — `xl` for the
  board and `lg` for Complaints/NCRs).
- [x] `apps/web/src/features/dcr/DcrsRegisterPage.tsx:126` — DCR width changes between loading and
  loaded states `[C]` (fixed: forbidden, loading, error, and loaded states all use `xl`).
- [x] `apps/web/src/features/improvement/ImprovementRegisterPage.tsx:105` — improvement width
  changes between loading/error and loaded states `[C]` (fixed: forbidden, loading, error, and
  loaded states all use `xl`).
- [x] `apps/web/src/features/management-review/ManagementReviewDetailPage.tsx:66` — MR detail
  hand-rolls a no-retry forbidden/error panel instead of the shared states `[C]` (fixed: 403 uses
  `NoAccessState`; genuine load failures use `ErrorState` with a working query refetch).

### ☑ M19 — Web state and badge consistency — [#399](https://github.com/CoJoA13/EasySynQ/pull/399)

`branch: fix/minor-web-state-badges` · web + vitest

- [x] `apps/web/src/admin/UsersAdmin.tsx:129` — user states use a local palette and raw all-caps
  enums instead of `StatusBadge` and humanized labels `[C]` (fixed: the exhaustive
  `UserStatusBadge` maps every known state to canonical label/tone/glyph semantics and degrades a
  future additive state to readable neutral copy).
- [x] `apps/web/src/features/audits/FindingPanel.tsx:8` — duplicates and has drifted from the
  canonical CAPA close-state labels `[C]` (fixed: the panel now consumes the same
  `CapaStateBadge`/`CLOSE_STATE_LABEL` source as the CAPA feature, including “Implementation” and
  “Verification”).
- [x] `apps/web/src/features/audits/ProgrammePage.tsx:97` — programme status invents glyphs and raw
  colors instead of `StatusBadge` `[C]` (fixed: Active and Archived use canonical success/neutral
  status treatments and accessible names).
- [x] `apps/web/src/features/capa/CapaDrawer.tsx:68` — CAPA close state has no canonical badge
  treatment `[C]` (fixed: one exhaustive close-state label/tone map now drives a shared
  `CapaStateBadge`; live lifecycle phases are informational, Closed is success, and Rejected is
  danger).
- [x] `apps/web/src/features/capa/NcrsPage.tsx:90` — NCR severity bypasses the domain's shared
  `SeverityBadge` `[C]` (fixed: NCR rows now render the existing exhaustive severity badge and its
  non-color glyph).
- [x] `apps/web/src/features/dcr/DcrStageTimeline.tsx:35` — raw DCR state casing leaks into the
  timeline and filter `[C]` (fixed: badge, timeline, and filter all consume one exhaustive
  `DCR_STATE_META`, so `InApproval` is consistently presented as “In approval”).
- [x] `apps/web/src/features/document/AcknowledgementsTab.tsx:127` — acknowledgment state uses a
  local color map and raw enum labels `[C]` (fixed: Acknowledged, Pending, and Overdue now use an
  exhaustive human label/tone map through `StatusBadge`).
- [x] `apps/web/src/features/ingestion/ItemDetailDrawer.tsx:269` — conflict/history badges expose
  raw machine tokens and color-only danger `[C]` (fixed: decision history has curated read-side
  labels for accept/correct/merge/split/exclude/defer plus a neutral additive fallback; proposal
  conflicts use curated or humanized labels and canonical danger glyphs).
- [x] `apps/web/src/features/ingestion/KindCell.tsx:52` — confirmed kind is the only filled badge
  and its contrast fails in both schemes `[C]` (fixed: confirmed Document/Record kinds use the
  AA-paired `StatusBadge` treatment while retaining their existing inline SVG domain icons).

### ☑ M20 — Web visual tokens and semantics — [#400](https://github.com/CoJoA13/EasySynQ/pull/400)

`branch: fix/minor-web-visual-tokens` · web + accessibility-focused vitest

- [x] `apps/web/src/features/capa/CloseGateStepper.tsx:31` — equivalent progress steppers use three
  unrelated visual treatments `[C]` (fixed: CAPA close requirements, document approval progress,
  and audit lifecycle now render through one ordered, accessible `LifecycleStepper` with canonical
  status glyphs and domain copy supplied by each caller).
- [x] `apps/web/src/features/compliance/CompliancePage.tsx:63` — compliance rollup uses an emoji
  anti-pattern inconsistent with the signal below `[C]` (fixed: the overdue count consumes the
  canonical danger glyph used by the row-level overdue badge).
- [x] `apps/web/src/features/document/ApprovalStepper.tsx:112` — hard-coded white bypasses the
  inverse-text token in dark mode `[C]` (fixed: strong lifecycle markers use
  `--es-text-inverse`; pending markers use the re-keyed surface/text/border token pair).
- [x] `apps/web/src/features/ingestion/ConfidenceCell.tsx:43` — ambiguity caption reintroduces a
  purged pictograph style `[C]` (fixed: the secondary signal is explicit “Ambiguous
  classification” copy with no decorative pictograph).
- [x] `apps/web/src/features/risk/RiskMatrix.tsx:42` — light-only SVG palette variables make dark
  mode axis text low contrast `[C]` (fixed: band fills, grid, axis/cell text, and selection ring all
  consume dark-rekeyed `--es-*` semantic tokens).
- [x] `apps/web/src/features/search/SearchResultRow.tsx:23` — clause chips have three different
  representations across primary surfaces `[C]` (fixed: one outlined, explicitly named
  `ClauseBadge` now serves Search, Library, the artifact header, and the Controlled Document
  Register while preserving mandatory-star meaning).

### ☑ M21 — Web async and error UX — [#401](https://github.com/CoJoA13/EasySynQ/pull/401)

`branch: fix/minor-web-async-errors` · web + vitest

- [x] `apps/web/src/admin/UsersAdmin.tsx:212` — drawer action failures render behind the overlay
  `[C]` (fixed: role and override mutations now own a drawer-local dismissible error surface; page
  actions retain their page-level alert).
- [x] `apps/web/src/app/shell/Breadcrumb.tsx:51` — breadcrumbs link to nonexistent routes `[C]`
  (revalidation found M18 already removed the raw slug/UUID half of the original report; the
  remaining `/documents`, `/reports`, `/settings`, and `/dcrs/:id` dead parents now render as
  orientation text rather than catch-all-redirect links).
- [x] `apps/web/src/features/ingestion/ItemDetailDrawer.tsx:49` — item-read failures become an
  infinite loading spinner `[C]` (fixed: loading, failed, and loaded reads are distinct; failures
  render the shared `ErrorState` with a working refetch).
- [x] `apps/web/src/features/ingestion/TriageTable.tsx:53` — fetch failure masquerades as an empty
  queue `[C]` (fixed: the files query's error/refetch state is threaded into the table, which shows
  a retryable error and suppresses stale pagination instead of the empty-queue message).
- [x] `apps/web/src/features/review/AckInbox.tsx:70` — bulk acknowledgment has no pending guard, so
  double-clicks produce a false failure summary `[C]` (fixed: an immediate ref guard closes the
  same-tick gap, while loading/disabled controls prevent selection or resubmission until the one
  request set settles).
- [x] `apps/web/src/features/review/CapaApprovalContext.tsx:13` — CAPA read failure leaves the
  review context spinning forever `[C]` (fixed: loading, forbidden, and genuine error states are
  explicit; genuine failures expose query retry).
- [x] `apps/web/src/features/review/ReviewApprovePage.tsx:340` — pending/failed subject resolution
  is mislabeled “already decided” `[C]` (fixed: only a non-pending task gets the decided alert;
  instance-to-document resolution has named loading and retryable error states).
- [x] `apps/web/src/features/risk/RiskDetailDrawer.tsx:44` — CAPA-spawn errors persist when the
  drawer switches risks `[C]` (fixed: the spawn mutation/error leaf is keyed to the selected risk,
  so the persistent drawer cannot carry one row's failure into another).

### ☑ M22 — Web accessibility

`branch: fix/minor-web-accessibility` · web + vitest/jest-axe/manual keyboard checks

- [x] `apps/web/src/admin/UsersAdmin.tsx:301` — sibling revoke/remove controls receive identical
  accessible names `[C]` (fixed: every control names its specific role or permission/effect/scope
  override, so repeated row actions are distinguishable).
- [x] `apps/web/src/app/shell/DetailDrawer.tsx:44` — resize handle has no keyboard or non-drag
  pointer operation `[C]` (fixed: the WAI-ARIA window-splitter pattern exposes current/min/max
  width and supports Arrow Left/Right plus Home/End; named narrow/widen buttons provide a
  single-pointer alternative while drag remains available).
- [x] `apps/web/src/features/document/RedlineViewer.tsx:90` — unchanged-context text is roughly
  3.2:1 at 14px `[C]` (fixed: unchanged context uses the scheme-aware secondary-text token,
  reaching at least 6.37:1 in light mode and 7.55:1 in dark mode).
- [x] `apps/web/src/features/document/VisualDiffViewer.tsx:275` — interactive page-rail text is
  roughly 3.2:1 `[C]` (fixed: inactive page buttons use the same AA-safe secondary-text token;
  active pages retain the accent treatment and `aria-current`).
- [x] `apps/web/src/features/home/StatLine.tsx:19` — RAG tone is hidden from assistive technology
  `[C]` (fixed: each named signal group announces the domain meaning—On track, Needs attention,
  Action required, or Informational—rather than a raw colour word).
- [x] `apps/web/src/features/library/LibraryPage.tsx:178` — clickable/focusable table rows have no
  interactive role or accessible name `[C]` (fixed: rows retain native table semantics and no
  longer masquerade as controls; each identifier is an explicit, focusable, fully named detail
  action).
- [x] `apps/web/src/index.css:16` — forced-colors mode loses the global focus indicator `[C]`
  (fixed: a forced-colors override restores a two-pixel system `Highlight` outline with offset
  when box shadows are suppressed).

---

_The source review remains the detailed failure-scenario record. This tracker owns grouping,
sequencing, revalidation state, fixes, and PR links._
