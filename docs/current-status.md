---
easysynq_status_schema: 1
as_of: "2026-08-15"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-records-read-console"
migration_head: "0086"
next_migration: "0087"
api_unit_tests: 1729
web_test_files: 266
web_tests: 1912
contract_tests: 283
integration_passed: 1080
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
DCR, improvement, risk, context, interested-party, identity-provisioning, and read-only Records surfaces.
Retention Policy and Evidence Pack management remain without dedicated SPA routes.

The last shipped slice adds an Evidence Operations Records register at `/records` and a dedicated
`/records/:recordId` detail route. `GET /records` now returns an authorization-correct cursor page ordered
by `(captured_at DESC, id DESC)`, with identifier/title search and the existing record-type, disposition,
legal-hold, source-document, and captured-by filters. Cursors bind their normalized query, hidden candidates
do not leak into counts or cursor boundaries, and all repository consumers—including the CAPA evidence
picker—consume the page envelope. List and detail labels remain independently authorization-gated.

The register preserves its search/filter state in the URL, exposes one semantic table with native record
links, and restores the filtered cursor page on browser Back. Detail presentation groups provenance,
lifecycle, correction lineage, structured values, evidence, evidence-for links, and rendition state.
Evidence and rendition activations request fresh presigned URLs without forwarding the EasySynQ bearer
token. The slice is read-only: it adds no capture, correction, evidence-link, legal-hold, disposition,
retention, WORM-destroy, permission, role, or Keycloak mutation.

Migration `0086` adds only the deterministic Records page-order index. The required responsive Chromium
cohort now includes the Records register and detail route alongside the prior nine shared registers. It
retains the dedicated authenticated test entry, central fail-closed fixtures, Chromium-only engine, one
worker, zero retries, and synthetic rather than live-stack boundary. Detailed shipped behavior and evidence
remain in [`slice-history.md`](slice-history.md#s-records-read-console--evidence-operations-read-console).

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
compatibility anchor remains `baseline_commit` `1dcbc2bc12b14e11f037a657d44659412a7a39c0`; the Records
slice did not rewrite that field merely because its branch SHA differs.

Fresh 2026-08-15 durable evidence measured the complete suites. API unit job
`job-msue53tc-802a4b3f` collected 1,730 items and finished with 1,729 passed and one release-ceremony skip
in 24.83 seconds. API integration job `job-msuas7j6-0af5ad42` collected 1,365 items and finished with
1,080 passed, two shared-database management-review skips, 283 deselected, and three testcontainers import
deprecations in 536.15 seconds. Published response-contract job `job-msu7ej4l-c1c3bbea` passed all 283
selected response schemas with the same three deprecations. Web job `job-msu6hrki-0c62b2a0` passed all
266 Vitest files and 1,912 tests in 284.82 seconds; stderr retained Node's repeated `localStorage`
experimental warning.

The populated migration coherence/downgrade/re-upgrade job passed 1/1 with the PostgreSQL testcontainers
deprecation, and the focused Records Docker-backed selection passed 62 tests with PostgreSQL, MinIO, and
Redis testcontainers deprecations. Focused Records unit coverage passed 95 tests, and the affected web
selection passed 10 files/124 tests. The exact browser command rebuilt the isolated entry and passed 38/38
Chromium tests in 14.7 seconds with one worker and zero retries.

Static and contract gates were also fresh: Ruff format reported 743 files already formatted; Ruff lint
passed; mypy found no issues in 441 source files; web ESLint exited 0; the production TypeScript/Vite build
transformed 1,106 modules and completed with only the existing large-chunk advisory; contract generation
and lint were in sync at SHA-256
`6acfcd63d6967a6294ce1f1a45cd5df833fe2e7c431f3f46a77f69598b24ccda`; and Alembic reported only
`0086_record_page_index (head)`, making `0087` next.

The broad contributor doctor remained diagnostic rather than a false failure for these exact commands: it
saw unsupported host Node 26, did not recognize the uv-managed Python 3.12.13 selected by `uv run`, and
reported the intentionally absent repository `.env`. The commands above nevertheless completed in their
own declared environments. PostgreSQL client `pg_dump` and `pg_restore` 16.14 were present for the green
integration rerun.

Firefox, WebKit, actual assistive-technology sessions, live backend/database/object-store/Keycloak
acceptance, a deployed Docker-backed application acceptance, deployment, and disposable Fedora proof did
not run and are not described as passed. Docker-backed pytest fixtures and the populated migration
round-trip are claimed only to the exact gates recorded above.

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
