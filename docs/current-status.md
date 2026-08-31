---
easysynq_status_schema: 1
as_of: "2026-08-31"
baseline_commit: "1dcbc2bc12b14e11f037a657d44659412a7a39c0"
last_shipped_slice: "S-rulepack-audit-program"
migration_head: "0091"
next_migration: "0092"
api_unit_tests: 1998
web_test_files: 277
web_tests: 2257
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

The latest completed work is the S-ui interface program, slices S-ui-1 to S-ui-6. Routes and information
architecture are unchanged, as is every permission and gating behaviour, and no migration or permission key
was added. It is otherwise a surface and layout rework, with two exceptions that are not cosmetic. S-ui-2
corrected cache invalidation for the caller's own task list, which was previously refreshed only by the
change-request, improvement and leadership decision branches, so a document approval, a periodic-review
completion, every CAPA decision and both acknowledgement paths left it stale. And S-ui-5a is the one slice
in the program that is not front-end-only: it changes API `ProblemException` titles, OpenAPI descriptions
and the contract lock.

S-ui-4 shared the register page header across eleven register routes and the scorecard band shell across
four, and answered the accepted-duplication question the decisions register had parked. That entry's
measurement was corrected there: the risk, context and interested-parties lifecycle panel and publish
modal are far closer to identical than recorded, with no structural divergence, and the owner's decision
was to collapse the scorecard band only and leave the panel and modal accepted. A shared four-branch page
frame, the Library and Records registers, and register heading-level normalisation were all deliberately
left out; the first and last are tracked in [`open-residuals.md`](open-residuals.md).

S-ui-5a, S-ui-5b and S-ui-5c are corrections found by the owner walking through the running
application, not by a gate. S-ui-5a adopted American-US spelling as the house standard for user-facing
product text; the rule, its exact scope, and the reasons it knowingly diverges from ISO's own English
are **R68** in [`decisions-register.md`](decisions-register.md), which is where that standard first
acquired an authority home — until this pass it existed only in a pull-request body. It renamed no
field, enum or key, but it did change two user-visible API strings and the OpenAPI descriptions, so the
contract lock moved `e66fa80c…` → `041da029…`. S-ui-5b set a 16-pixel gap at each seam after the page
header, where every seam had measured zero, and stopped Mantine truncating status badge labels in
squeezed cells; `e2e/register-rhythm.spec.ts` bounds each gap to 8-24 pixels rather than pinning 16, so
what is proven is separation without double-spacing, not the exact rhythm. S-ui-5c fixed two distinct
mechanisms the owner had reported as one: sortable column headers wrapping, fixed with `nowrap` on the
shared `SortableTh`, and table overflow being invisible, fixed by defaulting `ScrollArea` to
`type: "auto"`.

S-ui-5d then closed the three of those the owner chose to act on. The controlled-document surface had
acquired four different names, one of which — the user manual's — was a wrong navigation instruction
naming a rail entry that no longer existed; every one of them is now **"Master document list"**,
including the API's `report_name`, which is the formal title in the provenance stamp and is therefore
Title Case where the interface labels are sentence case. That reached the OpenAPI document, so the
contract lock moved `041da029…` → `2b8c2503…`. `src/lib/shellLabelContract.test.ts` now pins the rail,
breadcrumb and document-title map to one string per destination, which is the guard whose absence let
the breadcrumb disagree with the rail for a whole slice. And the risk-matrix legend is capped to the
grid it keys.

What remains deferred is [`RES-IP-REGISTER-COLUMN-JUMP`](open-residuals.md) (owner-deferred).
S-rulepack-audit-program closed the rule-pack half of the spelling standard and left the narrower
[`RES-APPROVAL-BLOCK-BRITISH-KEYWORD`](open-residuals.md) behind it. S-ui-6 closed the CAPA board's
coverage gap and replaced it with the narrower
[`RES-CAPA-LIST-TABLE-NO-SCROLL-CONTAINER`](open-residuals.md): that slice's spec measures the board
view, and the page's secondary List table has no scroll container, so the one table on `/capa` is still
unmeasured. The owner's rail-foot idea — a colour-scheme toggle, perhaps a clock — is a feature request
rather than a defect and is deliberately not tracked.

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
compatibility anchor remains `baseline_commit` `1dcbc2bc12b14e11f037a657d44659412a7a39c0`; S-ui-6, like
the slices before it, does not rewrite that implementation-evidence field merely because its branch SHA
differs.

Fresh 2026-08-31 evidence for S-rulepack-audit-program. It moves `api_unit_tests` 1,996 → **1,998**
(two new parametrize cases in an existing file) and nothing else in the frontmatter: it touches no
TypeScript, so the web figures are carried unchanged and NOT restated as freshly verified, and it adds
no migration or contract. It does move `classifier_version` `rule-heuristic-1` → **`rule-heuristic-1.1`**,
which is not a frontmatter field but is the fact most likely to be needed next: the pin is written into
permanent vault import provenance, and a resumed import run adopts it and re-classifies under one
matcher set. The published INTERIM accuracy band was re-measured against the new pin and is unchanged
at kind 0.911 / type 1.000 / clause precision 0.889 / clause recall 1.000 over 45 entries.

Measured locally on the branch. API unit passed **1,998 with the same 2 expected skips**; Ruff lint and
format-check were clean over 769 files and mypy found no issue in 449 source files. The web, integration
and response-contract suites were not run and are not described as passed.

Fresh 2026-08-30 evidence for S-ui-6. The numeric frontmatter above describes `main` after that slice,
whose four new Vitest tests land in two EXISTING files — so `web_tests` moves 2,253 → **2,257** while
`web_test_files` stays 277 — and whose seven new browser cases (`e2e/capa-board.spec.ts`) land in the
Playwright figure, which is not frontmatter. S-ui-6 changes no Python, no migration and no contract, so
`api_unit_tests`, `integration_passed`, `integration_skipped`, `contract_tests`, `migration_head` and
`next_migration` are carried unchanged and are NOT restated as freshly verified. The S-ui-5d, S-ui-5c,
S-ui-4 and 2026-08-17 paragraphs below are retained as the evidence for their own trees and are not
restated as current — in particular the "69 of 69 Chromium tests" figure below belongs to tree
`bdd8f2f`, not to this one.

Measured locally on the S-ui-6 branch, whose tree the squash preserved byte-for-byte as `c28040f`. Web Vitest passed **277 files and 2,257 tests** in 343.86
seconds. The full `npm run test:browser` script — build included, which is the only form that proves
anything, since a bare `playwright test` serves a stale `.playwright-dist` — passed **76 of 76 Chromium
tests** in 1.4 minutes, up seven. ESLint over `src` and `e2e`, both strict `tsc` projects and the
production build were clean. Every layout claim in the new spec was mutation-verified individually;
two assertions in the first draft were found INERT against their own mutations and were rewritten
until they reddened, which is recorded in the slice-history entry rather than smoothed over. The API
unit, integration and response-contract suites were not run locally and are not described as passed;
neither were Firefox, WebKit, assistive-technology sessions, SMTP delivery, deployment, live
acceptance or the disposable Fedora proof.

Measured locally on the merged S-ui-5d tree `bdd8f2f`. API unit passed **1,996 tests with the same 2 expected skips**
in 35.41 seconds; Ruff lint and format-check were clean over 769 files and mypy found no issue in 449
source files. Web Vitest passed **277 files and 2,253 tests** in 428.77 seconds — up two tests and one
file, the new `src/lib/shellLabelContract.test.ts`. The full `npm run test:browser` script passed **69
of 69 Chromium tests** in 1.4 minutes, up two: `e2e/risk-matrix-legend.spec.ts`. ESLint, both strict
`tsc` projects and the production build were clean, as were `check-repo-authority.sh`,
`check-no-site-data.sh` and the 91-fixture agent-authority test. `gen-contracts.sh --check` reports the
contract in sync at SHA-256 `2b8c25038de77414cf0d2ef1473611dd2a9c5e335915a54a3bcca2304bf45f3b`,
regenerated rather than hand-edited and superseding `041da029…`; the Alembic head stayed
`0091_documents_list_index`, so `0092` is still next.

Every claim S-ui-5d makes about layout is mutation-verified, because jsdom can see none of it. Removing
the `maxWidth` cap reddens both new browser cases — including the one asserting the scorecard band sits
beside the matrix at 1200 pixels, which is what establishes that the cap *widens* the side-by-side range
rather than narrowing it. Reverting only the breadcrumb, which is precisely the S-ui-5a defect, reddens
both cases in the new label contract; leaving a stale label beside the new one reddens only the second,
so neither assertion is redundant.

The integration and published response-contract suites were NOT run locally for S-ui-5d, and the
frontmatter's 1,224 integration and 284 contract figures are still carried from the S-ui-4 CI run. That
carry is weaker here than it was for S-ui-5b and S-ui-5c, which were front-end-only: S-ui-5d changes an
API response value and the OpenAPI document, so those suites are exactly the ones with something new to
say about it. Its pull-request CI run `33289631692` exercised them, passing all fifteen executing
pull-request checks with `release-gate` skipped as designed; no local run was performed. Firefox, WebKit,
assistive-technology sessions, SMTP delivery, deployment, live acceptance and the disposable Fedora
proof did not run and are not described as passed.

Measured locally on the merged S-ui-5c tree `6f0e0fd`. API unit passed **1,996 tests with the same 2
expected skips** in 32.86 seconds, the release-ceremony image-digest and image-build opt-ins; S-ui-5a
edited API docstrings, comments and two `ProblemException` titles, and no **API** unit test asserts
those strings. One web unit test does, `ProgramPage.test.tsx:249`, but against its own MSW fixture, so
it pins nothing about the API. Web Vitest passed **276 files and 2,251 tests** with exit code 0 in 353.26 seconds — unchanged,
because S-ui-5a renamed `ProgrammePage.test.tsx` to `ProgramPage.test.tsx` without changing its
assertion count and the other two slices added browser specs rather than unit ones. The full
`npm run test:browser` script, build included, passed **67 of 67 Chromium tests** in 1.3 minutes with one
worker and zero retries — but only after fixing a defect in one of the specs being recorded. S-ui-5c's
scroll cases reddened `web browser (Chromium)` twice on trees that passed locally, and the failing case
moved between runs, which is what identified it as a race rather than the wrong selector first
suspected: `ScrollAreaScrollbarAuto` renders nothing until one of its `ResizeObserver`s fires, so a
single synchronous snapshot can read `barShown: false` while the behaviour is correct. The assertion is
now polled, and the mutation was re-verified against the polling form because a poll that merely times
out would be a tautology. That is a **false failure**, the mirror of the false pass this repository
normally guards against. Playwright is up from 52: S-ui-5a added none, S-ui-5b's `e2e/register-rhythm.spec.ts` added
ten and S-ui-5c's `e2e/register-table-legibility.spec.ts` added five, across nine spec files once
`playwright.config.ts`'s `testIgnore` excludes the two harness probes. `uv run alembic heads` reported
`0091_documents_list_index (head)`, making `0092` next; none of the three slices added a migration.
Contract checking is in sync on this tree at SHA-256
`041da0299dde316f1a6c2ca8acb1106171960ea44e52c19b8b88afd3bf5f7958`, regenerated by
`scripts/gen-contracts.sh` in S-ui-5a and superseding the S-ui-4 hash `e66fa80c…`. Each of the three
pull requests passed all fifteen executing pull-request checks with `release-gate` skipped as designed.

The integration and published response-contract suites were NOT run locally for these three slices; the
frontmatter's 1,224 integration and 284 contract figures are carried from the S-ui-4 CI run, which is
sound for S-ui-5b and S-ui-5c because they are front-end-only, but is NOT fresh evidence for S-ui-5a,
which changed API strings and the OpenAPI document. S-ui-5a's own pull-request CI exercised those
suites; no local re-run was performed here. Firefox, WebKit, assistive-technology sessions, SMTP
delivery, deployment, live acceptance and the disposable Fedora proof did not run and are not described
as passed.

Three coverage limits of the new browser specs are worth stating, because a count of 67 reads as broader
than it is. First, of `register-table-legibility.spec.ts`'s five cases only three are load-bearing —
objectives' "Current / target" wrap and both scroll cases — and showing it took two mutations, not one.
Restoring the pre-fix `RegisterToolbar.tsx` and re-running the full `test:browser` script reddened
exactly one wrap case; removing the theme's `ScrollArea` entry reddened both scroll cases, each having
asserted its `overflows` precondition first. Context's "Last reviewed" and risks' "Risk / opportunity"
each measured a single line box before the fix too, so they are belt-and-braces rather than evidence. Second, the master document list — the surface
whose silent horizontal scrolling prompted the `ScrollArea` change, and which S-ui-5d has since
renamed from "Controlled register" — is reachable by no Playwright
scenario at all, because `reports` is not among the ten register cases in `e2e/support/registers.ts`; its
fix is inferred from the shared theme default, not measured. Third, that theme default also reaches two
bare `ScrollArea` consumers no test covers and no commit message mentions: the navigation rail
(`AppShell.tsx:67`), which now shows a persistent bar on a short viewport, and the CAPA board
(`CapaBoardPage.tsx:232`), whose six 260-pixel columns always overflow, so it now carries a permanently
visible horizontal scrollbar. `ScrollArea.Autosize` in the notification bell is unaffected — it resolves
under a separate theme key. Both are plausibly the intended improvement, but neither is measured, and the
second lands on the one route S-ui-5c declared unmeasurable.

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
re-read here rather than carried forward. The slice merged to `main` as `2626ba9`. Contract checking was in
sync on that tree at SHA-256 `e66fa80c1e7c6ffec3f0a1321a59fea2440cf1f48beb6677c1dbe26cd87243cf`, which
superseded the 2026-08-17 hash recorded below. (That figure was briefly overwritten here with the later
S-ui-5a hash by an authority-document spelling sweep, and is restored: `git show
2626ba9:packages/contracts/.contract.lock` is `e66fa80c…`, and the lock's history shows `041da029…` first
appearing at `030c89d`.)

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
