---
easysynq_status_schema: 1
as_of: "2026-08-16"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-first-admin-provisioning"
migration_head: "0087"
next_migration: "0088"
api_unit_tests: 1789
web_test_files: 267
web_tests: 1940
contract_tests: 284
integration_passed: 1137
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
the seeded System Administrator role, and displays a generated temporary password once. Provisioning
authority ends outside `UNINITIALIZED`; the public acknowledgment route additionally accepts only the
narrowly fenced matching replay of a completed claim while setup is `IN_SETUP`. Keycloak forces a password
replacement at first sign-in. Incomplete or mismatched replays fail closed.

All supported usernames are trimmed and canonicalized to lowercase before claim binding, Keycloak
lookup/create, response projection, and ordinary `/users/provision` handling. Display names retain their
case. Later users continue to be created from Administration → Users with `user.create`; role assignment
still additionally requires `permission.grant`, editing uses `user.update`, and every credential reset of
another linked user requires both `user.create` and the unconditional system-tier guard under R64. SMTP and
activation email are not required.

Migration `0087_first_admin_bootstrap` adds the nullable, upgrade-compatible bootstrap claim and linked-user
state. The cross-system workflow never deletes a Keycloak user as compensation, never stores or logs the
bootstrap proof or temporary password, and serializes proof admission and credential issuance. Failed-proof
accounting uses one atomic expiring Redis update and is rechecked inside the PostgreSQL singleton lock so
racing invalid attempts share the same limit. Detailed behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-first-admin-provisioning--first-administrator-without-keycloak-administration).

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

Fresh 2026-08-16 durable evidence measured the complete suites. API unit job
`job-msvivlh1-fa8002ec` passed 1,789 tests with one expected release-only image-digest skip in 29.87
seconds. API integration job `job-msvie2dd-2d54f93d` passed 1,137 tests with two shared-database skips,
284 deselected, and three known testcontainers import deprecations in 785.46 seconds. Published
response-contract job `job-msviwr7g-bfe4f534` passed all 284 selected schemas with the same three
deprecations in 297.42 seconds. Web job `job-msvj3prd-02845479` passed all 267 Vitest files and 1,940 tests
in 325.91 seconds; stderr retained Node's known `localStorage` experimental warning.

The populated migration coherence/downgrade/re-upgrade gate passed 1/1 with the PostgreSQL testcontainers
deprecation. The final setup/users/backup integration cohort passed 98 tests, focused API coverage passed
153 tests, and the affected web selection passed 5 files/87 tests. The complete synthetic browser gate
rebuilt its isolated entry and passed 40/40 Chromium tests with one worker and zero retries.

The separate narrow live job `job-msvic4ai-947a887c` passed 1/1 Chromium test in 2.8 seconds with one
worker and zero retries against fresh Docker-backed API, PostgreSQL, object store, Redis, and Keycloak
services. It proved mixed-case input returned and bound the canonical lowercase username, mandatory
password replacement succeeded with that username, and the obsolete temporary password was rejected from
a clean browser context. Teardown removed the exact Compose project's containers, volumes, network, and
all six local images.

Static and contract gates were also fresh: Ruff format reported 750 files already formatted; Ruff lint
passed; mypy found no issues in 444 source files; web ESLint exited 0; the production TypeScript/Vite build
transformed 1,107 modules and completed with only the existing large-chunk advisory. Repeated real contract
generation and the check-only gate were byte-stable and in sync at SHA-256
`b0bf7d0ac437a85cd171096520fb9499e608577d45bb861fec0a8ad53065f78d`; Alembic reported only
`0087_first_admin_bootstrap (head)`, making `0088` next. The executable workflow source still expands to
eleven jobs and fifteen aggregate/leaf checks.

Firefox, WebKit, actual assistive-technology sessions, SMTP delivery, deployment, a broader deployed
application acceptance, and disposable Fedora proof did not run and are not described as passed. The live
claim is limited to the first-administrator identity flow above; Docker-backed pytest fixtures and the
populated `0087` migration round trip are claimed only to their exact gates. Known passing diagnostics are
the testcontainers deprecations, Node `localStorage` warning, Vite large-chunk advisory, npm's expected
`using --force` warning inside the isolated live build, and `NO_COLOR`/`FORCE_COLOR` warnings from the live
browser command.

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
