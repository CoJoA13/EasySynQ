# S-responsive-browser-evidence design

**Status:** Owner approved on 2026-08-13

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 8 of 8 — responsive browser evidence

**Date:** 2026-08-13

**Baseline:** `323fb179` (`main`, squash merge of `S-responsive-data-heavy-views`)

## 1. Outcome

EasySynQ's nine-route shared-register cohort must have durable real-browser proof for the responsive
contract shipped in slice 7. A required Chromium job will measure document and localized table overflow,
prove that later columns and primary actions remain reachable, exercise request-intercepted recovery, and
inspect focus and browser-exposed accessibility semantics at representative narrow and desktop viewports.

The browser suite will run a separate test-only build of the real application route tree and production
components. It will replace only the production authentication provider with deterministic authenticated
state and will fulfill API requests through Playwright. This keeps the proof focused on responsive route
behavior without requiring Docker, FastAPI, PostgreSQL, or Keycloak and without adding a test path to the
production bundle.

The CI workflow will continue to expose the stable `web` aggregate check. That aggregate will depend on
both the existing Vitest shards and the new Chromium job, so a browser failure cannot be hidden behind a
green conventional web check.

## 2. Current behavior and evidence boundary

Slice 7 placed one page-owned `Table.ScrollContainer` around each approved table and gave each route an
explicit content floor:

| Route                 | Minimum table width |
| --------------------- | ------------------: |
| `/tasks`              |              720 px |
| `/audits`             |              800 px |
| `/objectives`         |              720 px |
| `/management-reviews` |              800 px |
| `/dcrs`               |             1040 px |
| `/improvement`        |              920 px |
| `/risks`              |              720 px |
| `/context`            |              880 px |
| `/interested-parties` |              880 px |

Its 260-file, 1,865-test jsdom suite proved component structure, one semantic/control tree, URL and
interaction preservation, and axe-clean representative states. It explicitly did not claim viewport,
clipping, scroll-reachability, focus-ring, forced-colors, request-interception, or screen-reader-oriented
browser evidence.

The web package has no browser-test harness or installed Playwright runtime. The production Vite entry
mounts `AuthProvider`, whose memory-only OIDC state redirects an operational logged-out browser through
Keycloak. A responsive-only browser test therefore cannot reach protected registers reliably by stubbing
ordinary application API calls alone. The test entry must make its authentication boundary explicit
rather than weaken or special-case production authentication.

The current CI topology has ten jobs and fourteen checks. Its stable `web` aggregate depends on two
Vitest shards, with lint and production build running on shard 2. Adding one browser job and retaining the
aggregate produces an expected topology of eleven jobs and fifteen checks.

## 3. Scope

This slice includes:

- `@playwright/test` as a new web development dependency, pinned through the npm lock;
- one Chromium-only Playwright configuration;
- a separate Vite browser-test entry and ignored build output;
- a deterministic authenticated provider value for the browser-test root;
- centralized, fail-closed Playwright request fulfillment using synthetic repository fixtures;
- 320 by 800 and 1280 by 900 geometry coverage for all nine shared-register routes;
- representative failure recovery, keyboard focus, forced-colors, and browser-semantic accessibility
  evidence;
- a dedicated CI browser job wired into the stable `web` aggregate;
- browser-specific npm and `just` commands plus contributor documentation;
- CI topology and shipped-evidence updates in the repository authority documents;
- an ADR for the new top-level dependency and harness boundary; and
- a mirrored debt-registry record for the ADR payoff trigger.

Production responsive or accessibility code may change only when the new browser evidence exposes a
defect inside this approved cohort. Any such change must start with the failing browser proof and retain
slice 7's single semantic/control tree and route-specific ownership.

## 4. Non-goals and preserved boundaries

This slice does not:

- automate Firefox, WebKit, NVDA, JAWS, VoiceOver, or Orca;
- claim actual assistive-technology testing from Playwright's browser semantics;
- test or alter OIDC discovery, login, callback, logout, token storage, or startup recovery;
- require a live API, database, object store, reverse proxy, or identity provider;
- add a production authentication bypass, browser-test route, environment branch, or fixture payload;
- replace page-owned scroll containers with a shared responsive-table abstraction;
- add cards, hidden columns, duplicate mobile controls, a custom breakpoint, or global overflow
  suppression;
- cover specialized tables outside the nine-route cohort;
- change API contracts, migrations, database behavior, deployment, telemetry, or notifications; or
- replace Docker-backed, integration, migration, deployment, Fedora, or manual assistive-technology
  acceptance.

## 5. Harness architecture

### 5.1 Separate browser-test entry

Browser-only source lives under `apps/web/e2e/`. A dedicated Vite configuration builds an HTML entry that
mounts the production `App` with the same Mantine theme, Query client, browser router, application error
boundary, and mutation-feedback lifetime used by the application.

The entry supplies `AuthContext` with a stable ready user and synthetic bearer token instead of mounting
`AuthProvider`. It creates a fresh Query client for each page load and uses the real `BrowserRouter`, so
paths, search parameters, route chrome, shell layout, nested routes, focus ownership, and recovery
boundaries remain real browser behavior.

The normal Vite configuration and production `index.html` remain authoritative for the shipped bundle.
The browser entry is built through its own command into an ignored directory and is never referenced by
the production build, container, or Compose files.

### 5.2 Playwright configuration

The configuration owns one Chromium project, one worker, and zero automatic retries. It starts the
separate browser-test preview server on a loopback-only port and uses a fixed base URL. Each test receives
an isolated browser context.

The suite records a trace and screenshot only on failure. Generated reports, traces, screenshots, build
output, and browser binaries remain ignored artifacts. Failures include the route, viewport, expected
content floor, measured document and container geometry, and any unmatched request.

One worker and no retries are deliberate initial defaults: this evidence gate must expose fixture races
and nondeterminism instead of masking them. The payoff trigger may revisit parallelism after the suite has
enough stable runtime evidence to preserve diagnostics and isolation.

## 6. Request and fixture model

`installRegisterApi(page, scenario)` owns all test API traffic. It installs one catch-all `/api/**`
interceptor before navigation and provides:

- operational setup state;
- current-user and permission responses required by the shell;
- any shell-level supporting responses requested by the mounted route;
- the selected register's loaded response set; and
- an optional ordered override for an HTTP or network failure scenario.

Named data already exported by the Vitest MSW fixtures is reused where practical. The Playwright layer
adds only the URL/method response map and minimal browser-only supporting payloads. If a needed fixture is
not exported, the existing synthetic fixture may be exported rather than copied. Real installation data,
customer identifiers, production hostnames, or credentials are prohibited.

An API request with no declared method-and-path match is aborted and fails the test. External HTTP(S)
requests other than the loopback harness origin are also rejected. This prevents an incomplete fixture
set from silently reaching a developer service or the network.

Failure scenarios are sequential and explicit:

1. the selected register request returns one declared failure;
2. the page renders its named localized error state without a stale table or duplicate controls;
3. the user activates the existing retry control once; and
4. the next request returns the loaded fixture and the one table becomes usable.

One representative scenario uses HTTP 503 and one uses a network abort. Automatic Playwright retries do
not participate in application recovery.

## 7. Geometry evidence

A single explicit manifest lists each route, minimum table width, table identity, final header, and final
primary action. Removing or changing an approved route requires updating that manifest and its expected
contract rather than silently reducing coverage.

### 7.1 Narrow viewport — 320 by 800

For every route the browser proves:

- `document.documentElement.scrollWidth` does not exceed its client width beyond a one-CSS-pixel rounding
  tolerance;
- the page-owned table scroll container fits within the document and has horizontal overflow;
- the rendered table respects at least the route's approved content floor;
- the final header and representative final-column action exist exactly once;
- setting the localized container to its maximum horizontal offset makes the final content reachable
  within the container bounds;
- the search control uses its available narrow width without forcing page overflow;
- filter controls stay within their bounded lane and remain operable; and
- scrolling the table does not duplicate, hide, or reorder the semantic table/control tree.

### 7.2 Desktop viewport — 1280 by 900

For every route the browser proves:

- the document has no unexpected horizontal overflow;
- the toolbar retains its desktop order and 260-pixel search-width contract;
- the full table remains inside its page-owned region;
- the ordered headers and representative primary action remain present once; and
- no narrow-only duplicate presentation or hidden column appears.

Assertions use numeric browser geometry and DOM semantics. Screenshots are diagnostic evidence on failure,
not substitutes for the measurements.

## 8. Representative interaction and accessibility evidence

### 8.1 DCR register

`/dcrs`, the widest cohort table, proves that keyboard movement can reach the offscreen final action and
that the localized container reveals the focused control. In normal rendering the focused control must
match `:focus-visible` and expose a non-none visible focus treatment.

With `forced-colors: active`, the same keyboard-derived focus must expose the application contract of a
two-pixel system-color outline, a two-pixel offset, and no box shadow. The assertion uses computed browser
styles and visibility inside the scroll container rather than a CSS source scan.

### 8.2 Tasks register

`/tasks` proves that the primary subject remains one native link, stays in ordinary tab order, preserves
its accessible name and destination, and participates in the existing row-keyboard behavior without
making the row interactive. A browser ARIA snapshot pins the table, ordered headers, and representative
action semantics.

### 8.3 Context register

`/context` proves that search and the wide filter lane remain reachable and named, filter state remains
browser-exposed, and changing search produces the expected result-count status announcement. Its ARIA
snapshot pins the relevant role, name, state, and live-region structure without treating that snapshot as
an actual screen-reader session.

## 9. CI and contributor workflow

The web package gains explicit commands for browser typechecking/building, serving, and testing. The
repository gains `just test-browser` as the supported local entry. Browser installation remains an
explicit one-time contributor command; ordinary `just setup` continues to hydrate only committed npm
packages and does not download a browser binary or request host package installation.

The new CI job:

1. checks out the repository;
2. installs Node 22 with the web lock as its npm-cache key;
3. runs `npm ci` in `apps/web`;
4. installs Chromium and required Linux packages through Playwright's supported command;
5. runs the browser suite; and
6. uploads the Playwright report, traces, and screenshots only if the test step fails.

The existing `web` aggregate changes its `needs` set to include both `web-shards` and the browser job. Its
gate step checks both results explicitly. CI contract fixtures pin the job, commands, failure propagation,
and stable aggregate name.

Contributor documentation records the install command, local test command, Chromium-only boundary, and
the difference between browser-semantic evidence and actual assistive-technology acceptance.

## 10. Test and implementation sequence

Implementation begins with focused failing proofs:

1. executable repository/CI contract tests for the new command and aggregate-gate topology;
2. the browser harness smoke test, initially failing before the entry and request fixture layer exist;
3. the nine-route narrow geometry matrix;
4. the nine-route desktop preservation matrix;
5. the two intercepted recovery scenarios; and
6. DCR, Tasks, and Context interaction/accessibility proofs.

If any browser proof reveals a production defect, keep the failing case, make the smallest route or shared
toolbar correction that preserves slice 7's approved structure, and rerun the affected Vitest preservation
selection before proceeding.

## 11. Acceptance criteria

The slice is complete only when all of the following are true:

1. All eighteen route-and-viewport geometry cases pass in real Chromium.
2. Every narrow route avoids document-level horizontal overflow and exposes all table content through its
   localized container.
3. Every desktop route preserves the shipped toolbar width, table structure, header order, and one primary
   action.
4. The DCR final action is keyboard reachable and visibly focused in normal and forced-colors rendering.
5. Tasks retain native-control semantics, ordered focus, and browser-exposed table/action structure.
6. Context retains named search/filter controls and a browser-exposed result-count announcement.
7. HTTP 503 and network-abort scenarios render localized failure, accept one user retry, and recover to one
   loaded table without duplicate controls.
8. No undeclared API or external network request succeeds.
9. The browser entry is absent from the production build and deployment inputs.
10. The CI browser job fails closed and the stable `web` aggregate cannot pass unless both Vitest shards
    and Chromium pass.
11. Web lint, browser and production typechecks, production build, the complete Vitest suite, authority
    checks, site-data guards, and diff guards pass with fresh evidence.
12. The handoff makes no Firefox, WebKit, actual screen-reader, backend, Docker, deployment, migration, or
    Fedora claim.

## 12. Verification and evidence

The implementation handoff records exact fresh commands and results for:

- focused RED/GREEN executable CI-contract tests;
- focused Playwright smoke, geometry, recovery, focus, forced-colors, and semantic checks;
- the complete Chromium browser suite;
- affected Vitest preservation tests if production source changes;
- web lint and all relevant TypeScript configurations;
- the normal production build and proof that it excludes the browser entry;
- the complete web Vitest suite through a durable process job if it exceeds one minute;
- repository authority and compatibility fixtures;
- site-data fixtures and direct scan;
- range and working-tree-inclusive diff guards; and
- task-level and whole-branch review with every in-scope Important finding resolved.

A skipped browser install, unavailable Chromium binary, partial route matrix, or diagnostic screenshot does
not become a pass claim.

## 13. Rejected alternatives

### 13.1 Production entry with emulated OIDC

Stubbing OIDC discovery, authorization, callback, token exchange, and session behavior would exercise the
production root but would couple responsive evidence to a separate security boundary. It would make
register layout failures harder to diagnose and risk normalizing a browser-only authentication shortcut.
Authentication needs its own owner-approved browser acceptance slice if that boundary becomes in scope.

### 13.2 Playwright component testing

Direct component mounts would simplify authentication but would not exercise the real browser router,
application shell, route chrome, document overflow, or page-owned scroll boundary together. The route
cohort needs full-page geometry rather than another component-level approximation.

### 13.3 Live Docker and Keycloak stack

A seeded live stack would provide broader integration evidence but would pull database, setup, identity,
deployment, fixture lifecycle, and host-runtime availability into a responsive frontend slice. Existing
integration and Fedora gates retain those responsibilities.

### 13.4 Multi-engine matrix

Firefox and WebKit would broaden compatibility evidence, but the current target is a deterministic first
browser gate for the slice-7 contract. Chromium supports the required viewport, interception,
accessibility-snapshot, focus, and forced-colors APIs with the smallest CI and maintenance increase.

### 13.5 One-time or manual-only browser evidence

Local screenshots or a manual PR checklist would demonstrate one run but would not prevent the responsive
contract from regressing. The owner selected a CI-enforced, checked-in harness.

## 14. Decision record and payoff trigger

The new top-level Playwright dependency and separate authenticated harness have multiple credible
alternatives and therefore require a Nygard ADR. The ADR records Chromium-only scope, test-only auth
substitution, fail-closed request interception, CI enforcement, and the rejected alternatives above.

Its payoff trigger is reached when production authentication itself needs browser acceptance, another
browser engine exposes a material divergence, or the browser cohort expands beyond this focused harness.
At that point, reassess the dedicated entry, engine matrix, fixture ownership, worker count, and whether a
live-stack layer is warranted. The debt registry mirrors this deliberate boundary.

## 15. Programme boundary

This slice closes Programme 1 only when its required browser gate and all acceptance evidence are shipped.
It does not close any open Programme 0 residual or pre-authorize the next product programme. The next slice
must be selected from current repository authority and owner direction after this browser evidence is
reviewed and merged.
