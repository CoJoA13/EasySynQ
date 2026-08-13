---
easysynq_status_schema: 1
as_of: "2026-08-13"
baseline_commit: "02f6c56"
last_shipped_slice: "S-responsive-data-heavy-views"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 260
web_tests: 1865
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

The last shipped slice gives the nine shared-register routes localized horizontal table containment:
Tasks, Audits, DCRs, Objectives, Management Reviews, Improvement, Risks, Context, and Interested Parties.
Each route retains one complete semantic table and its original native actions at a route-owned content
floor, while the shared toolbar gives its search the available sub-`sm` width and bounds oversized filters
in one following lane. At `sm` and above, the source/style contract preserves the existing 260 px search
width, columns, control order, and desktop presentation. No card alternative, hidden column, duplicate
mobile tree, custom breakpoint, global overflow suppression, or shared responsive-table abstraction was
introduced.

The slice preserves the operational QueryClient/provider identity, cached state, route-persistent mutation
feedback, authentication/setup gates, route-error and 404 ownership, API handlers, OpenAPI/generated
artifacts, database schema/migrations, Keycloak, dependencies, and deployment behavior. Detailed shipped
behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-responsive-data-heavy-views--localized-shared-register-containment).

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
evidence baseline `02f6c56` on 2026-08-13, on a branch based on main squash `082ba310`, durable job
`job-mss1id7k-9a7297cb` ran exact direct argv `npm --prefix apps/web test` from the isolated worktree and
exited 0 with all 260 files and 1,865 tests passing in 278.64 seconds (transform 2.49 seconds, setup 37.81
seconds, import 40.32 seconds, tests 115.95 seconds, environment 66.34 seconds), with no unhandled error.
Its stderr contained Node's repeated `ExperimentalWarning` that `localStorage` was unavailable because
`--localstorage-file` was not provided.

The exact 16-file affected selection passed 238 tests in 43.53 seconds on the reviewed implementation head.
Web lint, `tsc --noEmit`, the app-owned Prettier check over all 22 changed TypeScript/TSX files, and the
production build passed. Vite 8.1.5 transformed 1,098 modules and built in 561 ms; it emitted 0.77 kB HTML,
211.40 kB CSS, and 1,159.20 kB JavaScript and retained only the existing advisory for a chunk above 500 kB.
Range and working-tree diff guards were clean, and the structural-row ripgrep guard returned the expected
no-match status. Final review added loaded-state axe preservation coverage for Management Reviews and
required repository-neutral plan history from first introduction; both fixes are included in `02f6c56`
and the rewritten ancestry, with real browser claims explicitly excluded.

The history closure made the plan repository-neutral from its first introduction and retained recoverability
through reflog without creating a publishable backup branch. Scoped documentation Prettier passed; authority
fixtures passed 91/91, all seven Claude-hook compatibility assertions passed, repository authority returned
`AUTHORITY_OK`, site-data fixtures passed 13/13, the direct site-data scan was clean, and range plus
working-tree diff guards were clean. API, contract, integration, migration, and CI numeric values above
retain their prior successful evidence because their complete gates were not refreshed for this front-end-only
slice. Playwright, real
viewport/clipping/scroll-reachability, request-intercepted failure, focus-ring/forced-colors, screen-reader,
Docker-backed, deployment, and Fedora proofs did not run and remain outside this evidence.

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
