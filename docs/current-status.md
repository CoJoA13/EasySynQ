---
easysynq_status_schema: 1
as_of: "2026-08-13"
baseline_commit: "76c2f72"
last_shipped_slice: "S-url-state-correctness"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 259
web_tests: 1834
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

The last shipped slice classifies query-backed operational state by product meaning. The Tasks and
Acknowledgements material views now have distinct fixed titles and intentional live focus/announcement
behavior; recognized detail selectors and subviews participate in safe route recovery without stealing
feature-owned focus; and ordinary filters, search, sort, pagination, tabs, modes, and comparison controls
retain compact replacement history. Browser Back/Forward remains authoritative, every URL-backed drawer
follows live selector change, removal, and conflict, and loaded comparison IDs are validated before viewer
work. Unknown or conflicting selector values resolve to safe defaults without entering visible copy.

The slice preserves the operational QueryClient/provider identity, cached state, route-persistent mutation
feedback, authentication/setup gates, route-error and 404 ownership, API handlers, OpenAPI/generated
artifacts, database schema/migrations, Keycloak, dependencies, and deployment behavior. Detailed shipped
behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-url-state-correctness--effective-url-view-identity-history-recovery-and-accessibility).

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
the facts it freshly verifies; partial or unavailable checks must be reported as such. At implementation
baseline `76c2f72` on 2026-08-13, durable job `job-msr2j5ez-6c010aaa` ran
`npm --prefix apps/web test` and exited 0 with 259 files and 1,834 tests passing in 275.10 seconds, without
unhandled errors. It emitted only Node's repeated `localStorage` experimental warning. The exact 21-file
URL-state selection passed 467 tests in 57.92 seconds; web typecheck, lint, scoped documentation Prettier,
and production build passed. The build transformed 1,098 modules and retained its existing advisory for a
chunk above 500 kB; the JavaScript asset was 1,158.15 kB (318.75 kB gzip). Independent broad review drove
two focused fix waves, and the final scoped re-review reported no actionable findings; its seven-file
selection passed 211 tests, web typecheck passed, and its scoped diff was clean. API, contract, integration,
migration, and CI numeric values above retain their prior successful evidence because their complete gates
were not refreshed for this front-end-only slice. After the evidence edits, authority fixtures passed 91/91,
all seven Claude-hook compatibility assertions passed, repository authority returned `AUTHORITY_OK`,
site-data fixtures passed 13/13, the direct site-data scan was clean, and the diff guards were clean.

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
