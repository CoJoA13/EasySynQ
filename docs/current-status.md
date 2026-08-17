---
easysynq_status_schema: 1
as_of: "2026-08-16"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-first-admin-provisioning"
migration_head: "0088"
next_migration: "0089"
api_unit_tests: 1835
web_test_files: 267
web_tests: 1946
contract_tests: 284
integration_passed: 1163
integration_skipped: 2
ci_jobs: 11
ci_checks: 15
---

# Current execution snapshot

This is the dated coordination snapshot for contributors and repository automation. It is not product
authority and it is not runtime discovery: binding decisions live in
[`decisions-register.md`](decisions-register.md), while current deferred work lives only in
[`open-residuals.md`](open-residuals.md).

## Shipped boundary

The original MVP foundation and the ISO 9001 workflow families are delivered. The routed SPA covers the
main document, workflow, compliance, reporting, audit, ingestion, drift, objective, management-review,
DCR, improvement, risk, context, interested-party, identity-provisioning, first-run setup, and read-only
Records surfaces. Retention Policy and Evidence Pack management remain without dedicated SPA routes.

The latest completed slice removes normal first-install dependence on the Keycloak console, a Keycloak
subject, or the retired fixed-`qmsadmin` helper. While setup is `UNINITIALIZED`, `/setup` accepts the
one-time EasySynQ bootstrap proof, creates and links the first Keycloak identity and EasySynQ user, assigns
the seeded System Administrator role, and displays a generated temporary password once. The SPA retains
the associated credential receipt only in volatile component memory and submits it during acknowledgment.
Provisioning authority ends outside `UNINITIALIZED`; the public acknowledgment route additionally
accepts only the narrowly fenced matching replay of a completed claim while setup is `IN_SETUP`. The active
receipt, current setup proof, complete claim, exact administrator assignment, and absence of an unrelated
administrator are all required before consumption. A reminted setup proof can acknowledge the same
still-displayed password generation; a superseded receipt consumes nothing and requires explicit password
reissue.
Keycloak forces a password replacement at first sign-in. Incomplete or mismatched replays fail closed.

Public bootstrap refuses an unrelated System Administrator assignment without exposing whether one exists
before the setup proof is validated. A host operator may recover only while setup is `UNINITIALIZED` by
running `easysynq setup release-administrator-blocker` for one exact Keycloak subject. The command refuses
the claim owner, removes only that user's System Administrator assignment, preserves the identity, user,
other roles, and history, and requires an independent incident/change record. Supported fresh-install
instructions never create or sign in as a demo identity before the browser creates the first administrator.

All supported usernames are trimmed and canonicalized to lowercase before claim binding, Keycloak
lookup/create, response projection, and ordinary `/users/provision` handling. Display names retain their
case. Later users continue to be created from Administration → Users with `user.create`; role assignment
still additionally requires `permission.grant`, editing uses `user.update`, and every credential reset of
another linked user requires both `user.create` and the unconditional system-tier guard under R64. SMTP and
activation email are not required.

Migrations `0087_first_admin_bootstrap` and `0088_bootstrap_credential` add nullable,
upgrade-compatible claim/link and credential-receipt digest state. The plaintext receipt, bootstrap proof,
and temporary password are never persisted or logged. The cross-system workflow never deletes a Keycloak
user as compensation and serializes proof admission, exact administrator-set checks, marker-owned profile
reconciliation, and credential issuance. Failed-proof accounting uses one atomic expiring Redis update,
rejects malformed negative reader state, and is rechecked inside the PostgreSQL singleton lock so racing
invalid attempts share the same limit. Detailed behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-first-admin-provisioning--first-administrator-without-keycloak-administration).

The application API has exactly nine bearer-free operations in three bounded authorization categories:
public health/metadata/setup routing (`GET /healthz`, `GET /readyz`, `GET /auth/config`, and
`GET /setup/state`); bootstrap-secret-authorized mutations (`POST /setup/administrator` and
`POST /setup/administrator/acknowledge`); and signed-capability-authorized verification/share access
(`GET /verify`, `GET /evidence-packs/shared`, and `GET /evidence-packs/shared/download`). The capability
routes remain authorized and scope-bounded, not anonymous QMS-content access; ordinary operations, QMS
content, and customer/site data remain authenticated and authorized.

## Runtime truth

Before creating a migration, run:

```bash
cd apps/api && uv run alembic heads
```

The frontmatter value above is a dated snapshot. `alembic heads` is the migration authority; do not infer
the head from filename sorting or prose.

The permission catalog is defined in [`07-authorization-model.md`](07-authorization-model.md) and
enforced by the executable catalog assertion in `apps/api/tests/unit/test_authz.py`. The complete decision
set is defined by the headings and self-range declarations in [`decisions-register.md`](decisions-register.md).

## Verification baseline

The numeric frontmatter records the latest fresh completion evidence for each suite. It is consumed by
repository automation and must remain parseable, unique-keyed, and comma-free. A later slice updates only
the facts it freshly verifies; partial or unavailable checks must be reported as such. The implementation
compatibility anchor remains `baseline_commit` `1dcbc2bc12b14e11f037a657d44659412a7a39c0`; this slice does
not rewrite that implementation-evidence field merely because its branch SHA differs.

Fresh 2026-08-17 durable evidence measured the complete affected inventories after the first-administrator
PR review fixes.
API unit job `job-mswq4zse-b59b5405` passed 1,835 tests with one expected release-ceremony image-digest
skip in 30.33 seconds. API integration job `job-msx8jger-0e1559da` passed 1,163 tests with two expected
shared-database skips, 284 contract-marked tests deselected, and three registered Testcontainers import
deprecations in 548.61 seconds. Published response-contract job `job-mswlweou-9b17e09e` remained valid
because the later fixes changed no API/OpenAPI/response surface; it passed all 284 schemas with the same
three deprecations in 251.84 seconds. Web job `job-msx9sx1s-0e95eae6` passed all 267 Vitest files and
1,946 tests in 308.83 seconds with the known Node
`localStorage` diagnostics.

The bounded PR review fixes cover the first-administrator blocker host-recovery guidance, separate bound
username/email collision guidance, and trusted-remint admission-budget reset/rollback; the fresh complete
inventories exercised their owning web and setup integration suites without changing API unit, contract,
migration, hash, CI-topology, or residual evidence.

The populated migration gate passed 1/1 and independently exercised `0087 -> 0086 -> 0087` and
`0088 -> 0087 -> 0088`, with the registered PostgreSQL Testcontainers deprecation. Final
recovery/install cohorts passed 8 recovery integrations, 3 CLI/wrapper dispatch tests, and 15
supported-install/first-admin guards.
The complete synthetic browser job `job-mswm9e51-7a34ed2b` remained valid because the later fixes changed
no web/browser surface and passed 40/40 Chromium tests in 16.4 seconds with one worker and zero retries.

The separate narrow live job `job-mswlg4ft-b05efb99` remained valid because the later fixes did not change
its exercised identity/provider flow. It passed 1/1 Chromium test in 2.6 seconds with one worker and zero
retries against fresh Docker-backed API, PostgreSQL, object store, Redis, and Keycloak services. It proved
mixed-case input returned and bound the canonical lowercase username, mandatory password replacement
succeeded, and the obsolete temporary password was rejected from a clean browser context. Teardown removed
the exact `easysynq-first-admin-32b1b175ba35` project's containers, volumes, network, and six local images.

Static and contract gates were also fresh: Ruff format reported 750 files already formatted; Ruff lint
passed; mypy found no issues in 444 source files; web ESLint exited 0; and the production TypeScript/Vite
build transformed 1,107 modules with only the existing large-chunk advisory. Contract checking was in sync
at SHA-256 `5ab98c4a060563a8d1ea4fd2c57eba5a7a2923d69b52bd9ef623d6a528f98a58`;
Alembic reported only `0088_bootstrap_credential (head)`, making `0089` next. Executable workflow parsing
still finds eleven job definitions and fifteen expanded aggregate/leaf checks.

The final requirements/security whole-branch review found no Critical issue, fixed its authority,
current-comment, negative-Redis-state, and exact bearer-free allowlist findings in commits
`4ea6e78c5edf52a2666a6bee1130ace34ab19b56` and
`1f6f12def9ddb539711ceb4b382e3e28f4c1f87e`, and the scoped final re-review reported no unresolved
Critical or Important finding. No owner-visible residual closure contract changed.

Firefox, WebKit, actual assistive-technology sessions, SMTP delivery, deployment, general live acceptance
beyond the narrow first-administrator flow, and disposable Fedora proof did not run and are not described
as passed. Docker-backed pytest fixtures and both populated migration boundaries are claimed only to their
exact gates. Known passing diagnostics are the registered Testcontainers deprecations, expected suite
skips, Node `localStorage` warnings, the Vite large-chunk advisory, and `NO_COLOR`/`FORCE_COLOR` warnings
from the synthetic browser job.

## CI topology

The frontmatter records the current workflow topology snapshot. The workflow files themselves are
executable truth. Contributor workflow and evidence expectations live in [`../AGENTS.md`](../AGENTS.md)
and [`dev-workflow.md`](dev-workflow.md).

The dependency-light `contracts` job runs repository-authority and R61 protection first, then the Fedora
bootstrap/doctor/proof structural contracts and the disabled PostgreSQL MCP contract before dependency
hydration. These checks prove tracked interfaces and failure propagation; they do not emulate Fedora,
SELinux, libvirt, Docker, or a live application stack.

The dedicated `web-browser` job installs the locked web tree and Chromium with its Linux dependencies,
runs the complete browser suite, and uploads ignored diagnostics only on failure. Stable check `web` uses
`always()` and explicitly rejects a non-success result from either `web-shards` or `web-browser`; the
workflow now exposes eleven jobs and fifteen aggregate/leaf checks.

## Programme 0 acceptance status

- PostgreSQL MCP: **disabled** after the reviewed package failed the high-severity advisory gate. No
  connector, launcher, package lock, owner-database port overlay, or orphan database role ships. The sole
  re-enablement contract is [`RES-POSTGRES-MCP-REPLACEMENT`](open-residuals.md#res-postgres-mcp-replacement).
- Disposable Fedora Workstation proof: **PENDING — not run on this checkout as of 2026-08-09**. No Fedora
  media checksum, evidence commit, or PASS result is recorded because the required Fedora 44 Everything
  netinstall and Workstation Live media plus a usable `qemu:///system` proof host were not available.
  Completion requires the manual PR/release gate in [`runbooks/fedora-proof.md`](runbooks/fedora-proof.md).
- Local focused Python acceptance: **PASS on 2026-08-08 with CPython 3.12.13**. The Programme 0
  dependency-tooling, deployment-configuration, and CI-workflow matrix completed 80 tests. This does not
  replace the full repository suites or the pending Fedora VM proof.
