---
easysynq_status_schema: 1
as_of: "2026-08-14"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-responsive-browser-evidence"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 260
web_tests: 1865
contract_tests: 283
integration_passed: 1051
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
DCR, improvement, risk, context, interested-party, and identity-provisioning surfaces. Records,
retention/disposition, and Evidence Packs are API/worker-complete but do not have dedicated SPA
management routes.

The last shipped slice makes the nine shared-register responsive contract a required Chromium gate for
Tasks, Audits, Objectives, Management Reviews, DCRs, Improvement, Risks, Context, and Interested Parties.
A separate test-only Vite entry mounts the real application route tree, shell, theme, query provider, and
production components with deterministic authenticated context. Playwright centrally fulfills synthetic
API fixtures and fails closed on undeclared API or external HTTP(S) traffic; no browser-test authentication
path or fixture enters the production bundle.

At 320 by 800 and 1280 by 900, the browser suite measures document, localized container, table, toolbar,
and far-edge geometry while retaining one semantic table and one native action tree. It also proves one
HTTP 503 recovery, one network-abort recovery, DCR keyboard focus in normal and forced-colors rendering,
Tasks native-link and row-keyboard semantics, Context named filters and live result-count announcement,
and the fail-closed interceptor through isolated negative child probes. The stable `web` CI aggregate now
requires both Vitest shards and the dedicated `web-browser` Chromium job.

The slice retains Chromium-only, one-worker, zero-retry evidence and preserves production OIDC, provider
lifetime, API/OpenAPI, migrations, database, specialized tables, Compose, and deployment behavior. Detailed
shipped behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-responsive-browser-evidence--required-chromium-register-proof).

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
the facts it freshly verifies; partial or unavailable checks must be reported as such. At reviewed
implementation baseline `1dcbc2bc12b14e11f037a657d44659412a7a39c0` on 2026-08-14, durable job
`job-mssng14k-588252b8` ran exact direct argv `npm --prefix apps/web test` from the isolated worktree and
exited 0 with all 260 Vitest files and 1,865 tests passing in 311.95 seconds (transform 3.39 seconds, setup
41.58 seconds, import 45.23 seconds, tests 133.02 seconds, environment 72.59 seconds), with no failed suite,
failed test, retry, or unhandled error. Its stderr retained Node's repeated `ExperimentalWarning` that
`localStorage` was unavailable because `--localstorage-file` was not provided.

The preceding required run, durable job `job-mssmmw65-bdd6b919`, honestly exited 1 after 285.70 seconds:
all 1,865 intended Vitest assertions passed, but Vitest's default `*.spec.ts` discovery also collected six
Playwright files under `e2e/`, whose top-level Playwright `test()` calls failed as foreign suites. The
focused discovery RED listed those six files. Commit `1dcbc2b` extends `configDefaults.exclude` with
`e2e/**`, preserving Vitest's default exclusions; the same discovery probe then returned no file, one
intended source suite passed 10/10, and lint, browser build, production build, and the complete Chromium
suite remained green before the final durable rerun.

The final exact `npm --prefix apps/web run test:browser` command typechecked and built the isolated entry,
then passed 26 Chromium tests in 11.0 seconds with one worker and zero retries. The count is separate from
Vitest: eighteen route-and-viewport geometry cases cover all nine routes at 320 by 800 and 1280 by 900;
four focused cases cover DCR focus and forced colors, Tasks native semantics and row navigation, and Context
filter/live-region semantics; two cases cover HTTP and network recovery; one mounts the routed shell; and
one meta-spec validates the fail-closed request interceptor through two isolated deliberately failing child
probes. Diagnostic output retained Vite's existing large-chunk advisory plus Node's `localStorage` and
`NO_COLOR`/`FORCE_COLOR` warnings.

Implementation review added the shared sortable-header focus scroll correction, browser-only Docker-context
exclusions and one-layer Playwright removal, regression-safe fail-closed negative evidence, stricter Docker
reinclusion guards, and restored-error-copy recovery assertions. The final implementation and CI contract
checks passed before authority closure. Scoped documentation formatting, authority and Claude compatibility
fixtures, repository authority, site-data fixtures and direct scan, high-severity lock audit, range and
working-tree diff guards, and the structural-row no-match guard all passed at handoff. API, contract,
integration, and migration numeric values above retain their prior successful evidence because their full
authoritative gates were not refreshed for this frontend browser-evidence slice. Firefox, WebKit, actual
assistive-technology sessions, live backend integration, Docker-backed application acceptance, deployment,
migration round-trip, and disposable Fedora proof did not run and are not described as passed.

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
