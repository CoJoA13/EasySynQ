# Remediation plan — 2026-07-22 full review (MINOR findings)

> Working tracker for the **104 MINOR** findings in
> [`review-2026-07-22.md`](./review-2026-07-22.md). The source review grouped findings only by
> broad subsystem; this document regroups them into implementation-sized batches and records their
> current disposition.

## Inventory and conventions

- The historic denominator remains **104**. One finding was already closed while the MAJOR work was
  underway, leaving **103** to schedule here. Batch M1 closed 4; M2 closes 2; **97 remain queued**
  after M2.
- A queued finding is not assumed to still be live. Every batch must re-locate and revalidate its
  findings against then-current `main` before implementation; close, re-scope, or reject it with
  evidence rather than mechanically applying the 2026-07-22 suggestion.
- One branch and one ready (non-draft) PR per implementation batch. Each PR updates its own status,
  records the verified fix, and links the PR. Squash-merge remains owner-controlled.
- `[C]` means confirmed against current source, `[f]` preserves the source review's finder-only
  status pending batch-time revalidation. Line numbers are historical pointers and may drift.
- M0 is an accounting bucket, not a new PR. It prevents an already-closed finding from silently
  disappearing or being fixed twice.

## Status at a glance

| Batch | Group | Findings | Status | PR |
|---|---|:---:|---|---|
| M0 | Preclosed during MAJOR remediation | 1 | ☑ merged | [#369](https://github.com/CoJoA13/EasySynQ/pull/369), precision follow-up [#371](https://github.com/CoJoA13/EasySynQ/pull/371) |
| M1 | Auth boundaries and public diagnostics | 4 | ☑ merged | [#381](https://github.com/CoJoA13/EasySynQ/pull/381) |
| M2 | Scoped authorization and reporting | 2 | ☑ in PR | [#382](https://github.com/CoJoA13/EasySynQ/pull/382) |
| M3 | Notification reliability | 2 | ☐ queued — revalidate | — |
| M4 | Lifecycle and workflow guards | 4 | ☐ queued — revalidate | — |
| M5 | Ingestion commit integrity | 3 | ☐ queued — revalidate | — |
| M6 | Ingestion review and family integrity | 4 | ☐ queued — revalidate | — |
| M7 | Records and rendering resilience | 3 | ☐ queued — revalidate | — |
| M8 | Vault and retention input guards | 2 | ☐ queued — revalidate | — |
| M9 | Migration and ORM coherence | 4 | ☐ queued — revalidate | — |
| M10 | Schema and index design | 4 | ☐ queued — revalidate | — |
| M11 | Organization time and audit scalability | 3 | ☐ queued — revalidate | — |
| M12 | Data-model documentation drift | 5 | ☐ queued — revalidate | — |
| M13 | API documentation drift | 4 | ☐ queued — revalidate | — |
| M14 | Infrastructure, deploy, and public edge | 6 | ☐ queued — revalidate | — |
| M15 | Cross-stack naming | 4 | ☐ queued — revalidate | — |
| M16 | UI copy and terminology | 7 | ☐ queued — revalidate | — |
| M17 | Test false-PASS traps | 3 | ☐ queued — revalidate | — |
| M18 | Web shell, layout, and error consistency | 9 | ☐ queued — revalidate | — |
| M19 | Web state and badge consistency | 9 | ☐ queued — revalidate | — |
| M20 | Web visual tokens and semantics | 6 | ☐ queued — revalidate | — |
| M21 | Web async and error UX | 8 | ☐ queued — revalidate | — |
| M22 | Web accessibility | 7 | ☐ queued — revalidate | — |

**Accounting: 1 preclosed + 4 merged + 2 in PR + 97 queued = 104 original findings.**

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

### ☐ M3 — Notification reliability

`branch: fix/minor-notification-reliability` · API + unit/integration

- [ ] `apps/api/src/easysynq_api/api/notifications.py:69` — a negative notification-list limit
  reaches PostgreSQL as an invalid negative `LIMIT` and returns 500 `[f]`.
- [ ] `apps/api/src/easysynq_api/services/notifications/digest.py:151` — a missing `digest.daily`
  template still stamps `digested_at`, silently dropping retryable items `[f]`.

### ☐ M4 — Lifecycle and workflow guards

`branch: fix/minor-lifecycle-workflow-guards` · API + integration

- [ ] `apps/api/src/easysynq_api/services/improvement/service.py:219` — terminal Closed/Cancelled
  initiatives remain editable `[f]`.
- [ ] `apps/api/src/easysynq_api/services/dcr/repository.py:85` — same-transaction DCR stage events
  can tie on `occurred_at` and render out of order without a stable tiebreaker `[f]`.
- [ ] `apps/api/src/easysynq_api/services/dcr/service.py:397` — impact annotations remain editable
  on terminal DCRs `[f]`.
- [ ] `apps/api/src/easysynq_api/services/workflow/engine.py:521` — an unresolvable quorum
  conditional is overwritten from `NEEDS_ATTENTION` to a plausible `REJECTED` terminal `[f]`.

### ☐ M5 — Ingestion commit integrity

`branch: fix/minor-ingestion-commit-integrity` · API + integration

- [ ] `apps/api/src/easysynq_api/services/ingestion/commit.py:338` — the validated and audited
  folded owner decision is ignored during commit `[f]`.
- [ ] `apps/api/src/easysynq_api/services/ingestion/commit.py:530` — `_record_failed` can commit a
  false failure audit after a peer successfully committed the item `[f]`.
- [ ] `apps/api/src/easysynq_api/services/ingestion/review.py:253` — an empty identifier survives
  validation and commits a vault document with `identifier=''` `[f]`.

### ☐ M6 — Ingestion review and family integrity

`branch: fix/minor-ingestion-review-integrity` · API + integration

- [ ] `apps/api/src/easysynq_api/services/ingestion/review.py:477` — bulk “accept all HIGH”
  silently supersedes a prior explicit EXCLUDE `[C]`.
- [ ] `apps/api/src/easysynq_api/services/ingestion/review.py:487` — the bulk-selector path skips
  the `included_candidate` guard used by the other paths `[f]`.
- [ ] `apps/api/src/easysynq_api/services/ingestion/review.py:721` — splitting a version family
  resets a human-selected effective member to the total-order pick `[f]`.
- [ ] `apps/api/src/easysynq_api/services/ingestion/review.py:484` — bulk selectors silently stop
  at 5,000 matches while oversized explicit `file_ids` correctly fail `[f]`.

### ☐ M7 — Records and rendering resilience

`branch: fix/minor-record-render-resilience` · API + workers + integration

- [ ] `apps/api/src/easysynq_api/services/packs/build.py:358` — sealed packs use a mutable System
  Default retention policy rather than a guaranteed permanent policy `[f]`.
- [ ] `apps/api/src/easysynq_api/services/records/render.py:144` — a delayed render can recreate a
  destroyed record's rendition without a remaining purge path `[f]`.
- [ ] `apps/api/src/easysynq_api/services/records/service.py:308` — a failed structured-record PDF
  build/enqueue has no re-drive and leaves rendition retrieval at 409 forever `[f]`.

### ☐ M8 — Vault and retention input guards

`branch: fix/minor-vault-retention-guards` · API + integration

- [ ] `apps/api/src/easysynq_api/services/records/retention_policies.py:232` — explicit JSON nulls
  in a retention-policy patch reach `.strip()`/NOT NULL failures as 500s `[f]`.
- [ ] `apps/api/src/easysynq_api/services/vault/service.py:441` — `init_upload` can overwrite the
  lock holder's scratch pointer without proving the caller owns the checkout `[f]`.

### ☐ M9 — Migration and ORM coherence

`branch: fix/minor-migration-orm-coherence` · migrations + migration tests

- [ ] `migrations/versions/0004_seed_authz.py:487` — downgrade seed deletes abort once role
  assignments or permission overrides reference the rows `[f]`.
- [ ] `migrations/versions/0018_seed_clauses.py:105` — downgrade's unguarded clause delete aborts
  once mappings exist `[f]`.
- [ ] `migrations/versions/0028_retention_policy_crud.py:167` — retention grants are skipped after
  an operational install renames the organization short code `[C]`.
- [ ] `migrations/versions/0019_process_ia.py:179` — the live `process_edge` CHECK name is doubled
  while the ORM expects the canonical undoubled name `[C]`.

### ☐ M10 — Schema and index design

`branch: fix/minor-schema-index-design` · schema decision + migration + integration

- [ ] `apps/api/src/easysynq_api/db/models/blob.py:27` — a global SHA-256 primary key paired with
  one bucket/org cannot represent identical bytes in two buckets or organizations `[f]`.
- [ ] `apps/api/src/easysynq_api/db/models/document_version.py:73` — `change_summary` is dead and
  nullable while the data-model contract calls it mandatory at check-in `[f]`.
- [ ] `apps/api/src/easysynq_api/db/models/record.py:63` — `record.source_document_id` is unindexed
  despite backing real filters and joins `[f]`.
- [ ] `apps/api/src/easysynq_api/db/models/role.py:64` — `role_assignment.user_id` has no index for
  the per-request grant-resolution path; do not add the intentionally-absent blanket uniqueness
  constraint `[f]`.

### ☐ M11 — Organization time and audit scalability

`branch: fix/minor-org-time-audit-scale` · API + integration

- [ ] `apps/api/src/easysynq_api/api/objectives.py:416` — objective attainment uses server-local
  `date.today()` instead of organization-time `today_org()` `[C]`.
- [ ] `apps/api/src/easysynq_api/services/audit/verify.py:64` — on-demand chain verification loads
  the entire organization chain as ORM rows in memory `[f]`.
- [ ] `apps/api/src/easysynq_api/services/audits/service.py:608` — audit start/completion dates use
  the UTC calendar day instead of the organization calendar day `[f]`.

---

## Documentation, infrastructure, and naming

### ☐ M12 — Data-model documentation drift

`branch: fix/minor-data-model-docs` · docs only unless revalidation finds missing implementation

- [ ] `docs/14-data-model.md:286` — documents a `rendition` table although renditions are pointer
  columns `[f]`.
- [ ] `docs/14-data-model.md:312` — documents an unshipped `requirement_link` satellite table
  `[f]`.
- [ ] `docs/14-data-model.md:126` — documents an unshipped `delegation` table (and doc 15 names
  nonexistent delegation permission keys) `[f]`.
- [ ] `docs/14-data-model.md:80` — documents seven config tables that were never created `[f]`.
- [ ] `docs/14-data-model.md:241` — depicts class-table document/record inheritance despite the
  shipped single kind-discriminated base and contradicts the same document `[f]`.

### ☐ M13 — API documentation drift

`branch: fix/minor-api-docs` · docs + OpenAPI prose

- [ ] `docs/15-api-design.md:524` — documents unshipped NC/complaint promotion endpoints `[f]`.
- [ ] `docs/15-api-design.md:184` — documents unshipped auth-session and `/me/actions` endpoints
  `[f]`.
- [ ] `docs/15-api-design.md:457` — documents unshipped task claim/reassign/escalate and workflow
  definition endpoints `[f]`.
- [ ] `docs/15-api-design.md:339` — names absent permission keys on never-built delete/folder
  routes `[f]`.

### ☐ M14 — Infrastructure, deploy, and public edge

`branch: fix/minor-infra-public-edge` · env + container build + compose + runbook + Caddy

- [ ] `.env.example:15` — omits required PostgreSQL and public-site variables from the documented
  copy-and-run path `[f]`.
- [ ] `apps/web/Dockerfile:6` — `npm ci || npm install` tolerates lock drift and the missing
  `.dockerignore` admits host artifacts into build context `[f]`.
- [ ] `docs/runbooks/install-online.md:16` — says the installer uses the owner DSN although it now
  provisions the non-owner app role `[f]`.
- [ ] `infra/compose/compose.yml:64` — `minio-init` does not receive audit-sink credential/retention
  settings and falls back to mismatched hard-coded values `[f]`.
- [ ] `infra/compose/compose.yml:167` — the default import source resolves under `infra/compose`
  rather than the documented repository root `[f]`.
- [ ] `infra/compose/caddy/Caddyfile:40` — public API-served HTML lacks CSP and frame protection
  `[f]`.

### ☐ M15 — Cross-stack naming

`branch: fix/minor-cross-stack-naming` · API/web/docs; re-scope before renaming persisted contracts

- [ ] `apps/api/src/easysynq_api/api/mgmt_review.py:88` — Management Review uses four naming
  dialects across API, code, and events `[f]`.
- [ ] `apps/api/src/easysynq_api/api/risk.py:205` — singular/plural module and feature naming
  diverges across the cloned register families `[f]`.
- [ ] `apps/api/src/easysynq_api/db/models/workflow.py:69` — the same active/effective flag pattern
  ships under inconsistent column spellings `[f]`.
- [ ] `apps/web/src/app/shell/LeftRail.tsx:49` — the web UI calls the domain “ingestion” while API,
  permission, table, and navigation contracts call it “import” `[f]`.

### ☐ M16 — UI copy and terminology

`branch: fix/minor-ui-copy-terminology` · API problem copy + web + focused tests

- [ ] `apps/api/src/easysynq_api/services/risk/lifecycle.py:151` — lowercase, unpunctuated problem
  details are rendered verbatim in user alerts `[f]`.
- [ ] `apps/web/src/admin/ConfigAdmin.tsx:65` — user copy mixes “organisation” and “organization”
  `[f]`.
- [ ] `apps/web/src/features/audits/AuditsListPage.tsx:148` — heading capitalization conflicts
  with the dominant sentence-case convention `[f]`.
- [ ] `apps/web/src/features/capa/SpawnCapaModal.tsx:36` — “Spawn CAPA” exposes developer jargon
  where sibling surfaces use “Raise” `[f]`.
- [ ] `apps/web/src/features/document/DocumentDetailPage.tsx:98` — error copy mixes incompatible
  contraction and retry conventions `[f]`.
- [ ] `apps/web/src/features/library/FacetBar.tsx:27` — lifecycle filters/chips expose raw enum
  values instead of canonical labels `[f]`.
- [ ] `apps/web/src/features/risk/RisksRegisterPage.tsx:148` — the risk register has four
  user-facing names `[f]`.

### ☐ M17 — Test false-PASS traps

`branch: test/minor-false-pass-traps` · API integration + web unit tests

- [ ] `apps/api/tests/integration/test_backfill_review_dates.py:37` — skips unless another test
  happens to leave a suitable document behind `[f]`.
- [ ] `apps/api/tests/integration/test_org_clock.py:141` — detects its timezone regression during
  only 14 of 24 UTC hours `[f]`.
- [ ] `apps/web/src/features/objectives/hooks.test.tsx:19` — the only web test file relying on
  implicit Vitest globals remains green only because the environment masks the deviation `[f]`.

---

## Web consistency and usability

### ☐ M18 — Web shell, layout, and error consistency

`branch: fix/minor-web-shell-layout` · web + vitest

- [ ] `apps/web/src/admin/RolesAdmin.tsx:67` — admin drawers use bare unnamed loaders `[C]`.
- [ ] `apps/web/src/app/shell/Breadcrumb.tsx:47` — global breadcrumbs expose raw UUIDs/slugs `[C]`.
- [ ] `apps/web/src/features/audits/AuditDetailPage.tsx:92` — audit detail duplicates the global
  breadcrumb `[C]`.
- [ ] `apps/web/src/features/audits/AuditsListPage.tsx:149` — equivalent page titles use different
  heading levels `[C]`.
- [ ] `apps/web/src/features/audits/AuditsListPage.tsx:150` — only three feature families prefix
  create buttons with a fullwidth plus `[C]`.
- [ ] `apps/web/src/features/capa/CapaLayout.tsx:24` — CAPA navigation and content widths do not
  align `[C]`.
- [ ] `apps/web/src/features/dcr/DcrsRegisterPage.tsx:126` — DCR width changes between loading and
  loaded states `[C]`.
- [ ] `apps/web/src/features/improvement/ImprovementRegisterPage.tsx:105` — improvement width
  changes between loading/error and loaded states `[C]`.
- [ ] `apps/web/src/features/management-review/ManagementReviewDetailPage.tsx:66` — MR detail
  hand-rolls a no-retry forbidden/error panel instead of the shared states `[C]`.

### ☐ M19 — Web state and badge consistency

`branch: fix/minor-web-state-badges` · web + vitest

- [ ] `apps/web/src/admin/UsersAdmin.tsx:129` — user states use a local palette and raw all-caps
  enums instead of `StatusBadge` and humanized labels `[C]`.
- [ ] `apps/web/src/features/audits/FindingPanel.tsx:8` — duplicates and has drifted from the
  canonical CAPA close-state labels `[C]`.
- [ ] `apps/web/src/features/audits/ProgrammePage.tsx:97` — programme status invents glyphs and raw
  colors instead of `StatusBadge` `[C]`.
- [ ] `apps/web/src/features/capa/CapaDrawer.tsx:68` — CAPA close state has no canonical badge
  treatment `[C]`.
- [ ] `apps/web/src/features/capa/NcrsPage.tsx:90` — NCR severity bypasses the domain's shared
  `SeverityBadge` `[C]`.
- [ ] `apps/web/src/features/dcr/DcrStageTimeline.tsx:35` — raw DCR state casing leaks into the
  timeline and filter `[C]`.
- [ ] `apps/web/src/features/document/AcknowledgementsTab.tsx:127` — acknowledgment state uses a
  local color map and raw enum labels `[C]`.
- [ ] `apps/web/src/features/ingestion/ItemDetailDrawer.tsx:269` — conflict/history badges expose
  raw machine tokens and color-only danger `[C]`.
- [ ] `apps/web/src/features/ingestion/KindCell.tsx:52` — confirmed kind is the only filled badge
  and its contrast fails in both schemes `[C]`.

### ☐ M20 — Web visual tokens and semantics

`branch: fix/minor-web-visual-tokens` · web + accessibility-focused vitest

- [ ] `apps/web/src/features/capa/CloseGateStepper.tsx:31` — equivalent progress steppers use three
  unrelated visual treatments `[C]`.
- [ ] `apps/web/src/features/compliance/CompliancePage.tsx:63` — compliance rollup uses an emoji
  anti-pattern inconsistent with the signal below `[C]`.
- [ ] `apps/web/src/features/document/ApprovalStepper.tsx:112` — hard-coded white bypasses the
  inverse-text token in dark mode `[C]`.
- [ ] `apps/web/src/features/ingestion/ConfidenceCell.tsx:43` — ambiguity caption reintroduces a
  purged pictograph style `[C]`.
- [ ] `apps/web/src/features/risk/RiskMatrix.tsx:42` — light-only SVG palette variables make dark
  mode axis text low contrast `[C]`.
- [ ] `apps/web/src/features/search/SearchResultRow.tsx:23` — clause chips have three different
  representations across primary surfaces `[C]`.

### ☐ M21 — Web async and error UX

`branch: fix/minor-web-async-errors` · web + vitest

- [ ] `apps/web/src/admin/UsersAdmin.tsx:212` — drawer action failures render behind the overlay
  `[f]`.
- [ ] `apps/web/src/app/shell/Breadcrumb.tsx:51` — breadcrumbs link to nonexistent routes `[f]`.
- [ ] `apps/web/src/features/ingestion/ItemDetailDrawer.tsx:49` — item-read failures become an
  infinite loading spinner `[f]`.
- [ ] `apps/web/src/features/ingestion/TriageTable.tsx:53` — fetch failure masquerades as an empty
  queue `[f]`.
- [ ] `apps/web/src/features/review/AckInbox.tsx:70` — bulk acknowledgment has no pending guard, so
  double-clicks produce a false failure summary `[f]`.
- [ ] `apps/web/src/features/review/CapaApprovalContext.tsx:13` — CAPA read failure leaves the
  review context spinning forever `[f]`.
- [ ] `apps/web/src/features/review/ReviewApprovePage.tsx:340` — pending/failed subject resolution
  is mislabeled “already decided” `[f]`.
- [ ] `apps/web/src/features/risk/RiskDetailDrawer.tsx:44` — CAPA-spawn errors persist when the
  drawer switches risks `[f]`.

### ☐ M22 — Web accessibility

`branch: fix/minor-web-accessibility` · web + vitest/jest-axe/manual keyboard checks

- [ ] `apps/web/src/admin/UsersAdmin.tsx:301` — sibling revoke/remove controls receive identical
  accessible names `[f]`.
- [ ] `apps/web/src/app/shell/DetailDrawer.tsx:44` — resize handle has no keyboard or non-drag
  pointer operation `[f]`.
- [ ] `apps/web/src/features/document/RedlineViewer.tsx:90` — unchanged-context text is roughly
  3.2:1 at 14px `[f]`.
- [ ] `apps/web/src/features/document/VisualDiffViewer.tsx:275` — interactive page-rail text is
  roughly 3.2:1 `[f]`.
- [ ] `apps/web/src/features/home/StatLine.tsx:19` — RAG tone is hidden from assistive technology
  `[f]`.
- [ ] `apps/web/src/features/library/LibraryPage.tsx:178` — clickable/focusable table rows have no
  interactive role or accessible name `[f]`.
- [ ] `apps/web/src/index.css:16` — forced-colors mode loses the global focus indicator `[f]`.

---

_The source review remains the detailed failure-scenario record. This tracker owns grouping,
sequencing, revalidation state, fixes, and PR links._
