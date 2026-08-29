---
easysynq_status_schema: 1
as_of: "2026-08-29"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-ui-4"
migration_head: "0091"
next_migration: "0092"
api_unit_tests: 1996
web_test_files: 276
web_tests: 2251
contract_tests: 284
integration_passed: 1224
integration_skipped: 2
ci_jobs: 12
ci_checks: 16
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

The latest completed work is the S-ui interface program, slices S-ui-1 to S-ui-4. Routes, information architecture, the API, and every permission
and gating behaviour are unchanged. It is otherwise a surface and layout rework, with one behavioural
exception: S-ui-2 also corrected cache invalidation for the caller's own task list, which was previously
refreshed only by the change-request, improvement and leadership decision branches, so a document
approval, a periodic-review completion, every CAPA decision and both acknowledgement paths left it stale.

S-ui-4 shared the register page header across eleven register routes and the scorecard band shell across
four, and answered the accepted-duplication question the decisions register had parked. That entry's
measurement was corrected there: the risk, context and interested-parties lifecycle panel and publish
modal are far closer to identical than recorded, with no structural divergence, and the owner's decision
was to collapse the scorecard band only and leave the panel and modal accepted. A shared four-branch page
frame, the Library and Records registers, and register heading-level normalisation were all deliberately
left out; the first and last are tracked in [`open-residuals.md`](open-residuals.md).

The design tokens are now authoritative. The Mantine theme reads the `--es-*` typography, spacing, radius
and elevation scales instead of its own defaults, and `AppShell` separately reads the layout tokens rather
than hardcoding its dimensions. Before this the theme already took its font families and its status colour
pairs from tokens, but the typography, spacing, elevation and layout ramps each had zero consumers, so
those four rendered at Mantine's defaults. The accent is the brand mark's teal, so the mark and the interface agree.
`Archivo` is self-hosted under `apps/web/public/fonts/` under the SIL Open Font License 1.1, because the
Caddy CSP sets `font-src 'self'` and the air-gap bundle has no egress. Muted text reaches WCAG AA by
remapping Mantine's own `--mantine-color-dimmed` onto the token, which corrects roughly 331 `c="dimmed"`
call sites without component edits; correcting the token alone would not have changed them.

The left rail leads each destination with a hand-rolled inline SVG glyph, marks each PDCA section with
that phase's hue beside the phase name, and shows one navigation count: the caller's own open tasks. That
is the only count the RAIL can state honestly, because no aggregate counts endpoint exists and the
per-register numbers are derived by scanning capped register windows; the top bar's notification bell
carries the shell's other count. It follows the established
never-a-confident-zero rule, so a failed count renders as unavailable rather than as zero.

The Home quadrants render a tinted header band carrying the phase, its clause range and that quadrant's
current signal. The signal is folded from the same observations the tiles beneath it render, so a header
states an observed count and the label that count belongs to, never a compliance verdict, and it cannot
drift from the tile it summarises. Dark-scheme quadrant tints are designed rather than derived from the
light values.

Earlier first-run provisioning removes normal first-install dependence on the Keycloak console, a Keycloak
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

Fresh 2026-08-29 evidence for the S-ui program. The numeric frontmatter above now describes the tree at
`main` after S-ui-4, which also carries the slices that shipped between 2026-08-17 and this date; the
2026-08-17 paragraphs below are retained as the evidence for their own tree and are not restated as
current.

Measured on the merged S-ui-4 tree `2626ba9`. API unit passed 1,996 tests with 2 expected skips in 35.65
seconds, both skips being the release-ceremony image-digest and image-build opt-ins — unchanged, because
S-ui-4 touched no API code. Web Vitest passed 276 files and 2,251 tests with exit code 0, up from 273 and
2,230: S-ui-4 added the shared header and band-shell unit suites and a source-text adoption contract. The
Playwright browser suite passed 52 of 52 Chromium tests with one worker and zero retries, up from 42; the
ten new cases measure each shared register header both for a granted caller, whose action renders, and for
a denied one, whose header must carry no element standing in for the affordance they lack. Web ESLint
exited 0 and strict `tsc --noEmit` passed for both the application and browser projects; the production
build completed. `check-repo-authority.sh`, `check-no-site-data.sh` and the 91-fixture agent-authority
test all passed. Alembic reported `0091_documents_list_index (head)`, making `0092` next — S-ui-4 added no
migration.

One measurement caveat worth carrying forward. `npm run test:browser` is `build:browser && playwright
test`; invoking `playwright test` alone serves a previously built bundle, so a source change — including a
deliberate mutation used as proof — does not reach the browser and the suite reports a stale result. Any
Playwright evidence must come from the full script.

Pull-request CI run `33264035498`, on the pre-merge S-ui-4 branch head `ddd893d`, passed all fifteen
pull-request checks with `release-gate` skipped as designed. Its four integration shards passed 272, 209,
389 and 354 tests — 1,224 with the two expected shared-database skips — and the published
response-contract job passed all 284 schemas. Those figures are unchanged from the S-ui-3 run
(`33239332445`, branch head `05cd1663`), which is what a front-end-only slice should produce; they are
re-read here rather than carried forward. The slice merged to `main` as `2626ba9`. Contract checking is in sync on this tree at SHA-256
`041da0299dde316f1a6c2ca8acb1106171960ea44e52c19b8b88afd3bf5f7958`, which supersedes the S-ui-3 hash `e66fa80c…` and the 2026-08-17 hash
recorded below.

`docs/11-ui-ux-design-system.md` was not updated by this program and now disagrees with the shipped
tokens on typography, accent, focus ring and shell metrics; that gap is recorded as
`RES-DOC11-TOKEN-DRIFT` in [`open-residuals.md`](open-residuals.md).

The integration and response-contract suites were NOT run locally for this program; their counts above
are taken from that CI run rather than from a local execution, because the local Docker-backed fixtures
were not exercised. Firefox, WebKit, assistive-technology sessions, SMTP delivery, deployment, live
acceptance and the disposable Fedora proof did not run and are not described as passed.

The three UI slices were additionally reviewed by an adversarial multi-lens pass whose confirmed findings
were folded before merge. Two are worth recording because they defeated the automated gates entirely: a
CSS custom-property override that lost on specificity and was inert in the light scheme, and a card layout
that clipped the Home quadrants' only navigation action out of view at every breakpoint. Neither was
visible to ESLint, strict TypeScript or the complete Vitest suite, because jsdom performs no layout and
resolves no stylesheet cascade. Both are now covered by executable guards — a token contrast gate that
derives its pairs from the token naming convention, and `apps/web/e2e/home-geometry.spec.ts`, which
measures each quadrant's action against its card at 320 and 1280 pixels in a real browser.

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
The earlier synthetic browser job `job-mswm9e51-7a34ed2b` passed 40/40 Chromium tests in 16.4 seconds
with one worker and zero retries for its then-tested tree. After responsive first-administrator corrections
through `d091ee58aa05b5590f42fe395e22062137ccb38e`, the final local
`npm --prefix apps/web run test:browser` cohort again passed 40/40 Chromium tests with one worker and zero
retries. CI run `32040002549` corroborated that final tree: all fifteen expanded checks, including
`web browser (Chromium)` and aggregate `web`, completed successfully.

The separate narrow live job `job-mswlg4ft-b05efb99` remained valid because the later fixes did not change
its exercised identity/provider flow. It passed 1/1 Chromium test in 2.6 seconds with one worker and zero
retries against fresh Docker-backed API, PostgreSQL, object store, Redis, and Keycloak services. It proved
mixed-case input returned and bound the canonical lowercase username, mandatory password replacement
succeeded, and the obsolete temporary password was rejected from a clean browser context. Teardown removed
the exact `easysynq-first-admin-32b1b175ba35` project's containers, volumes, network, and six local images.

Static and contract gates were also fresh: Ruff format reported 750 files already formatted; Ruff lint
passed; mypy found no issues in 444 source files; web ESLint exited 0; and the production TypeScript/Vite
build transformed 1,107 modules with only the existing large-chunk advisory. Contract checking is in sync
at SHA-256 `bec600ffcc53e6f73871c46e0a52ae520902a50e78e1b1e72d1265927cebb90b`;
Alembic reported only `0088_bootstrap_credential (head)`, making `0089` next. Executable workflow parsing
found eleven job definitions and fifteen expanded aggregate/leaf checks at that date; it now finds
twelve and sixteen. The one added job and check is `release-gate`, from the release-gate slice.

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
workflow now exposes twelve jobs and sixteen aggregate/leaf checks, fifteen of which run on an ordinary
pull request; `release-gate` is skipped outside the release ceremony. Sixteen checks expand from twelve
jobs because `integration-shards` fans out four ways and `web-shards` two.

## Program 0 acceptance status

- PostgreSQL MCP: **disabled** after the reviewed package failed the high-severity advisory gate. No
  connector, launcher, package lock, owner-database port overlay, or orphan database role ships. The sole
  re-enablement contract is [`RES-POSTGRES-MCP-REPLACEMENT`](open-residuals.md#res-postgres-mcp-replacement).
- Disposable Fedora Workstation proof: **PENDING — not run on this checkout as of 2026-08-09**. No Fedora
  media checksum, evidence commit, or PASS result is recorded because the required Fedora 44 Everything
  netinstall and Workstation Live media plus a usable `qemu:///system` proof host were not available.
  Completion requires the manual PR/release gate in [`runbooks/fedora-proof.md`](runbooks/fedora-proof.md).
- Local focused Python acceptance: **PASS on 2026-08-08 with CPython 3.12.13**. The Program 0
  dependency-tooling, deployment-configuration, and CI-workflow matrix completed 80 tests. This does not
  replace the full repository suites or the pending Fedora VM proof.
