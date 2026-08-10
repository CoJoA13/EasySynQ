---
easysynq_status_schema: 1
as_of: "2026-08-10"
baseline_commit: "6f5676e"
last_shipped_slice: "S-app-route-boundary"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 257
web_tests: 1596
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

The last shipped slice adds a two-tier application render boundary and an operational 404 without changing
authentication or setup ownership. A route-content boundary inside `AppShell` preserves the shell when a
routed page fails; retry remounts only that content subtree and neither clears nor invalidates shared query
data or issues a mutation. A global last-resort boundary sits outside the router, auth, and query providers
while remaining inside the theme provider, so provider, router, startup, or shell failures have a
router-independent full-screen recovery. Unknown operational URLs remain visible and render a fixed,
shell-contained `Page not found` state with safe Dashboard and Document Library links; pre-operational
unknown routes still go through setup. Detailed shipped behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-app-route-boundary--shell-preserving-page-recovery-global-fallback-and-safe-operational-404).

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
repository automation and must remain parseable, unique-keyed, and comma-free. A later slice updates
only the facts it freshly verifies; partial or unavailable checks must be reported as such. At `6f5676e` on
2026-08-10, the durable complete web command exited 0 with 257 files/1,596 tests passing in 263.07 seconds.
The earlier TanStack Query post-teardown `window is not defined` failure was a shared test-harness issue, not
a route-specific limitation: a queued observer callback could outlive React Testing Library cleanup and
Vitest's jsdom teardown. The test-only harness now preserves TanStack's normal asynchronous scheduling while
tracking notifications and draining them to stable event-loop quiescence after cleanup and MSW reset; it also
preserves callback errors and supports fake timers. Node still emits repeated `localStorage` experimental
warnings without affecting the green exit. Web typecheck, lint, and production build passed; the build
transformed 1,096 modules and retained its existing large-chunk advisory. API, contract, integration,
migration, and CI values above retain their prior executable evidence because this front-end-only slice did
not rerun or change them.

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
