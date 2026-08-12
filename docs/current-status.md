---
easysynq_status_schema: 1
as_of: "2026-08-11"
baseline_commit: "e1d0802"
last_shipped_slice: "S-mutation-feedback"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 258
web_tests: 1637
contract_tests: 283
integration_passed: 1051
integration_skipped: 2
ci_jobs: 10
ci_checks: 14
---

# Current execution snapshot

This is the dated coordination snapshot for contributors and repository automation. It is not product
authority and it is not runtime discovery: binding decisions live in
[`decisions-register.md`](decisions-register.md), while current deferred work lives only in
[`open-residuals.md`](open-residuals.md).

## Shipped boundary

The original MVP foundation and the ISO 9001 workflow families are delivered. The routed SPA covers the
main document, workflow, compliance, reporting, audit, ingestion, drift, objective, management-review,
DCR, improvement, risk, context, interested-party, and identity-provisioning surfaces. Records,
retention/disposition, and Evidence Packs are API/worker-complete but do not have dedicated SPA
management routes.

The last shipped slice makes failed notification mutations visible, announced, and recoverable without
discarding the operator's intent. Explicit mark-one, bell/page mark-all, and preference-save failures remain
local to their controls; opening an unread notification still navigates immediately, while its late
mark-read failure persists in the operational shell until Dismiss or a successful explicit retry. Retry is
never automatic and is available only for these named personal-state operations under the approved
effective-repeat-safety contract when the error is a fetch
`TypeError`, HTTP 408, HTTP 429, or HTTP 500–599. Local pending/retry, Dismiss, and preference-edit
lifecycle guards prevent abandoned callbacks from restoring stale feedback or overwriting newer preference
intent. The slice does not change API handlers, OpenAPI/generated artifacts, database schema/migrations,
authentication/setup gates, query-client identity, notification delivery, or URL semantics. Detailed
shipped behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-mutation-feedback--notification-write-failure-feedback-and-explicit-safe-retry).

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
the facts it freshly verifies; partial or unavailable checks must be reported as such. At `e1d0802` on
2026-08-11, durable job `job-mso9aheq-41b8fb9c` ran `npm --prefix apps/web run test` with 258 files and
1,637 tests passing in 270.42 seconds, without unhandled errors. It emitted only Node's repeated
`localStorage` experimental warning. The final 11-file affected selection passed 149 tests; web typecheck,
lint, scoped Prettier, and production build passed. The build transformed 1,097 modules and retained its
existing advisory for a chunk above 500 kB. API Ruff format/check and mypy passed, but the exact focused
notification integration command exited 1 before any test body or assertion: all 23 shared
`PostgresContainer` setups hit Docker-socket `PermissionError`. That repeat-safety integration proof is
therefore unavailable on this host, not a pass. Authority fixtures passed 91/91, Claude-hook compatibility
passed, repository authority returned `AUTHORITY_OK`, site-data fixtures passed 13/13, the direct site-data
scan was clean, and `git diff --check` was clean. API, contract, integration, migration, and CI numeric
values above retain their prior successful evidence unless refreshed by their own complete gate.

## CI topology

The frontmatter records the current workflow topology snapshot. The workflow files themselves are
executable truth. Contributor workflow and evidence expectations live in [`../AGENTS.md`](../AGENTS.md)
and [`dev-workflow.md`](dev-workflow.md).

The dependency-light `contracts` job runs repository-authority and R61 protection first, then the Fedora
bootstrap/doctor/proof structural contracts and the disabled PostgreSQL MCP contract before dependency
hydration. These checks prove tracked interfaces and failure propagation; they do not emulate Fedora,
SELinux, libvirt, Docker, or a live application stack.

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
