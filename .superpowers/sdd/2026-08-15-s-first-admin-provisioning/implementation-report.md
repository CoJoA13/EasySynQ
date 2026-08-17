# S-first-admin-provisioning implementation report

**Evidence date:** 2026-08-16

**Branch:** `codex/first-admin-bootstrap`

**Implementation compatibility baseline:** `1dcbc2bc12b14e11f037a657d44659412a7a39c0`

**Authority:** owner-approved design and plan, R64/R65/R66, ADR 0005, and AGENTS.md

This report records completed feature-branch implementation and fresh verification. It does not claim a
merge, deployment, SMTP configuration, or production acceptance beyond the exact live flow below.

## Observable outcome

- A fresh installation presents **Create the first administrator** in `/setup` before OIDC sign-in.
- The operator supplies the one-time EasySynQ bootstrap proof and the administrator profile; no Keycloak
  console, Keycloak subject, or Keycloak user-creation script is part of the supported flow.
- EasySynQ creates the Keycloak identity without a credential, persists the EasySynQ user and System
  Administrator assignment, then sets a generated temporary Keycloak credential.
- The temporary password is displayed once in component memory. After acknowledgment succeeds, the SPA
  clears the proof and password before sign-in. Keycloak forces replacement at first login.
- A matching acknowledgment replay is idempotent after proof expiry only for an already-consumed,
  complete claim with the administrator assignment present. Unconsumed expiry, mismatch, incomplete
  claim, or missing assignment fails closed.
- Usernames use one supported canonical form, `strip().lower()`, before the bootstrap claim, Keycloak
  lookup/create, response and audit projection, and ordinary later-user provisioning. Display names keep
  their submitted case.
- Administration → Users remains the later-user surface. `user.create`, `user.update`, and
  `permission.grant` stay separate. Every credential reset of another existing linked user requires
  `user.create` plus the unconditional system-tier guard under R64.
- SMTP and activation email are not prerequisites. The show-once temporary password is the approved
  handoff mechanism.

## Requirements and security mapping

| Authority | Implemented proof |
| --- | --- |
| R64 | Shared create-without-credential → database commit → temporary-credential ordering; no compensating Keycloak delete; no password persistence; `user.create` plus system tier for reset of any other linked user. |
| R65 | Removed `POST /api/v1/setup/bootstrap`, direct `bootstrap_admin`, fixed `qmsadmin` appliance creation, setup-sheet human password, and every repository consumer in the same slice. |
| R66 | Provisioning authority exists only in `UNINITIALIZED`; public acknowledgment also accepts only a narrowly fenced matching completed replay in `IN_SETUP`; durable single-identity claim; generic secret failure; bounded/serialized rate limiting; system audit actors; no bearer-token authority; retry-safe partial-failure recovery. |
| ADR 0005 | In-app first administrator, show-once credential, lowercase Keycloak username contract, optional profile reconciliation, expired consumed-proof replay, and deliberate Redis/PostgreSQL/Keycloak coupling are implemented and tested. |

The bootstrap proof, temporary password, Keycloak service credential, recovery marker, and Keycloak
subject are absent from audit payloads and public response projections. Request/response browser artifacts
are disabled for the live flow. The application never deletes a Keycloak account to compensate for a
later failure.

## Changed file families

- **Migration and persistence:** `0087_first_admin_bootstrap`, `system_config` claim/link fields, audit
  enum/model parity, populated downgrade/re-upgrade coverage.
- **Public contract and generated artifacts:** new provision/acknowledge endpoints and problem schemas,
  lowercase username descriptions, unconditional reset authority wording, generated Pydantic/TypeScript
  artifacts, canonical bundle, and contract lock.
- **API and identity services:** setup router/CLI, first-administrator orchestrator, shared identity
  provisioning primitives, Keycloak profile reconciliation, atomic Redis failure accounting, and ordinary
  `/users/provision` convergence.
- **Web setup:** pre-authenticated routing, `FirstAdministratorStep`, shared show-once password component,
  volatile-state clearing, sign-in continuation, setup and application tests.
- **Installation paths:** online/server/appliance manuals and runbooks, appliance provisioner, deleted
  `easysnq-create-user` helper, deployment guards, and setup-sheet convergence.
- **Browser acceptance:** isolated live Playwright entry/config/spec, Docker-backed shell harness, synthetic
  fixture preservation, and failure-only artifact policy.
- **Architecture and debt:** R64/R65/R66, ADRs 0003/0005, browser-harness narrowing, the claim/credential/
  profile/live-CI/admission/appliance debt records, and generator/hook debt payoff.

## Compatibility decisions

- Existing `OPERATIONAL` installations never re-enter bootstrap.
- Existing `IN_SETUP` installations whose earlier bootstrap was consumed continue through authenticated
  setup gates; nullable `0087` fields read as no pending claim on upgrade.
- The provisional authenticated bootstrap endpoint and fixed appliance human account have no compatibility
  shim under R65 because there is no completed supported production deployment to preserve.
- Keycloak remains the authentication and credential authority. Its internal service administrator remains
  an infrastructure secret and is not the human first administrator.
- Email, first name, and last name remain optional. EasySynQ removes only their `required` flags from the
  supported Keycloak profile and preserves unrelated attributes and validators.

## Commits

The implementation branch contains these exact reviewed commits after `origin/main`:

- Design/plan: `1b778ccbe23ef049925a917839d7c645581184eb`,
  `e4c5e6994f75861c4dfb77f9d2e5b2beee641fa9`.
- Task 1 contract: `8be9e3b97cdde3562581111f5fa021c3f5ffa46a`,
  `6bf3052f5eb7cd2c5a1a553fecf35d81f41b9657`,
  `8f8c9ce473383daaabec0913edbf0e936a0b7e51`.
- Task 2 migration: `3f67597b519751984e7ee12f87cc1bbfb907f0a3`.
- Task 3 shared identity boundary: `13453c246f64e9555a69914ffb33bce6d14e4354`,
  `a5702cfd527874e7a722053d4820ad0136bf3223`.
- Task 4 bootstrap orchestration: `31ad175d292b34537765bb066f0b5f4e76c43a19`,
  `0c2faab31c8061dfa3b233052dd3aaaadad3c07c`,
  `a4ff17245e88610927047a060871e66ccf1b3f77`.
- Task 5 later-user convergence: `f0f95a58485963e7f076e6f3dc09acfa6ce8ef33`,
  `2a39b63052eb209aa04f6274594ec45791849461`.
- Task 6 web flow: `1057928e26fb07a8d68400cc502601d22541ea87`,
  `8ed8c8927d22ebe3e9496f9cf04bc3aaf4cb1c86`,
  `ec6ebee936a31497dfa35842c254271a3dd04904`.
- Task 7 install convergence: `d650831e59da1f2bdef3dc0526199b787891f58b`,
  `6fc57db5352b02d18ae0c8f8608f33cf137e53f1`,
  `e45ca5e5ce08e8e717fe84e41aa86f718c5e70d4`,
  `a57e265834f79dea17f8b8cf05d4e27e7767fcf3`,
  `6a812b04fdac3b020ecfdd775bd9c4b83f4af1a8`,
  `591a7645c207d71c7b9046f77653b8a1e63eb90c`.
- Task 8 live acceptance: `543fc8b553cb68fd60100d4c8b6693d33b22edf4`,
  `b06b61bf2ef15cabc73d52661d107665250860b4`.
- Task 9 convergence/remediation: `c82af6e0325ef113bfa3b2420467bedadb56afa1`,
  `8926c00ab95a1d89d9f4310b3668dc22dedb4b6c`.

## Focused RED/GREEN and affected verification

Implementation tasks used focused failing proofs before production changes. The final Task 9 review wave
recorded these additional REDs and smallest GREENs:

- Backup convergence: the first complete integration job failed eight `test_backup.py` cases—six stale
  helper signatures and two missing fake-Keycloak seams. The bounded test-only fix passed all 16 backup
  integrations and committed as `c82af6e0325ef113bfa3b2420467bedadb56afa1`.
- Admission/identity/contract RED: 5 unit failures covered the atomic counter, first-admin and ordinary-user
  lowercase behavior, and stale contract/reset text. The smallest unit GREEN passed 5/5.
- Integration RED: 9 failures and 1 pass exposed concurrent invalid attempts all returning 403, missing
  inside-lock checks on both endpoints, no-TTL counter state, expired consumed replay, fail-closed replay
  seams, and mixed-case first/later-user identity behavior. The smallest GREEN passed 11/11 with only three
  known testcontainers deprecations.
- Generator RED: 13/14 initially exposed the missing final LF. The later commit-hook convergence RED passed
  14 and failed 2 formatter requirements; the generated-Ruff-header RED passed 16 and failed 1. Final
  `bash scripts/tests/test-gen-contracts.sh` passed 17/17.
- Final minor-review RED: 2 failures covered acknowledgment response descriptions and the doc 08 reset base
  permission; the same 2 passed after correction.

Final affected commands/results:

```text
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_first_admin_contract.py tests/unit/test_setup.py \
  tests/unit/test_setup_administrator.py tests/unit/test_keycloak_provisioning.py \
  tests/unit/test_identity_provisioning.py tests/unit/test_identity_onboarding_contract.py \
  tests/unit/test_deploy_configuration.py -q
=> 153 passed

cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_setup.py tests/integration/test_users_provision.py \
  tests/integration/test_backup.py -q
=> 98 passed; 3 known testcontainers deprecations

cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/migration/test_migration_coherence.py -q
=> 1 passed; known PostgreSQL testcontainers deprecation

npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx \
  src/SetupWizard.test.tsx src/App.test.tsx src/admin/CreateUserModal.test.tsx \
  src/admin/UsersAdmin.test.tsx
=> 5 files, 87 tests passed

npm --prefix apps/web run test:browser
=> 40/40 Chromium passed; one worker; zero retries
```

The final setup/users subset independently passed 82 tests, backup passed 16, deploy-configuration passed
77, the affected API unit selection passed 153, and the contract/generator harness passed 17.

## Complete-suite evidence

The controller ran every long command as a durable process job and supplied its persisted final result:

| Job | Exact command | Result |
| --- | --- | --- |
| `job-msvivlh1-fa8002ec` | `cd apps/api && uv run pytest tests/unit -m unit` | Exit 0; 1,789 passed; 1 expected release-only image-digest skip; 29.87s. |
| `job-msvie2dd-2d54f93d` | `cd apps/api && uv run pytest tests/integration -m integration` | Exit 0; 1,137 passed; 2 skipped; 284 deselected; 3 known testcontainers deprecations; 785.46s. |
| `job-msviwr7g-bfe4f534` | `cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract` | Exit 0; 284 passed; 3 known testcontainers deprecations; 297.42s. |
| `job-msvj3prd-02845479` | `npm --prefix apps/web test` | Exit 0; 267/267 files and 1,940/1,940 tests; 325.91s; known Node `localStorage` warning. |

No retry, timeout, deselection, skip, or setup failure was converted into a pass claim.

## Live Keycloak evidence

Controller-owned durable job `job-msvic4ai-947a887c` ran exactly:

```text
cwd: isolated first-admin-bootstrap worktree root
command: bash scripts/test-first-admin-keycloak.sh
```

It exited 0 with 1/1 Chromium test in 2.8 seconds, one worker, and zero retries. Against fresh Docker-backed
API, PostgreSQL, MinIO, Redis, worker/beat, and Keycloak services, the test:

1. submitted a mixed-case first-administrator username;
2. asserted the provision response returned the canonical lowercase username;
3. received the show-once temporary password;
4. signed in with the canonical username and completed mandatory password replacement;
5. returned authenticated to `/setup`; and
6. rejected the obsolete temporary password in a clean browser context.

The exact Compose project `easysynq-first-admin-da7bb53bc5d9` was torn down. Persisted output records removal
of its containers, named volumes, network, and all six locally built images. Independent inspection found no
repository `.env`, matching Docker resource/image, Playwright report, trace, screenshot, video, error
context, or test-result directory afterward.

## Static, generated, migration, authority, and topology evidence

```text
cd apps/api && uv run ruff format --check .
=> 750 files already formatted

cd apps/api && uv run ruff check .
=> passed

cd apps/api && uv run mypy src
=> no issues in 444 source files

npm --prefix apps/web run lint
=> exit 0

npm --prefix apps/web run build
=> exit 0; 1,107 modules; known Vite large-chunk advisory

just contracts-check
=> in sync at b0bf7d0ac437a85cd171096520fb9499e608577d45bb861fec0a8ad53065f78d

cd apps/api && uv run alembic heads
=> 0087_first_admin_bootstrap (head)

just authority-check
bash scripts/check-no-site-data.sh
git diff --check origin/main...HEAD
=> passed
```

Two consecutive real `just contracts` runs and `just contracts-check` produced stable hashes and status.
The generated Python model passed both Ruff lint and format checks. Executable parsing of
`.github/workflows/ci.yml` found exactly 11 job definitions and 15 expanded aggregate/leaf checks; the
dependency-free CI hardening contract passed 85/85.

## Independent review and correction rounds

Two independent whole-branch reviews covered the acceptance criteria, R64/R65/R66, public secrecy,
non-deletion, transaction/lock ordering, collision classification, rollback/ORM expiry, rate limiting,
bearer irrelevance, migration round trip, volatile browser state, installer convergence, live cleanup, and
changed-file test sufficiency.

The first blocker/fix round comprised:

- eight complete-integration backup convergence failures;
- two security Important findings: rate-limit check-then-act concurrency and split Redis `INCR`/`EXPIRE`
  state, plus confirmation of stale reset authority text;
- requirements Important findings: expired acknowledgment replay, mixed-case Keycloak username stranding,
  generated-contract EOF drift, and the same reset-authority mismatch.

The second round found two Minor documentation/contract issues: acknowledgment response descriptions and
doc 08's reset base permission. Focused tests failed before and passed after those corrections.

After remediation, the requirements reviewer and the security/code-quality reviewer each reported no
Critical, Important, or Minor finding. Commit `8926c00ab95a1d89d9f4310b3668dc22dedb4b6c` passed Ruff,
Ruff format, strict mypy, EOF/whitespace/merge/large-file guards, hardcoded-secret detection, repository
authority, and OpenAPI lint hooks.

## Review-hardening convergence — 2026-08-16

The approved S-first-admin-review-hardening work refined the completed slice without changing its
browser-first, no-SMTP outcome or R64 ordering/non-deletion boundary.

### Observable and security corrections

- Every successful temporary-password reset rotates a high-entropy `credential_receipt`; only its SHA-256
  digest is persisted. Provision returns the plaintext receipt beside the shown-once password, and the SPA
  retains both only in component memory. Acknowledgment proves the current setup secret and active receipt
  in constant time. A stale receipt returns `bootstrap_credential_superseded`, consumes nothing, and
  requires explicit reissue; a reminted setup secret can acknowledge the same still-current generation.
- Public bootstrap refuses every System Administrator assignment other than the user linked to the active
  claim, and performs that check only after the generic-denial setup proof boundary. It cannot be used as
  an administrator-existence oracle. A definitive create-time validation rejection releases only an
  unowned claim; timeouts, conflicts, marked identities, linked users, and ambiguous states retain it.
- Recovery retries keep the canonical username fixed and reconcile normalized display/profile values only
  on the exact marker-owned Keycloak identity and linked EasySynQ user. The whole-representation update
  preserves every unrelated Keycloak field, required action, federation link, custom attribute, and
  bootstrap marker.
- Supported production and developer first-install sections open public `/setup`, create the first
  administrator, save the shown credential, acknowledge its active generation, and only then sign in and
  change the temporary password. Demo fixture creation remains explicitly post-bootstrap.
- Host-only recovery uses `easysynq setup release-administrator-blocker` with an exact subject and optional
  organization code. It requires `UNINITIALIZED`, locks singleton then administrator set, refuses the claim owner,
  removes only the named user's System Administrator assignment, preserves the identity/user/other
  roles/history, rolls back on failure, and requires an independent incident/change record.
- The application API has exactly nine bearer-free operations in three authorization categories: public
  health/metadata/setup routing (`GET /healthz`, `GET /readyz`, `GET /auth/config`, `GET /setup/state`);
  bootstrap-secret-authorized mutations (`POST /setup/administrator`,
  `POST /setup/administrator/acknowledge`); and signed-capability-authorized access (`GET /verify`,
  `GET /evidence-packs/shared`, `GET /evidence-packs/shared/download`). Capability routes are authorized and
  scope-bounded, not anonymous QMS-content access. Ordinary operations, QMS content, and customer/site data
  remain authenticated and authorized.

Migration `0088_bootstrap_credential` adds the nullable bounded receipt digest. The populated coherence
proof independently traversed `0087 -> 0086 -> 0087` and `0088 -> 0087 -> 0088`, preserving identity and
setup state and restoring nullable storage without fabricating claim or receipt values. Alembic reports one
`0088_bootstrap_credential` head, making `0089` next.

### Focused final-tree evidence

```text
release-administrator-blocker integration selector
=> 8 passed, 70 deselected; 3 registered Testcontainers deprecations

administrator-blocker CLI/wrapper selector
=> 3 passed, 79 deselected

fresh-linux/install/first-admin/administrator-blocker content selector
=> 15 passed, 72 deselected

populated migration coherence
=> 1 passed; registered PostgreSQL Testcontainers deprecation

final authority/comment/negative-counter fix cohorts
=> 211 setup/authority/content units and 6 real-Redis integrations passed

exact nine-operation authority/OpenAPI guard cohort
=> 189 authority/deployment/content tests passed
```

The negative Redis-counter regression first failed because `-1` bypassed `_check_rate_limit`, then passed
after malformed negative reader state was routed through the existing redacted
`503 dependency_unavailable` boundary. The exact bearer-free guard mutations first demonstrated that an
added public `/documents` claim or removed authenticated boundary could pass the prior presence-only guard;
the final exact-set authority/OpenAPI guard rejects those mutations.

### Accepted durable evidence

| Job | Exact workload | Accepted result |
| --- | --- | --- |
| `job-mswq4zse-b59b5405` | `env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit -m unit` | Exit 0; 1,835 passed; 1 expected release-ceremony image-digest skip; 30.33s. |
| `job-mswq94op-73d090ea` | `env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration -m integration` | Exit 0; 1,162 passed; 2 expected shared-database skips; 284 deselected; 3 registered Testcontainers deprecations; 828.02s. |
| `job-mswlweou-9b17e09e` | `env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_contract_response_schemas.py -m contract` | Exit 0; 284/284 passed; 3 registered Testcontainers deprecations; 251.84s. |
| `job-mswm2hb9-92fba980` | `npm --prefix apps/web test` | Exit 0; 267/267 files and 1,944/1,944 tests; 299.02s; known Node `localStorage` diagnostics. |
| `job-mswm9e51-7a34ed2b` | `npm --prefix apps/web run test:browser` | Exit 0; 40/40 Chromium tests; 16.4s; one worker; zero retries; known Vite chunk, Node `localStorage`, and `NO_COLOR` diagnostics. |
| `job-mswlg4ft-b05efb99` | `bash scripts/test-first-admin-keycloak.sh` | Exit 0; 1/1 live Chromium test; 2.6s; one worker; zero retries; exact project `easysynq-first-admin-32b1b175ba35` removed its containers, volumes, network, and six local images. |

The API unit/integration jobs were rerun after the final runtime negative-counter fix. Contract, Vitest,
synthetic Chromium, and narrow live evidence remain accepted final-tree evidence because commits
`4ea6e78c5edf52a2666a6bee1130ace34ab19b56` and
`1f6f12def9ddb539711ceb4b382e3e28f4c1f87e` changed no API/OpenAPI response, contract, web/browser,
provider, installation, or exercised live-flow surface. No failed, cancelled, partial, unavailable,
skipped, deselected, warning-bearing, or retried result is upgraded beyond the exact scope above.

### Static, contract, topology, and review verdict

Ruff format reported 750 files already formatted; Ruff lint passed; strict mypy found no issues in 444
source files; web ESLint and the production TypeScript/Vite build passed, transforming 1,107 modules with
the existing large-chunk advisory. Contract checking is in sync at SHA-256
`5ab98c4a060563a8d1ea4fd2c57eba5a7a2923d69b52bd9ef623d6a528f98a58`. Executable CI parsing finds 11
job definitions and 15 expanded checks.

The final whole-branch review reported no Critical finding and raised one authority Important plus two
Minor current-source/malformed-counter findings. Commit `4ea6e78c5edf52a2666a6bee1130ace34ab19b56`
closed those findings; its scoped review found one remaining Important guard weakness. The owner authorized
one narrow correction, and commit `1f6f12def9ddb539711ceb4b382e3e28f4c1f87e` documented and enforced the
exact nine-operation allowlist. The final scoped requirements/security re-review reported no unresolved
Critical or Important finding.

The implementation compatibility baseline remains
`1dcbc2bc12b14e11f037a657d44659412a7a39c0`. Existing `OPERATIONAL` and legitimate `IN_SETUP`
installations retain their upgrade path; no compatibility shim revives the provisional endpoint or fixed
demo administrator. No owner-visible `RES-*` closure contract changed, so `docs/open-residuals.md` remains
byte-identical.

No SMTP delivery, Firefox, WebKit, actual assistive-technology session, deployment, general live acceptance
beyond the narrow first-administrator flow, or disposable Fedora proof was run or claimed.

## Debt and residual state

Open, deliberate debt:

- `20260815194752-bootstrap-claim-state-machine` — staged Keycloak/PostgreSQL recovery until one
  transactional identity boundary exists.
- `20260815215020-bootstrap-credential-lock` — singleton row lock across the Keycloak password reset until
  a fenced issuance generation exists.
- `20260816010910-keycloak-profile-reconciliation` — non-CAS whole-profile Keycloak update.
- `20260816024758-bootstrap-admission-identity-coupling` — custom Redis admission plus supported-provider
  lowercase identity knowledge, mirrored from ADR 0005.
- `20260816002506-appliance-post-ready-fingerprint` — exact shell fingerprint until a maintained Bash policy
  parser is adopted.
- `20260815200349-first-admin-live-ci` — live Keycloak remains a handoff/release gate, not required PR CI,
  until runner capacity and budget are approved.
- `20260813234519-playwright-responsive-browser-harness` — only the remaining Chromium responsive-engine
  and cohort boundary; production-auth/live-stack absence was removed because the narrow live test now
  exists.

Paid debt:

- `20260815111629-generated-contract-eof-hook` was deleted after one-LF canonicalization, generated Ruff
  convergence, regression coverage, stable repeated generation, and a clean contract check.

No existing owner-visible `RES-*` closure contract changed and no new product residual was introduced;
`docs/open-residuals.md` is intentionally unchanged.

## Known diagnostics and unverified boundaries

Known passing diagnostics are the three testcontainers import deprecations, one expected release-only API
image-digest skip, two shared-database integration skips, Node's `localStorage` warning, Vite's large-chunk
advisory, datamodel-code-generator's formatter-future warning, npm's expected `using --force` warning inside
the isolated live build, and live `NO_COLOR`/`FORCE_COLOR` warnings.

Not run or not claimed:

- SMTP delivery or activation email;
- Firefox or WebKit;
- NVDA, JAWS, VoiceOver, Orca, or any actual assistive-technology session;
- external identity federation/directory synchronization or general Keycloak administration UI;
- a broad deployed-application acceptance beyond the exact first-administrator live flow;
- production deployment or upgrade; and
- disposable Fedora proof.

Docker-backed pytest fixtures, the populated `0087` migration round trip, and the live Compose/Keycloak
claim apply only to the exact commands above.

## Handoff boundary

The isolated feature branch is ready for its final evidence commit and owner handoff. No push, PR, merge,
deployment, worktree removal, or pruning is authorized by this report. The primary checkout's pre-existing
`.superdesign/` state and unrelated prunable `/tmp` worktree records remain outside the slice.
