---
easysynq_status_schema: 1
as_of: "2026-08-10"
baseline_commit: "cb6bdd6"
last_shipped_slice: "S-app-route-boundary"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 257
web_tests: 1600
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
routed page fails. The owner-approved 2026-08-10 clarification requires Retry to remount only that content
subtree while preserving the original query provider, exact client identity, source-client lifecycle, and
cached data. Retry explicitly calls no invalidation, refetch, reset, removal, clearing, equivalent cache
operation, or mutation seam; a stale query observer may still perform TanStack Query's normal configured
refetch when it remounts. A global last-resort boundary sits outside the router, auth, and query providers
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
only the facts it freshly verifies; partial or unavailable checks must be reported as such. At `cb6bdd6`
on 2026-08-10, the durable final post-clarification `npm --prefix apps/web run test` exited 0 with all 257
files and 1,600 tests passing in 261.05 seconds. It emitted only Node's repeated existing `localStorage`
experimental warning. This is the first complete web run after the owner-approved QueryClient provider
clarification and replaces `6f5676e` as the current web baseline; the earlier 257-file/1,596-test run
remains preserved in slice history as pre-clarification evidence. Before the final run, the clarification
implementation checkpoint `8d285d7` passed the 12-file affected selection 126/126; web typecheck and lint
exited 0; and the production build transformed 1,096 modules and exited 0 with the existing large-chunk
advisory. Repository-authority fixtures passed 91/91, Claude-hook compatibility passed all seven
assertions, repository authority returned `AUTHORITY_OK`, site-data fixtures passed 13/13, and the direct
site-data scan was clean.
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
