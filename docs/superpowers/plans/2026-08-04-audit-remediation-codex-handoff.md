# Audit remediation plan revision — Codex handoff to Claude

> **Purpose:** revise `2026-08-04-audit-remediation.md` into an evidence-correct,
> implementation-ready programme. This is a planning pass, not authorization to implement fixes.
>
> **Reviewed revision:** `376ec1e1a01ccc3f8a8faf27172f2c9a994bd4b2`
>
> **Preserve:** keep the original audit, Claude proposal, and current untracked evidence unchanged so
> the validation trail remains inspectable. Produce a separate
> `docs/superpowers/plans/2026-08-04-audit-remediation-v2.md`.

## Assignment

Read these together:

1. `audit-results/2026-08-03/AUDIT.md`
2. `docs/superpowers/plans/2026-08-04-audit-remediation.md`
3. This handoff
4. The cited source, tests, decisions register, and slice-history residuals

Then produce a v2 programme that:

- accounts for every original finding exactly once;
- corrects the factual record for Claude's NEW-03 through NEW-08;
- separates existing test coverage from genuine verification deltas;
- resolves high-risk storage, recovery, release, and authentication semantics before scheduling
  implementation;
- gives every slice one coherent authority/chokepoint, executable acceptance criteria, dependencies,
  rollback conditions, and fresh verification commands;
- keeps documentation truthful after every merge rather than depending on a later wave to reverse an
  intentional contradiction.

Do not implement production changes during this revision pass.

## Disposition of Claude's validation

The original audit remains a credible release-blocking assessment. The Codex review found no basis to
refute an original factual finding. Claude also correctly identified the core facts behind NEW-01 and
NEW-02, the unignored audit evidence, the release-identity family, and several strong execution rules.

The current proposal is nevertheless **REVISE**, not approved for execution. Its principal defects are:

- one original finding is absent from the programme;
- four alleged verification gaps are contradicted by tests that run in CI;
- the Celery redelivery premise and subsystem counts are wrong;
- multiple release-blocking fixes are attached at the wrong lifecycle or authority boundary;
- Wave 4 is a sizing table rather than an executable plan;
- some owner decisions ask questions already resolved by the implementation;
- several slices combine unrelated defects despite the proposal's shared-chokepoint rule.

## Non-negotiable factual corrections

### 1. Restore M-01 to the ledger and programme

`M-01` appears at `audit-results/2026-08-03/AUDIT.md:247` and nowhere in the proposal. It covers
fail-open realm/config/checkpoint backup legs, plaintext degradation when no key exists, and the
single-current-key restore limitation in
`apps/api/src/easysynq_api/services/backup/drill.py:331-421`.

Do not hide it under C-01 without naming it. The recovery programme must explicitly own both C-01 and
M-01.

### 2. Rewrite NEW-04 through NEW-07 as narrower verification deltas

The proposal says audit verification, mirror regeneration, R27 disposition, and workflow negative paths
are invisible to every gate. That is false at HEAD. Relevant coverage includes:

- `apps/api/tests/integration/test_audit.py:168-399`: chain verification, privileged tamper, signed checkpoint,
  off-host soft gate, freshness, and missing-row behavior;
- `apps/api/tests/integration/test_mirror.py:162-235` and
  `apps/api/tests/integration/test_mirror_scan.py:214-274`: Effective-only rebuild, whole-tree
  replacement, tamper quarantine, and rescan;
- `apps/api/tests/integration/test_verify.py:139-217`: verify QR and rerender behavior;
- `apps/api/tests/integration/test_records_disposition.py:485-750` and
  `apps/api/tests/integration/test_records_disposition.py:1211-1354`: dual control, physical deletion,
  COMPLIANCE refusal, outages, reaper recovery, concurrency, and crash recovery;
- `apps/api/tests/integration/test_workflow_engine.py:228-519` and
  `apps/api/tests/unit/test_workflow_engine_domain.py:122-153`: quorum, distinct approvers, early
  failure, fail-closed conditions, cycles, replay, and concurrency.

The audit's 22% workflow number is unit-only coverage; it does not imply that the remaining behavior is
untested by integration tests.

Replace `S-verify-invariants` with an inventory plus separate delta slices. The credible remaining deltas
include:

- audit pending-tail age/count policy and checkpoint-history threat coverage;
- a real Gotenberg/Office-conversion and production-browser artifact proof;
- Celery worker/broker process failure injection around R27 and other durable tasks;
- any behavior shown missing after combined unit and integration coverage is measured.

### 3. Rewrite NEW-08 around the effective Celery contract

Fresh effective configuration at HEAD is:

```text
task_acks_late=True
task_reject_on_worker_lost=None
worker_cancel_long_running_tasks_on_connection_loss=False
broker_transport_options={}
beat_schedule_count=25
registered task modules=14
```

`task_acks_late=True` alone does not make hard-kill redelivery routine. In locked Celery 5.6.3,
`WorkerLostError` is acknowledged unless `reject_on_worker_lost` is true. The real problem is that the
project has not declared and proven a delivery contract per task; some failures may strand work, while
connection-loss behavior can create duplicates.

The replacement slice must classify tasks by at-most-once/at-least-once expectations, set explicit
worker/broker options, and kill real worker processes before and after commit. Acceptance must prove
terminal state, no duplicate business or audit effects, reaper recovery where applicable, and no
infinite poison-message loop.

### 4. Split NEW-03 into baseline drift, optional MFA, and deferred step-up

The realm does not implement "none" of the documented policy. It has brute-force protection, a
12-character password floor, public-client PKCE, temporary-password first-login behavior, and effective
Keycloak default lifetimes. A live read showed a 300-second access token, 1800-second SSO idle timeout,
and 36000-second SSO maximum.

Confirmed drift remains:

- no refresh-token rotation;
- password-history and other documented baseline settings are not encoded;
- production retains broad localhost wildcard callbacks;
- authentication/admin event logging is disabled;
- the documented EasySynQ authentication-event trail is not implemented;
- no realm-wide MFA enrollment policy is enforced.

The documentation also describes MFA as optional and Part-11 step-up as deferred in places. Separate:

1. mandatory shipped-baseline hardening;
2. optional MFA enrollment, with mandatory recovery and break-glass policy for enrolled users;
3. deferred Part-11 step-up and `acr`/`amr` enforcement.

Represent these as `NEW-03a`, `NEW-03b`, and `NEW-03c` in the corrected ledger, retaining NEW-03 only as
the superseded umbrella reference. Give each sub-ID its own severity and primary slice.

Changing `realm-export.json` repairs only fresh installations. Existing realms require an idempotent
Admin API reconciliation with export/rollback, enrollment/recovery, session-impact handling, and exact
per-install redirect origins. If baseline auth drift remains High, schedule it before auth-shell work,
not in Wave 4.

### 5. Correct the H-02 database-grant slice

The proposal identifies five unrestricted tables but omits `blob` from `S-db-grants`. A fresh database
query showed full `DELETE, INSERT, SELECT, UPDATE` for the application role on all five:

- `blob`
- `document_version`
- `evidence_blob`
- `import_decision`
- `record`

`apps/api/src/easysynq_api/db/models/blob.py:8-10` says that only `verified_at` and
`verify_failed_at` are mutable operational stamps. Include `blob`, use column-scoped updates, and do not
preserve blanket application-role DELETE merely because R27 needs deletion. Put destructive
blob/evidence operations behind an authority-bound function, role, or equivalent database control,
following the `pending_blob_purge` precedent.

Keep H-02 High unless the integrity guarantee itself is narrowed. The tables are the core controlled
content/version/record boundary.

### 6. Remove digest binding from owner decision D-D

Approval already signs the exact candidate version and content digest in
`apps/api/src/easysynq_api/services/workflow/service.py:195-208`:

```text
signed_object_id = result.version.id
content_digest = result.version.source_blob_sha256
```

D-D should decide only raw download versus preview, explicit reviewer acknowledgement, and whether the
decision fails closed while content is loading, forbidden, missing, non-previewable, changed, or unable
to open. Add a regression that directly asserts both existing signature bindings. The displayed and
opened version ID and digest must still match the version signed at decision time; cover a concurrent
candidate/state change between page load, content open, and decision submission.

Do not defer the generic async-download/popup-blocking issue to a later wave if the Wave-0 approval gate
depends on that action. Do not carry L-10 wholesale into the approval slice: check-in feedback,
lifecycle copy, and first-release confirmation are separate surfaces and should be assigned deliberately.

### 7. Move organization-calendar conversion to the authoritative API boundary

`apps/web/src/features/dcr/DcrRaiseFields.tsx:32-35` converts a date to UTC midnight, while
`apps/api/src/easysynq_api/api/dcr.py:73-114` accepts arbitrary datetimes and the service persists them
unchanged. A JavaScript `orgLocalMidnightIso` helper fixes only one client.

Require date-only calendar intent at the authoritative API boundary and server-side resolution in the
organization's validated IANA timezone. If wire compatibility temporarily requires accepting the old
datetime field, normalize it server-side, reject ambiguous arbitrary instants, and publish an explicit
deprecation path. Keep display formatting separate. Acceptance must cover create, CAPA spawn, edit,
save, and read round trips for Chicago winter/summer, positive-offset zones, date boundaries,
midnight-transition zones, and unavailable timezone state.

### 8. Make release identity reach the running containers

Adding `image:` beside `build:` does not make a deployed image trustworthy. If H-08 is to be closed, a
manifest-only digest is not an owner-selectable alternative: the running stack must use the attested
digest, or the product claim must be narrowed.

Build each final image once; record platform digests in a signed release manifest; scan, sign, attest,
and bundle those exact images; use a release overlay with no `build:` entries and digest-only `image:`
values; prohibit network pulls and local builds for the offline install; and verify running container
image IDs against the manifest. Split build/release manifest, offline installation, and deployment/
rollback into reviewable slices even if they remain one dependency family.

### 9. Do not call the upgrade pre-backup a safety net until recovery is self-contained

NEW-02's narrow catch and ignored `verified` flag are real, but widening the stage-one catch does not
satisfy `run_upgrade`'s `Never raises` contract. `_alembic_head`, destination lookup, engine/session
setup, health checks, and audit commits can still escape. `docs/slice-history.md:107-123` also records
the unresolved lack of migration `lock_timeout`/`statement_timeout`.

Use one shared typed backup-result validator rather than duplicating guards. Add a single-flight lock,
operation identity, orphaned-STARTED reconciliation, idempotent retry, and terminal audit behavior.
Production upgrade eligibility must depend on the exact recovery generation passing a self-contained
restore/cutover proof; `S-backup-objects` therefore precedes or ships atomically with that eligibility.

### 10. Make accessibility a release gate

The proposal promotes M-18 to High but schedules it in Wave 3 while earlier slices are described as
individually shippable. State that no product release occurs before the accessibility gate passes, or
move accessibility acceptance for approval, authentication, and show-once-secret flows into their
owning earlier slices.

`jest-axe` does not exercise browser-computed color contrast. Require production-browser checks across
routes, themes, lifecycle states, 320/390 widths, zoom/reflow, keyboard order, focus restoration, and
portal-rendered content.

### 11. Reconcile severity independently from implementation effort

Use these as the starting positions in the v2 ledger:

- keep H-02 High because unrestricted mutation/deletion reaches the controlled content, version, and
  record integrity boundary;
- keep H-04 High while air-gapped installation is a supported product profile and a clean offline
  install fails completely;
- keep H-08 High unless the trusted-release/deployed-digest guarantee is explicitly narrowed;
- keep H-10 High while governed calendar dates are stored at the wrong instant for every non-UTC
  organization;
- H-12's downgrade to Medium is reasonable because the auditable reissue route prevents permanent
  lockout, although the loss/navigation defect remains real;
- M-18's upgrade to High is defensible because the stated WCAG bar fails across the complete route set;
- classify M-15 as Medium unless visual diff is itself a release-critical control, but keep it as an
  immediate release fix; urgency and low fix cost are different from severity;
- treat NEW-01 as release-blocking Critical or High only after the storage-admin threat boundary is
  explicit;
- keep NEW-02 High;
- assign separate severities to NEW-03's mandatory baseline, optional MFA, and deferred step-up parts;
- do not retain NEW-04 through NEW-07 as defects merely because the original browser journey did not
  exercise them manually; severity belongs only to the narrower residuals demonstrated after the test
  inventory.

## Additional slice corrections

- **Approval content:** do not render an enabled decision control until the candidate version and bytes
  are identified and openable. Show version/revision, filename or MIME where available, and SHA-256.
  Cover loading, 403, 404, 500, missing bytes, changed candidate, non-previewable MIME, and popup-blocked
  behavior. Use a synchronously opened tab or an ordinary authorized link after presigning. Split M-22:
  merely adding an unawaited distribution invalidation can still race the document refresh; acceptance
  must prove that `Effective` and `Not yet effective` are never rendered together.
- **Show-once secrets:** modal close predicates plus `beforeunload` do not protect SPA links, history,
  or programmatic navigation. Require a router-level navigation blocker, unload handling, explicit
  secure-capture acknowledgement, visible/announced clipboard success and failure, and the existing
  auditable reissue path. Test X, backdrop, Escape, app navigation, back/forward, reload, tab close, Done,
  and listener cleanup. Explicitly accept crash loss or design recoverable server escrow; do not imply
  that browser guards solve a process crash.
- **Setup and restore:** M-07 field hydration, M-09 durable restore-job recovery, and M-13 dirty-form
  protection are different systems. M-09 needs a persisted job ID/status contract so reload can resume
  queued/running/skipped/failed/passed state; snapshot hydration alone cannot fix it.
- **Capability truth:** a test that walks `require()` sees permission keys but misses dynamic
  `enforce()` calls, resolver identity, composite authority, auth-only routes, and ABAC/SoD state. Prefer
  a server-owned action manifest containing operation, action identity, permission, evaluator/scope, and
  composite checks, or persona contract tests for every visible action. Preserve per-field
  available/forbidden/error/not-published semantics in any dashboard aggregate and prove that ordinary
  shell/deep-link rendering creates no routine `ACCESS_DENIED` audit delta. Keep M-10 query errors and
  M-12 URL-selection state outside this authorization slice.
- **Auth shell and sessions:** require explicit loading, retryable failure, terminal failure,
  unauthenticated, authenticated, expired, and reauthentication states with preserved deep links and
  dirty-form behavior. M-03 also needs the Keycloak half: require `iat`, atomically advance and audit the
  local watermark, revoke Keycloak sessions/refresh capability, and define fail-closed behavior when
  Keycloak is unavailable. Test old and missing-`iat` tokens, disable/enable, refresh after revocation,
  equality boundaries, and a clean post-revocation login.
- **Edge trust:** Caddy CSP and token headers, Gunicorn/Uvicorn proxy trust, Host validation, FastAPI docs,
  rate/body limits, and liveness are not one chokepoint. The stock Caddy image has no rate-limit module.
  Keep static configuration tests but add production-edge requests and Playwright coverage for final
  headers, trusted versus untrusted forwarded IP, exact `/api`, Host behavior, token `no-store`/
  `no-referrer`, visual-diff `naturalWidth`, and CSP console errors.
- **Contract and CI gates:** `gen-contracts.sh --check` currently checks a lock hash, not generated
  authority. Decide whether generated artifacts are authoritative; then generate/compile/compare them or
  remove the dead claim. Contract tests must seed valid operation inputs and require intended 2xx
  responses before validating schemas. Split shared-database isolation, combined-coverage policy, and
  browser/release/edge gates.
- **Container hardening:** per-service env files are not a capability design. Inventory API, worker,
  maintenance, purge, backup/restore, Keycloak-admin, database-owner, and S3 governance-bypass authority.
  A non-root conversion also needs fixed UID/GID, populated-volume ownership preflight/migration,
  appliance parity, rollback, and proof that mirror, backup, and persistent signing-key paths remain
  writable without falling back to ephemeral keys.
- **Upload integrity:** H-03, browser double buffering, and upload size/back-pressure do not share one
  fix. Do not re-hash only after WORM promotion, because wrong bytes would already be locked. Verify the
  staging object before promotion or cryptographically bind and verify a presigned checksum. Cover
  document, record, and ingestion paths; omitted/altered headers; same-size wrong bytes; multipart;
  limits; dedup/concurrency; rejection audit; cleanup; storage failure; and bounded memory.

## High-risk designs required before implementation

The v2 programme must include or link short, decision-ready design records for these boundaries. Codex
and technical review make them ready for the owner; owner approval is the next gate after the complete
v2 package is presented, not a prerequisite for drafting it.

### WORM ownership and retention

Define:

- GOVERNANCE versus COMPLIANCE mode and the R27 consequence;
- the retention anchor/basis date;
- how one deduplicated object receives the maximum non-decreasing retention of every live owner;
- object VersionId persistence and repeated-copy races;
- propagation when a pinned policy is extended;
- `worm_lock_period=null`, event-based periods, `PERMANENT`, and legal holds;
- an inventory-first, resumable, auditable backfill that never shortens a lock;
- concurrency, partial storage failure, restart, and rollback behavior.

Applying retention only inside generic `finalize_worm(sha, bucket, source_bucket)` cannot satisfy these
requirements because policy ownership is resolved elsewhere and later deduplicated attachments skip
that storage call.

### Atomic recovery set

Define one generation containing mandatory database, object bytes, identity realm, configuration,
checkpoint, manifest, digests, and key ID. It is independently durable and visible as complete only after
every mandatory leg verifies. Specify consistency/quiesce behavior across PostgreSQL and MinIO, R27 and
older-backup policy, interruption/resume, extra/missing/corrupt objects, key rotation, capacity, RTO, and
restore into a stack with no source-vault access.

### Release and rollback contract

Define the signed release manifest, digest-only Compose inputs, offline bundle contents, image/platform
support, upgrade use of the target release image, database compatibility window, health gate, running-
digest verification, and rollback limits.

### Authentication baseline and migration

Define effective realm values rather than relying on absent JSON fields or mutable defaults. Cover fresh
install and existing-realm reconciliation, exact origins, auth-event ownership, active-session impact,
MFA enrollment/recovery/break-glass, rollback, and the source of truth for UI `mfa_enrolled` and signature
`acr`/`amr` data.

## Required programme structure

### Deliverable 1: complete finding ledger

Create a table with one row per original finding and per corrected new finding/sub-finding and these
columns:

| Field | Required content |
| --- | --- |
| ID and source | Original audit, coverage observation, or Claude completeness pass |
| Corrected claim | Narrow, falsifiable statement |
| Status | Confirmed, partial, refuted-as-phrased, or known residual |
| Severity | Impact-based rationale and release-blocking yes/no |
| Existing evidence | Source/runtime evidence and tests already in CI |
| Remaining delta | Exact behavior still unproven or defective |
| Owner decision | Decision ID or none |
| Owning slice | Exactly one primary slice; explicit remainder references only |
| Acceptance | Observable result and proof level |

Mechanically assert that all 57 original IDs appear exactly once as primary ownership. Claude-added
umbrella findings may be split into traceable sub-IDs, as required for NEW-03; the superseded umbrella
row is not counted as another primary owner. Remove duplicate primary ownership currently present for
M-27, M-32, and L-09.

### Deliverable 2: corrected slices

Each slice must contain:

- goal and finding IDs;
- one authority boundary or coherent chokepoint;
- explicit files/surfaces expected to change;
- dependencies and owner decisions;
- a falsifying proof appropriate to the slice; require a test observed RED against HEAD for a current
  executable defect, while inventory, design, documentation-only, and genuinely unproven verification
  slices instead require a mechanical check or an explicit new test-level justification;
- focused unit/integration/browser/runtime/failure-injection commands as appropriate;
- positive, negative, concurrency, recovery, and upgrade/backfill cases where applicable;
- migration/compatibility and rollback conditions;
- documentation changes that leave the repository truthful immediately after that slice;
- release-gate effect and residual risk.

Split at least these current groups:

- `S-verify-invariants` by subsystem;
- `S-edge-trust` into CSP/public-token headers, proxy/Host trust, API-doc exposure, and abuse/liveness;
- `S-setup-hydration` into setup field resume, durable restore-job recovery, and dirty-form conflicts;
- `S-web-capability-truth` away from query-error and URL-selection defects;
- `S-authoring-approval` away from the distribution-cache race;
- `S-release-identity` into independently testable build identity, offline installation, and deployment/
  rollback slices.

### Deliverable 3: corrected sequencing

Use this dependency order:

1. **Programme correction and baseline inventory:** complete ledger, existing-test inventory, corrected
   severities, duplicate ownership removal, and high-risk design decisions. No production edits.
2. **Integrity and recovery:** WORM design/implementation, atomic backup and restore including M-01 and
   C-01, upload identity before WORM promotion, database grants including `blob`, then upgrade safety and
   migration timeout semantics.
3. **Release trust:** build-once digest identity, exact offline bundle/install, deployed-digest proof,
   upgrade/deploy/rollback, container identities/volumes, and final-image security ratchet.
4. **Authentication and user-facing correctness:** mandatory Keycloak baseline plus existing-realm
   migration, auth/session state machine, approval content gate, organization-calendar contract, secret
   recovery/navigation guard, capability truth, and remaining edge fixes.
5. **Accessibility and responsive release gate:** browser-computed contrast, semantics, keyboard/focus,
   zoom/reflow, themes, lifecycle states, and mobile widths. No product release before this passes.
6. **Depth and governance deltas:** real renderer/browser proof, Celery failure injection, audit-tail and
   checkpoint-history policy, test isolation/combined coverage, contract authority, liveness, orphan
   reconciliation, and governance artifacts.

The ledger's release-blocking field is authoritative across all six stages: no release occurs until
every release-blocking slice passes. Stage 5 is an unconditional minimum gate; any Stage-6 integrity,
reliability, or governance delta classified release-blocking must also close before release.

Small independent corrections such as M-15's CSP token may be implemented early only after the v2 plan
defines a real Caddy/Playwright acceptance test. Do not advertise the pre-upgrade archive as a complete
safety net before the atomic recovery-set work lands.

## How to use Codex inside Claude

Use Codex as an independent evidence checker at bounded checkpoints, not as another vote counted by
agent quantity. Give it read-only assignments and require exact source/test/runtime evidence.

### Checkpoint A — ledger reconciliation

Ask Codex:

> Read the original audit, Claude proposal, Codex handoff, and proposed v2 ledger. Do not edit files.
> Mechanically verify that all 57 original IDs have exactly one primary owner, identify missing and
> duplicate ownership, distinguish existing CI coverage from unproven deltas, and report every claim
> contradicted by source or effective runtime configuration.

Do not proceed until the count and contradictions are reconciled in the ledger.

### Checkpoint B — risky-boundary adversarial review

Ask Codex to review each WORM, recovery, release, and authentication design independently. Require it to
construct failure scenarios involving deduplication, policy extension, object versions, concurrent R27,
partial backup generations, key rotation, offline installation from an empty cache, existing Keycloak
realms, active sessions, process death, retry, and rollback. Every surviving scenario must become an
acceptance criterion or an explicit owner-accepted residual.

### Checkpoint C — executable-plan review

Ask Codex:

> Review the v2 programme as an execution contract. For every slice, verify one coherent authority
> boundary, an appropriate falsifying proof, exact commands, dependency ordering,
> rollback/compatibility, immediate documentation truth, and no duplicate finding ownership. For a
> current executable defect, require a test observed RED against HEAD; do not manufacture RED tests for
> inventory, design, or documentation-only work. Flag any slice that is only a roadmap label or whose
> proposed defect test already passes against HEAD.

### Checkpoint D — implementation reviews

When implementation begins, use a fresh Codex review after each independently testable slice. The review
must inspect the actual diff and fresh test output, then challenge authorization, persistence,
concurrency, failure recovery, upgrades/backfills, documentation, and product claims relevant to that
slice. Do not infer combined success from subagent summaries; rerun integrated verification on the final
tree.

## Evidence already refreshed during Codex review

At the reviewed HEAD:

- focused unit checks passed: 56 tests covering checkpoint, chain verification, mirror, and workflow
  domain behavior;
- focused live integration checks passed: audit tamper/checkpoint, mirror/QR, workflow quorum/early-fail,
  and concurrent R27 purge;
- `uv lock --dry-run --upgrade-package cryptography --upgrade-package starlette` resolved
  `cryptography 50.0.0` and `starlette 1.3.1` without changing the lockfile;
- the application role had full DML on all five H-02 tables;
- effective Celery configuration and counts matched the values recorded above;
- the live Keycloak realm had implicit token/session defaults but lacked refresh rotation, MFA
  enforcement, and auth/admin events;
- `audit-results/` remained unignored with 89 files.

These results prove the specific statements above; they are not substitutes for the full gates required
by each eventual implementation slice.

## Completion gate for this planning pass

The v2 plan is ready for owner review only when all of the following are true:

- every original finding is accounted for exactly once;
- NEW-03 through NEW-08 use corrected claims and evidence;
- M-01 is explicit;
- the four risky-boundary designs are linked and decision-complete;
- every slice has executable acceptance, dependency, rollback, and documentation criteria;
- Wave 4 has been replaced by real slices;
- severity and release-blocking status agree with sequencing;
- Codex checkpoints A through C return no unresolved factual or structural blockers;
- no production source, protected audit evidence, operator data, or non-test live state was modified
  during the planning pass; isolated disposable verification fixtures may be created and destroyed.
