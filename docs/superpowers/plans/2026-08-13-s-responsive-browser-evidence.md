# Responsive Browser Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Make the responsive contract for EasySynQ's nine shared-register routes a required,
deterministic Chromium CI gate with real viewport, localized scroll reachability, intercepted recovery,
focus, forced-colors, and browser-semantic accessibility evidence.

**Architecture:** Build a separate Vite browser-test entry that mounts the production `App`, route tree,
shell, theme, and application providers while substituting deterministic test authentication. Playwright
owns a fail-closed API fixture router and runs one Chromium project against that entry. A route manifest
drives all narrow and desktop geometry cases; focused specs cover HTTP/network recovery, DCR far-edge
focus, Tasks native row navigation, and Context live-region semantics. A new CI job feeds the existing
stable `web` aggregate.

**Tech Stack:** React 19, TypeScript 6, Mantine 7, React Router 7, TanStack Query 5, Vite 8, Vitest 4,
MSW 2 fixture data, Playwright Test with Chromium, GitHub Actions, Bash and pytest CI contract tests.

## Global constraints

- Work only in `/home/cjones/Desktop/EasySynQ/.worktrees/responsive-browser-evidence` on
  `codex/responsive-browser-evidence`.
- The implementation baseline is design clarification commit `06ebfdc`; do not alter the primary
  checkout's untracked `.superdesign/` or either unrelated prunable worktree registration.
- Read any existing `docs/debt/` entry before editing a file it references.
- Add `@playwright/test` only as an `apps/web` development dependency. Do not add Axe, component-test,
  authentication, server, or multi-browser dependencies.
- The browser entry must remain outside the production Vite entry, `apps/web/dist`, Dockerfiles, Compose,
  and deployment inputs. Add no production auth bypass or environment switch.
- Use one Chromium worker and zero automatic retries. Traces, screenshots, reports, the browser build,
  and downloaded binaries are generated/ignored evidence, never commits.
- Block undeclared `/api/**` and non-loopback HTTP(S) traffic. Use synthetic repository fixture data only.
- Preserve all slice-7 widths, toolbar behavior, native controls, structural rows, URL/history behavior,
  drawer ownership, route recovery, permissions, and production provider lifetime.
- Change production route or shared UI source only after a real browser test proves an in-scope defect.
- Do not edit specialized tables, API/OpenAPI/generated contracts, migrations/database, OIDC behavior,
  Docker/Compose, telemetry, notifications, or Fedora proof machinery.
- Start each behavior task with a focused failing proof. After GREEN, run the smallest adjacent Vitest or
  contract check, inspect the task diff, and commit a bounded checkpoint.
- Use app-owned Prettier after dependencies are hydrated. Do not mass-format `docs/slice-history.md`.
- Do not claim Firefox, WebKit, an actual screen reader, backend integration, Docker, deployment,
  migration, or Fedora evidence.

## File ownership map

- `apps/web/package.json`, `apps/web/package-lock.json` — Playwright dependency and browser commands.
- `apps/web/playwright.config.ts`, `apps/web/tsconfig.browser.json` — one-project browser runner and strict
  type boundary.
- `apps/web/e2e/index.html`, `apps/web/e2e/vite.config.ts`, `apps/web/e2e/main.tsx` — separate built
  application entry.
- `apps/web/e2e/support/api.ts` — fail-closed request map and ordered failure scenarios.
- `apps/web/e2e/support/registers.ts` — exact nine-route evidence manifest and geometry helpers.
- `apps/web/e2e/smoke.spec.ts`, `register-geometry.spec.ts`, `register-recovery.spec.ts`, and
  `register-accessibility.spec.ts` — browser evidence.
- `apps/web/src/test/msw/handlers.ts` — export existing synthetic list fixtures when browser tests need
  them; do not duplicate payloads or change Vitest behavior.
- `.gitignore`, `justfile`, `docs/dev-workflow.md` — generated-artifact and contributor commands.
- `.github/workflows/ci.yml`, `scripts/tests/test-ci-hardening.sh`,
  `apps/api/tests/unit/test_ci_workflow.py` — required browser job and stable aggregate failure propagation.
- `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md` plus the debt-ops-generated entry — new
  dependency/harness decision and payoff trigger.
- `docs/current-status.md`, `docs/slice-history.md` — final reviewed evidence and Programme 1 closure.

---

### Task 1: Record the dependency decision and hydrate Playwright

**Files:**

- Create: `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`
- Create: one `docs/debt/<timestamp>-playwright-responsive-browser-harness.md` through debt-ops
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`

**Interfaces:**

- Adds one top-level development dependency: `@playwright/test`.
- Records Chromium-only, separate-entry, deterministic-auth, fail-closed interception, and required-CI
  decisions.
- Leaves runtime dependencies and production artifacts unchanged.

- [ ] **Step 1: Draft ADR 0003 before changing the manifest**

Use Nygard format with this exact structure:

```markdown
# 0003 — Use Playwright for responsive browser evidence

**Date:** 2026-08-13

**Status:** Accepted

## Context
...

## Decision
...

## Consequences
...

## Alternatives

### Production entry with emulated OIDC
...

### Playwright component testing
...

### Live Docker and Keycloak stack
...

### Multi-engine or manual-only evidence
...

## Payoff trigger
...
```

Decision text must state that the dedicated entry is test-only, Chromium is the only engine, API and
external traffic fail closed, and the stable `web` aggregate requires the browser job. The payoff trigger
is production-auth browser acceptance, a material non-Chromium divergence, or expansion beyond the focused
cohort.

- [ ] **Step 2: Invoke debt-ops immediately for the ADR boundary**

Read and use the `debt-ops:add` skill. Register the dedicated auth substitution, Chromium-only engine,
single-worker default, and fixture ownership under one deliberate-debt entry that links ADR 0003. Use the
ADR payoff trigger verbatim. Keep the emitted `+1 entry: ...` line as the tool result and record the actual
generated file for Task 8.

- [ ] **Step 3: Install the locked development dependency**

```bash
npm install --prefix apps/web --save-dev @playwright/test
```

If registry access is sandbox-blocked, retry only this scoped npm command with the required approval. Do
not hand-edit the lock or use an uncommitted global package.

- [ ] **Step 4: Inspect the dependency delta and audit it**

```bash
npm ls --prefix apps/web @playwright/test playwright playwright-core --depth=1
npm --prefix apps/web audit --package-lock-only --audit-level=high
git diff --check -- apps/web/package.json apps/web/package-lock.json docs/adr/0003-use-playwright-for-responsive-browser-evidence.md
git diff --stat
```

Expected: Playwright resolves from the committed web tree, the high/critical audit exits 0, only the
manifest/lock plus ADR/debt record changed, and no runtime dependency moved.

- [ ] **Step 5: Commit the decision/dependency checkpoint**

```bash
git add apps/web/package.json apps/web/package-lock.json docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/debt/<actual-file>.md
git commit -m "chore: establish responsive browser harness"
```

---

### Task 2: Build the isolated browser entry and smoke proof

**Files:**

- Modify: `.gitignore`
- Modify: `apps/web/package.json`
- Create: `apps/web/tsconfig.browser.json`
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/index.html`
- Create: `apps/web/e2e/vite.config.ts`
- Create: `apps/web/e2e/main.tsx`
- Create: `apps/web/e2e/support/api.ts`
- Create: `apps/web/e2e/smoke.spec.ts`
- Verify: `apps/web/src/main.tsx` remains unchanged

**Interfaces:**

- `npm run build:browser` typechecks and builds only the browser entry into
  `apps/web/.playwright-dist/`.
- `npm run preview:browser` serves that build on `127.0.0.1:4174`.
- `npm run test:browser` builds and runs one Chromium project with no retries and one worker.
- `installRegisterApi(page, { route: "tasks" })` fulfills the shell plus Tasks APIs and rejects every
  undeclared request.

- [ ] **Step 1: Write the smoke test before the harness exists**

Create `apps/web/e2e/smoke.spec.ts`:

```ts
import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

test("mounts the real routed shell with deterministic authenticated data", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  await page.goto("/tasks");

  await expect(page).toHaveTitle(/Tasks/);
  await expect(page.getByRole("heading", { name: "Review and approve" })).toBeVisible();
  await expect(page.getByRole("table", { name: "My tasks" })).toHaveCount(1);
  await expect(page.getByRole("link", { name: /SOP-PUR-014/ })).toHaveCount(1);
});
```

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web run test:browser -- e2e/smoke.spec.ts
```

Expected: FAIL because the browser command/config, entry, and request helper do not exist.

- [ ] **Step 3: Add ignored outputs and browser scripts**

Append these ignored paths under the web section of `.gitignore`:

```gitignore
apps/web/.playwright-dist/
apps/web/playwright-report/
apps/web/test-results/
```

Add package scripts:

```json
"build:browser": "tsc -p tsconfig.browser.json --noEmit && vite build --config e2e/vite.config.ts",
"preview:browser": "vite preview --config e2e/vite.config.ts --host 127.0.0.1",
"test:browser": "npm run build:browser && playwright test"
```

`tsconfig.browser.json` extends the production config, includes `src`, `e2e`, and
`playwright.config.ts`, and supplies Vite, Node, and Playwright types without weakening strict flags.

- [ ] **Step 4: Add the separate Vite and Playwright configurations**

`apps/web/e2e/vite.config.ts` must set:

```ts
export default defineConfig({
  root: __dirname,
  publicDir: "../public",
  plugins: [react()],
  build: { outDir: "../.playwright-dist", emptyOutDir: true },
  preview: { host: "127.0.0.1", port: 4174, strictPort: true },
});
```

Use the repository-approved ESM-safe directory calculation if `__dirname` is unavailable under the
current TypeScript/Vite setup. Do not change the production `vite.config.ts`.

`apps/web/playwright.config.ts` must use `testDir: "./e2e"`, match `*.spec.ts`, set zero retries,
one worker, Chromium only, `baseURL: "http://127.0.0.1:4174"`, failure-only screenshot/trace retention,
HTML plus line reporters, and a web server command of `npm run preview:browser`.

- [ ] **Step 5: Mount the real app with test-only authentication**

`apps/web/e2e/main.tsx` mirrors the production provider order but replaces `AuthProvider` only:

```tsx
const auth: AuthState = {
  status: { kind: "ready" },
  token: "browser-test-token",
  user: { access_token: "browser-test-token", profile: { sub: TEST_USER_ID } } as AuthState["user"],
  login: async () => undefined,
  retry: async () => undefined,
  logout: async () => undefined,
};

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
```

Mount `MantineProvider`, `ApplicationErrorBoundary`, `QueryClientProvider`, `BrowserRouter`,
`AuthContext.Provider`, and `App`, importing the same Mantine and application CSS as production. Keep
`React.StrictMode`. Do not add a runtime check or export from production `main.tsx`.

- [ ] **Step 6: Implement the minimal fail-closed Tasks request map**

In `e2e/support/api.ts`, import existing `taskFixture`, `notificationFixtures`, and synthetic user data.
Fulfill these method/path families:

- `GET /api/v1/setup/state` → `OPERATIONAL`;
- `GET /api/v1/me` → the synthetic current user;
- `GET /api/v1/me/permissions` → an empty permission set at SYSTEM scope;
- `GET /api/v1/notifications?unread_only=true&limit=100` → `[]`;
- `GET /api/v1/notifications/stream` → an empty event-stream response accepted on reconnect;
- `GET /api/v1/tasks?assignee=me&state=PENDING` → two synthetic rows derived from `taskFixture`, so
  Arrow Down can later move between primary links.

Match method plus pathname and validate relevant search parameters. Abort and throw with method/full URL
for any unmatched `/api/**` request. Reject non-loopback HTTP(S) traffic while allowing the harness origin
and static assets.

- [ ] **Step 7: Install Chromium once and run GREEN**

```bash
cd apps/web && npx playwright install chromium
npm run test:browser -- e2e/smoke.spec.ts
npm run build
git status --short
git diff --check
```

If browser download is sandbox-blocked, retry only `npx playwright install chromium` with the required
approval. Expected: one smoke test passes in Chromium; the normal production build passes and does not
contain the browser entry; only ignored browser outputs are generated.

- [ ] **Step 8: Format, inspect, and commit**

```bash
apps/web/node_modules/.bin/prettier --write apps/web/playwright.config.ts apps/web/tsconfig.browser.json apps/web/e2e
apps/web/node_modules/.bin/prettier --check apps/web/playwright.config.ts apps/web/tsconfig.browser.json apps/web/e2e
npm --prefix apps/web run lint
npm --prefix apps/web run build:browser
git diff --check
git diff --stat
git status --short --ignored
git add .gitignore apps/web/package.json apps/web/tsconfig.browser.json apps/web/playwright.config.ts apps/web/e2e
git commit -m "test: add isolated browser harness"
```

Confirm `apps/web/src/main.tsx`, production Vite config, Dockerfiles, and Compose files are unchanged.

---

### Task 3: Make Chromium evidence part of the stable web CI gate

**Files:**

- Modify: `scripts/tests/test-ci-hardening.sh`
- Modify: `apps/api/tests/unit/test_ci_workflow.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `justfile`

**Interfaces:**

- Adds CI job `web-browser`, displayed as `web browser (Chromium)`.
- Stable job `web` depends on `web-shards` and `web-browser` and rejects either non-success result.
- Adds `just test-browser` as the local repository command.

- [ ] **Step 1: Write shell and semantic CI RED proofs**

Extend `test-ci-hardening.sh` to extract `WEB_BROWSER_BLOCK` and require:

- checkout and Node 22 with `apps/web/package-lock.json` cache;
- `npm ci` in `apps/web`;
- `npx playwright install --with-deps chromium`;
- unconditional `npm run test:browser`;
- no `continue-on-error`, `|| true`, changed-file selection, or test retries;
- a failure-only `actions/upload-artifact` step for `playwright-report` and `test-results`;
- `web` needs both jobs and checks both results; and
- `just test-browser` invokes the locked web script.

Update `test_ci_workflow.py` with semantic dictionaries for the browser job and aggregate. The aggregate
must look like:

```yaml
needs: [web-shards, web-browser]
if: ${{ always() }}
```

and inspect both `${{ needs.web-shards.result }}` and `${{ needs.web-browser.result }}` before exiting.

- [ ] **Step 2: Run RED**

```bash
bash scripts/tests/test-ci-hardening.sh
cd apps/api && uv run pytest tests/unit/test_ci_workflow.py -m unit -q
```

Expected: both fail because the workflow has no browser job and the aggregate still depends only on
`web-shards`.

- [ ] **Step 3: Implement the CI job and stable aggregate**

Add before the stable `web` job:

```yaml
web-browser:
  name: web browser (Chromium)
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v7
    - uses: actions/setup-node@v7
      with:
        node-version: "22"
        cache: npm
        cache-dependency-path: apps/web/package-lock.json
    - working-directory: apps/web
      run: npm ci
    - name: install Chromium
      working-directory: apps/web
      run: npx playwright install --with-deps chromium
    - name: responsive browser evidence
      working-directory: apps/web
      run: npm run test:browser
    - name: upload browser diagnostics
      if: ${{ failure() }}
      uses: actions/upload-artifact@v6
      with:
        name: playwright-report
        path: |
          apps/web/playwright-report
          apps/web/test-results
        if-no-files-found: ignore
        retention-days: 7
```

Change `web.needs` and its shell gate to name both failing results. Keep `name: web` unchanged. Add:

```just
test-browser:
    cd apps/web && npm run test:browser
```

- [ ] **Step 4: Run GREEN and inspect topology**

```bash
bash scripts/tests/test-ci-hardening.sh
cd apps/api && uv run pytest tests/unit/test_ci_workflow.py -m unit -q
git diff --check -- .github/workflows/ci.yml scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_ci_workflow.py justfile
rg -n '^  [[:alnum:]_-]+:$' .github/workflows/ci.yml
```

Expected: both contract suites pass; eleven workflow jobs are visible; the stable `web` gate still exists
once and checks both prerequisites.

- [ ] **Step 5: Commit the CI enforcement checkpoint**

```bash
git add .github/workflows/ci.yml scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_ci_workflow.py justfile
git commit -m "ci: require Chromium browser evidence"
```

---

### Task 4: Prove all nine route geometries at narrow and desktop viewports

**Files:**

- Modify: `apps/web/src/test/msw/handlers.ts`
- Expand: `apps/web/e2e/support/api.ts`
- Create: `apps/web/e2e/support/registers.ts`
- Create: `apps/web/e2e/register-geometry.spec.ts`
- Verify: all nine production route files remain unchanged unless RED identifies a defect

**Interfaces:**

- Exports the existing `mgmtReviewListFixture` and `dcrListFixture`; fixture content and MSW handlers stay
  unchanged.
- `REGISTER_CASES` is the exact approved route/floor/far-edge/action inventory.
- `measureRegister(page, registerCase)` returns numeric document, container, table, toolbar, and far-edge
  geometry after moving only the localized container.

- [ ] **Step 1: Declare the exact manifest and failing geometry matrix**

Create `e2e/support/registers.ts` with this inventory:

| Key | Path | Floor | Final header | Representative primary action |
|---|---|---:|---|---|
| tasks | `/tasks` | 720 | Due | `SOP-PUR-014` link |
| audits | `/audits` | 800 | Started | `REC-000061` link |
| objectives | `/objectives` | 720 | Due | `OBJ-001` link |
| management reviews | `/management-reviews` | 800 | Status | `MR-001` link |
| dcrs | `/dcrs` | 1040 | Created | `DCR-2026-0001` button |
| improvement | `/improvement` | 920 | Opened | `IMP-2026-0001` button |
| risks | `/risks` | 720 | Treatment | `Supplier single point of failure` button |
| context | `/context` | 880 | Last reviewed | `Skilled and certified QA team` button |
| interested parties | `/interested-parties` | 880 | Last reviewed | `Acme Manufacturing` button |

Include the expected search placeholder and first filter accessible name where present. Keep the manifest
`as const` and type-check role as `"link" | "button"`.

In `register-geometry.spec.ts`, parameterize each case at `{ width: 320, height: 800 }` and
`{ width: 1280, height: 900 }`. The initial test should expect a helper that does not exist yet.

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web run test:browser -- e2e/register-geometry.spec.ts
```

Expected: FAIL because the complete fixture map and geometry helper are absent.

- [ ] **Step 3: Reuse the existing fixture payloads**

Export `mgmtReviewListFixture` and `dcrListFixture` in `src/test/msw/handlers.ts` without changing their
values or handler order. Import these plus `taskFixture`, `auditListFixture`, `objectiveFixtures`,
`initiativeFixtures`, `riskListFixture`, `contextListFixture`, `contextRegisterStatusFixture`,
`interestedPartyListFixture`, `interestedPartyRegisterStatusFixture`, `directoryFixture`, and
`processesFixture` into `e2e/support/api.ts`.

Add response families:

- `/api/v1/audits` and `/api/v1/directory/users`;
- `/api/v1/objectives/scorecard` with `total`, `on_target`, `by_rag`, and `objectives` derived from
  `objectiveFixtures`;
- `/api/v1/management-reviews`;
- `/api/v1/dcrs`;
- `/api/v1/improvement-initiatives` plus directory;
- `/api/v1/risks`, `/api/v1/risks/register`, `/api/v1/processes`, and scoped permissions;
- `/api/v1/context` and `/api/v1/context/register`; and
- `/api/v1/interested-parties` and `/api/v1/interested-parties/register`.

Match query strings where the production hook requires them, and do not add blanket success fallbacks.

- [ ] **Step 4: Implement browser geometry measurement**

From the one visible `table`, walk ancestors in the page until computed `overflow-x` is `auto` or
`scroll`. Fail if none exists or if more than one visible table exists. Measure:

```ts
type RegisterGeometry = {
  documentClientWidth: number;
  documentScrollWidth: number;
  containerClientWidth: number;
  containerScrollWidth: number;
  tableWidth: number;
  searchWidth: number;
  farEdgeInsideAfterScroll: boolean;
};
```

At narrow width assert:

- document overflow is at most one CSS pixel;
- the localized container fits the document and `scrollWidth > clientWidth`;
- its scroll width and table presentation meet the declared floor within one pixel;
- after setting only `container.scrollLeft = container.scrollWidth`, the final header is inside the
  container bounds;
- the search input is wide enough to consume the available toolbar lane and never exceeds the viewport;
- optional filters are visible/reachable; and
- final header and representative primary action each exist once.

At desktop assert:

- document overflow is at most one pixel;
- the search root is 260 px within a one-pixel tolerance;
- ordered headers match the manifest's route contract; and
- the primary action exists once with its native role.

Do not assert that every desktop table must overflow; narrower floors may fit.

- [ ] **Step 5: Run the 18-case GREEN proof**

```bash
npm --prefix apps/web run test:browser -- e2e/register-geometry.spec.ts
npm --prefix apps/web test -- src/lib/RegisterToolbar.test.tsx src/lib/responsiveRegisterContract.test.ts
npm --prefix apps/web run build
```

Expected: all eighteen browser cases and focused slice-7 preservation tests pass. If a browser case proves
a production defect, stop, retain the failing test, apply only the smallest cohort fix, run its existing
page Vitest suite, and document the measured before/after behavior in the commit.

- [ ] **Step 6: Format, inspect, and commit**

```bash
apps/web/node_modules/.bin/prettier --write apps/web/e2e apps/web/src/test/msw/handlers.ts
apps/web/node_modules/.bin/prettier --check apps/web/e2e apps/web/src/test/msw/handlers.ts
npm --prefix apps/web run lint
npm --prefix apps/web run build:browser
git diff --check
git diff --stat 06ebfdc..HEAD
git add apps/web/e2e apps/web/src/test/msw/handlers.ts
git commit -m "test: measure responsive register geometry"
```

---

### Task 5: Prove intercepted HTTP and network recovery

**Files:**

- Expand: `apps/web/e2e/support/api.ts`
- Create: `apps/web/e2e/register-recovery.spec.ts`

**Interfaces:**

- A scenario can override one exact method/path with ordered outcomes: `http-503`, `network-error`, then
  `loaded`.
- Request counts are observable by the test and unmatched traffic remains fatal.

- [ ] **Step 1: Write the failing DCR and Context recovery tests**

For `/dcrs`, return one RFC-9457-shaped 503 for `GET /api/v1/dcrs`, then the loaded fixture. Assert:

```ts
await expect(page.getByText("Couldn't load change requests")).toBeVisible();
await expect(page.getByRole("table")).toHaveCount(0);
await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(1);
await page.getByRole("button", { name: "Try again" }).click();
await expect(page.getByRole("table")).toHaveCount(1);
expect(requestCount("GET", "/api/v1/dcrs")).toBe(2);
```

For `/context`, abort the first `GET /api/v1/context`, then fulfill the second. Assert the named context
error, one retry control, absence of stale table/control duplicates, and exactly two requests.

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web run test:browser -- e2e/register-recovery.spec.ts
```

Expected: FAIL because ordered overrides and request accounting do not exist.

- [ ] **Step 3: Add ordered scenario handling**

Implement an exact-key queue keyed by uppercase method plus pathname. A declared `http-503` uses JSON
content type and `{ code, title, detail }`; `network-error` uses Playwright's request abort; `loaded`
delegates to the normal route response. Dequeue only the target register request, not shell polling or the
notification stream. Expose a read-only request-count function to the test.

- [ ] **Step 4: Run GREEN and regression checks**

```bash
npm --prefix apps/web run test:browser -- e2e/register-recovery.spec.ts
npm --prefix apps/web test -- src/features/dcr/DcrsRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx
npm --prefix apps/web run build:browser
git diff --check
```

Expected: two recovery tests pass with exactly one user retry each; adjacent Vitest suites remain green.

- [ ] **Step 5: Commit**

```bash
git add apps/web/e2e/support/api.ts apps/web/e2e/register-recovery.spec.ts
git commit -m "test: prove register request recovery"
```

---

### Task 6: Prove focus, forced-colors, keyboard, and browser semantics

**Files:**

- Create: `apps/web/e2e/register-accessibility.spec.ts`
- Expand only if needed: `apps/web/e2e/support/registers.ts`
- Verify: `apps/web/src/index.css`, `apps/web/src/lib/useRowKeyboardNav.ts`, and the three route files
  remain unchanged unless RED proves a defect

**Interfaces:**

- DCR: keyboard-derived far-edge `Sort by Created` focus in normal and forced-colors rendering.
- Tasks: two native subject links, ordinary tab order, Arrow Down row navigation, and table ARIA snapshot.
- Context: named search/filter semantics, live result-count update, and focused ARIA snapshot.

- [ ] **Step 1: Write the failing representative accessibility tests**

Add four focused tests:

1. DCR normal mode: focus `Sort by State`, press `Tab`, require `Sort by Created` to become active,
   match `:focus-visible`, and lie within the localized container after browser scrolling.
2. DCR forced colors: call `page.emulateMedia({ forcedColors: "active" })` before navigation, repeat the
   keyboard step, then require computed `outline-style: solid`, `outline-width: 2px`,
   `outline-offset: 2px`, and `box-shadow: none`.
3. Tasks: require two native links, focus the first, press `ArrowDown`, require the second to receive focus,
   verify both stay in ordinary tab order and rows have no interactive role/tabindex, then pin the table's
   browser ARIA snapshot including ordered headers and named links.
4. Context: pin role/name/state structure for Search and the three filter controls, type `legacy`, wait for
   the debounced result, require the polite live region to announce one issue, and pin the filtered table
   snapshot with one named primary button.

Generate the initial ARIA snapshot from `locator.ariaSnapshot()`, review it for only stable semantic
content, then commit the smallest readable `toMatchAriaSnapshot` contract. Do not snapshot decorative SVG
or generated Mantine class names.

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web run test:browser -- e2e/register-accessibility.spec.ts
```

Expected: FAIL until the exact focus/container helper and reviewed ARIA contracts are implemented. A
production CSS or behavior failure remains RED evidence and must not be weakened into a source assertion.

- [ ] **Step 3: Add only test helpers needed for the approved evidence**

Extend `registers.ts` with helpers that:

- identify the nearest localized horizontal scroll owner from the table;
- report whether the active element's bounding box is inside it;
- read computed focus styles; and
- assert one structural table and one `[data-rownav]` control per fixture row.

Do not add production test IDs. Prefer browser roles, accessible names, visible text, and computed layout.

- [ ] **Step 4: Run GREEN and adjacent preservation tests**

```bash
npm --prefix apps/web run test:browser -- e2e/register-accessibility.spec.ts
npm --prefix apps/web test -- src/index.test.ts src/lib/useRowKeyboardNav.test.tsx src/features/review/TasksInbox.test.tsx src/features/dcr/DcrsRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
git diff --check
```

Expected: normal and forced-colors focus, Tasks keyboard/semantics, and Context search/live-region evidence
pass in Chromium; all adjacent jsdom preservation tests remain green.

- [ ] **Step 5: Commit**

```bash
git add apps/web/e2e/register-accessibility.spec.ts apps/web/e2e/support/registers.ts
git commit -m "test: verify browser accessibility evidence"
```

---

### Task 7: Complete contributor documentation and implementation review

**Files:**

- Modify: `docs/dev-workflow.md`
- Verify: all implementation files from Tasks 1-6

- [ ] **Step 1: Document the supported local browser workflow**

Add a focused subsection to `docs/dev-workflow.md` under local loops:

```bash
cd apps/web
npx playwright install chromium   # one-time browser download
npm run test:browser
# or from the repository root:
just test-browser
```

State that CI installs Chromium plus Linux dependencies, local setup does not download a browser,
the suite is Chromium-only and backend-free, generated diagnostics are ignored, and ARIA/browser
semantics are not an actual screen-reader claim.

- [ ] **Step 2: Run the complete browser suite and static gates**

```bash
npm --prefix apps/web run test:browser
npm --prefix apps/web run lint
npm --prefix apps/web run build:browser
npm --prefix apps/web run build
bash scripts/tests/test-ci-hardening.sh
cd apps/api && uv run pytest tests/unit/test_ci_workflow.py -m unit -q
```

If the browser suite exceeds one minute, use the durable process-job workflow and retrieve its bounded
result. Record exact browser test count, duration, and any diagnostic warnings. Expected: every command
exits 0, the browser runner reports Chromium only and zero retries, and the production build excludes the
browser entry.

- [ ] **Step 3: Run task-level requirements review**

Review `323fb179..HEAD` against all fifteen design acceptance criteria. Confirm:

- exact nine-route/two-viewport matrix;
- numeric document/container/table/far-edge measurements;
- native primary actions and no production test IDs;
- DCR normal/forced-colors focus;
- Tasks row keyboard/native semantics;
- Context search/filter/live-region semantics;
- one HTTP and one network recovery with exactly one user retry;
- fail-closed API/external traffic;
- separate production and browser entries;
- stable `web` aggregate failure propagation; and
- Chromium-only/no-AT/no-backend claim boundaries.

For every Critical or Important finding, add the smallest failing test, prove RED, fix, rerun the focused
and adjacent checks, and commit `fix: address browser evidence review`.

- [ ] **Step 4: Run code-quality review**

Inspect for route-handler ambiguity, silent fixture fallback, query-string mismatch, production leakage,
geometry rounding errors, arbitrary sleeps, screenshot-only assertions, focus via script instead of
keyboard, brittle generated classes, ARIA snapshot noise, automatic retries, stale build reuse, artifact
tracking, credential-like fixture data, and unrelated edits. Resolve all in-scope Important findings with
RED/GREEN evidence.

- [ ] **Step 5: Format scoped files and rerun final implementation gates**

```bash
apps/web/node_modules/.bin/prettier --write apps/web/playwright.config.ts apps/web/tsconfig.browser.json apps/web/e2e apps/web/src/test/msw/handlers.ts
apps/web/node_modules/.bin/prettier --check apps/web/playwright.config.ts apps/web/tsconfig.browser.json apps/web/e2e apps/web/src/test/msw/handlers.ts
npm --prefix apps/web run test:browser
npm --prefix apps/web run lint
npm --prefix apps/web run build:browser
npm --prefix apps/web run build
bash scripts/tests/test-ci-hardening.sh
cd apps/api && uv run pytest tests/unit/test_ci_workflow.py -m unit -q
git diff --check 323fb179..HEAD
git diff --check
```

- [ ] **Step 6: Commit documentation/review fixes and record the implementation head**

```bash
git add docs/dev-workflow.md
git commit -m "docs: document browser evidence workflow"
git status --short --branch
git rev-parse --short HEAD
git log --oneline 323fb179..HEAD
```

Expected: clean worktree. Preserve this exact implementation SHA for Task 8; the later evidence-only
commit is not the implementation baseline.

---

### Task 8: Run complete evidence and close Programme 1 authority

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Verify: `docs/open-residuals.md` remains unchanged unless a genuine new current residual was discovered
- Verify: design, plan, ADR, and debt record links resolve

- [ ] **Step 1: Run the complete web Vitest suite through a durable process job**

Use the process-jobs start workflow from the isolated worktree with direct argv
`npm --prefix apps/web test`. Retrieve the completed result through the supported result workflow.
Expected: exit 0 with every Vitest file/test passing and no unhandled error. Record exact file/test counts,
duration, job ID, and any repeated Node `localStorage` warning; do not merge those counts with Playwright's
separate browser count.

- [ ] **Step 2: Re-run the final Chromium evidence on the reviewed head**

```bash
npm --prefix apps/web run test:browser
```

Record the exact Chromium test count and duration. If longer than one minute, use a durable process job.
Do not turn an unavailable browser or partial spec selection into completion evidence.

- [ ] **Step 3: Update `docs/current-status.md` from fresh evidence**

Set:

- `baseline_commit` to Task 7's exact reviewed implementation SHA;
- `last_shipped_slice` to `S-responsive-browser-evidence`;
- `web_test_files` and `web_tests` to Task 8 Step 1's exact Vitest totals; and
- `ci_jobs: 11` and `ci_checks: 15` from the executable workflow topology.

Leave migration, API, contract, and integration counts unchanged unless their full authoritative gates ran.
Replace the slice-7 browser exclusion with exact slice-8 Chromium evidence, commands, route/viewport
matrix, recovery/focus/semantic results, dependency/ADR decision, reviews, warnings, and the explicit
Firefox/WebKit/actual-AT/backend/Docker/deployment/Fedora exclusions.

- [ ] **Step 4: Add the Programme 1 history entry**

Insert `### S-responsive-browser-evidence — required Chromium register proof` above the slice-7 entry in
`docs/slice-history.md`. Record:

- dedicated test-only entry and fail-closed fixture boundary;
- nine routes, both viewports, and exact browser count;
- HTTP/network recovery, DCR focus/forced colors, Tasks semantics, and Context live region;
- CI job/aggregate and 11-job/15-check topology;
- exact focused/full/static evidence and review fixes;
- links to design, plan, ADR 0003, and the actual debt entry; and
- honest non-Chromium, actual-AT, backend, Docker, deployment, migration, and Fedora boundaries.

State that this closes Programme 1 only; do not select or claim the next programme.

- [ ] **Step 5: Format scoped documentation**

```bash
apps/web/node_modules/.bin/prettier --write docs/current-status.md docs/dev-workflow.md docs/superpowers/specs/2026-08-13-s-responsive-browser-evidence-design.md docs/superpowers/plans/2026-08-13-s-responsive-browser-evidence.md docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/debt/<actual-file>.md
apps/web/node_modules/.bin/prettier --check docs/current-status.md docs/dev-workflow.md docs/superpowers/specs/2026-08-13-s-responsive-browser-evidence-design.md docs/superpowers/plans/2026-08-13-s-responsive-browser-evidence.md docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/debt/<actual-file>.md
```

Do not whole-file format `docs/slice-history.md`; inspect only the inserted block.

- [ ] **Step 6: Run authority, site-data, dependency, and diff gates**

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
./scripts/check-no-site-data.sh
npm --prefix apps/web audit --package-lock-only --audit-level=high
git diff --check 323fb179..HEAD
git diff --check
rg -n -U '<Table\.Tr[^>]*(onClick|onKeyDown|tabIndex|role=)' apps/web/src --glob '*.tsx'
```

Expected: authority/compatibility/site-data suites pass, direct authority returns `AUTHORITY_OK`, the web
lock has no gated vulnerability, diffs are clean, and the structural-row guard returns the expected
no-match status.

- [ ] **Step 7: Inspect scope, commit evidence, and rerun final guards**

```bash
git diff --stat 323fb179..HEAD
git diff --name-status 323fb179..HEAD
git status --short --branch
git log --oneline 323fb179..HEAD
```

Confirm no production auth, deployment, API, contract, migration, specialized-table, or unrelated change.
Then:

```bash
git add docs/current-status.md docs/slice-history.md docs/superpowers/plans/2026-08-13-s-responsive-browser-evidence.md
git commit -m "docs: record responsive browser evidence"
git diff --check 323fb179..HEAD
git status --short --branch
./scripts/check-repo-authority.sh
```

Expected: clean branch and `AUTHORITY_OK`.

- [ ] **Step 8: Hand off through review**

Report observable outcome, exact changed files/commits, browser and Vitest counts, CI topology, dependency
and compatibility decisions, review results, warnings, and every unverified layer. Use the
finishing-development-branch workflow to offer push/PR, local integration, or preservation. Never push
directly to `main` and never remove the worktree before a reviewed merge.
