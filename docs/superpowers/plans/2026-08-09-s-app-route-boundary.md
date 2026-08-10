# S-app-route-boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the operational application shell through routed-page render failures, provide a
router-independent last-resort recovery screen, and replace the silent wildcard redirect with a safe,
accessible 404 page.

**Architecture:** A shared class-based `ApplicationErrorBoundary` captures only synchronous React
descendant failures and exposes a reset callback. One instance wraps the application below Mantine and
above Query/Router/Auth; another wraps only `AppShell` content and resets on location changes. Unknown
operational routes render a fixed not-found mode inside `AppShell` without echoing the URL.

**Tech Stack:** React 19, TypeScript strict mode, React Router 7 declarative routes, TanStack Query,
Mantine, Vitest, Testing Library, MSW, jest-axe, Prettier, ESLint.

> **Owner-approved QueryClient clarification (2026-08-10):** The original implementation-plan base and
> Tasks 1–5 remain the historical slice record. For the bounded correction wave based on `67eac41`, Retry
> must preserve the original `QueryClientProvider`, exact `QueryClient` identity, and source-client
> mount/unmount lifecycle. Retry calls no invalidation, refetch, reset, removal, clearing, or equivalent
> cache operation. TanStack Query may perform its normal stale-query refetch when route observers remount.
> This clarification supersedes only the earlier zero-refetch/zero-network interpretation.

## Global Constraints

- Work only in `/tmp/EasySynQ-app-route-boundary` on `codex/app-route-boundary`, based on `ae84951`.
- Preserve the authentication and setup-state startup behavior shipped in PRs #453 and #454.
- Route retry remounts only the failed page subtree; it must not explicitly clear, invalidate, refetch,
  reset, or remove Query data, issue a mutation, or change provider/client identity or lifecycle. Normal
  stale-observer refetch-on-mount remains enabled.
- Never render a raw exception, stack, component name, response detail, status, pathname, query, hash,
  host/database detail, or arbitrary diagnostic identifier.
- Unknown operational URLs remain unchanged; pre-operational unknown URLs still authorize only setup.
- Route error, global error, and 404 actions are native buttons/links with visible names and at least
  `44px` height.
- At `320px` CSS width, text and controls wrap/stack without document-level horizontal scrolling.
- Existing theme tokens, reduced-motion, focus-ring, forced-colors, and light/dark behavior remain
  authoritative; add no palette literal, animation, external asset, telemetry, or dependency.
- Do not change API, OpenAPI/generated contracts, Keycloak, migrations, mutation feedback, URL filter
  state, table semantics, Playwright, deployment, environment examples, or open residuals.
- Every production behavior begins with a focused failing proof and ends with fresh focused and affected
  evidence. Do not turn a skipped, killed, partial, or rerun-only command into a pass claim.

---

## File structure and ownership

- `apps/web/src/app/errors/ApplicationErrorBoundary.tsx` — boolean capture/reset mechanism; accepts no
  raw error presentation prop.
- `apps/web/src/app/errors/ApplicationErrorBoundary.test.tsx` — capture, explicit reset, location reset,
  and no-raw-detail proofs.
- `apps/web/src/app/errors/ApplicationErrorScreen.tsx` — router-independent full-screen fallback.
- `apps/web/src/app/errors/ApplicationErrorScreen.test.tsx` — copy, focus, target size, reload, raw-detail,
  320px, and axe proofs.
- `apps/web/src/app/errors/RouteErrorPage.tsx` — shell-contained error recovery and temporary title owner.
- `apps/web/src/app/errors/RouteErrorPage.test.tsx` — retry/navigation/reload, focus/title, target size,
  raw-detail, 320px, and axe proofs.
- `apps/web/src/app/errors/NotFoundPage.tsx` — fixed operational 404 content and safe internal links.
- `apps/web/src/app/errors/NotFoundPage.test.tsx` — copy, navigation, focus, raw-path, target size, 320px,
  and axe proofs.
- `apps/web/src/main.tsx` — global boundary placement inside Mantine and outside Query/Router/Auth/App.
- `apps/web/src/app/shell/AppShell.tsx` — route boundary, full-location reset key, and explicit not-found
  content mode.
- `apps/web/src/app/shell/AppShell.test.tsx` — shell-preservation, remount, navigation reset, exact source
  client identity/lifecycle, no-explicit-cache-operation, cache-continuity, normal stale-refetch, and
  deterministic-rethrow proofs.
- `apps/web/src/app/shell/Breadcrumb.tsx` / `.test.tsx` — exact `Home / Page not found` override.
- `apps/web/src/lib/routeChrome.ts` / `.test.tsx` — exact route-pattern title matching and not-found title.
- `apps/web/src/App.tsx` / `.test.tsx` — wildcard routing and auth/setup/known-route falsifiers.
- `docs/current-status.md` and `docs/slice-history.md` — fresh shipped evidence only.

---

### Task 1: Add the shared render-error capture and reset primitive

**Files:**

- Create: `apps/web/src/app/errors/ApplicationErrorBoundary.tsx`
- Create: `apps/web/src/app/errors/ApplicationErrorBoundary.test.tsx`

**Interfaces:**

- Consumes: React `Component`, `Fragment`, and `ReactNode` only.
- Produces:

```ts
export interface ApplicationErrorFallbackProps {
  onReset: () => void;
}

export interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  fallback: (props: ApplicationErrorFallbackProps) => ReactNode;
  resetKey?: string;
}

export class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {}
```

- [ ] **Step 1: Write the failing capture and secrecy tests**

Create `ApplicationErrorBoundary.test.tsx` with a deterministic thrower and expected React console-noise
suppression scoped to each test:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ApplicationErrorBoundary } from "./ApplicationErrorBoundary";

afterEach(() => vi.restoreAllMocks());

function BrokenPage(): never {
  throw new Error("RAW_ERROR_DETAIL_SENTINEL RAW_PATH_DETAIL_SENTINEL");
}

test("captures descendant render failures without displaying the thrown value", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);

  render(
    <ApplicationErrorBoundary
      fallback={({ onReset }) => (
        <button onClick={onReset}>Recover safely</button>
      )}
    >
      <BrokenPage />
    </ApplicationErrorBoundary>,
  );

  expect(
    screen.getByRole("button", { name: "Recover safely" }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("RAW_ERROR_DETAIL_SENTINEL");
  expect(document.body).not.toHaveTextContent("RAW_PATH_DETAIL_SENTINEL");
});
```

- [ ] **Step 2: Write failing explicit-reset and reset-key tests**

Append tests that prove a transient child remounts and a changed location key clears an error:

```tsx
test("explicit reset remounts only the failed descendant subtree", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  let shouldThrow = true;
  let successfulMounts = 0;

  function TransientPage() {
    if (shouldThrow) throw new Error("transient unsafe detail");
    successfulMounts += 1;
    return <h1>Recovered page</h1>;
  }

  render(
    <ApplicationErrorBoundary
      fallback={({ onReset }) => (
        <button
          onClick={() => {
            shouldThrow = false;
            onReset();
          }}
        >
          Try this page again
        </button>
      )}
    >
      <TransientPage />
    </ApplicationErrorBoundary>,
  );

  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(
    screen.getByRole("heading", { name: "Recovered page" }),
  ).toBeInTheDocument();
  expect(successfulMounts).toBe(1);
});

test("a changed reset key clears a captured failure without a retry loop", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  let shouldThrow = true;

  function RoutePage() {
    if (shouldThrow) throw new Error("route failed");
    return <h1>New location</h1>;
  }

  const rendered = render(
    <ApplicationErrorBoundary
      resetKey="/broken"
      fallback={() => <p>Page unavailable</p>}
    >
      <RoutePage />
    </ApplicationErrorBoundary>,
  );
  expect(screen.getByText("Page unavailable")).toBeInTheDocument();

  shouldThrow = false;
  rendered.rerender(
    <ApplicationErrorBoundary
      resetKey="/library"
      fallback={() => <p>Page unavailable</p>}
    >
      <RoutePage />
    </ApplicationErrorBoundary>,
  );

  expect(
    screen.getByRole("heading", { name: "New location" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Page unavailable")).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorBoundary.test.tsx
```

Expected: FAIL because `./ApplicationErrorBoundary` does not exist.

- [ ] **Step 4: Implement the minimal boundary**

Create `ApplicationErrorBoundary.tsx`:

```tsx
import { Component, Fragment, type ReactNode } from "react";

export interface ApplicationErrorFallbackProps {
  onReset: () => void;
}

export interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  fallback: (props: ApplicationErrorFallbackProps) => ReactNode;
  resetKey?: string;
}

interface ApplicationErrorBoundaryState {
  failed: boolean;
  observedResetKey: string | undefined;
  retryEpoch: number;
}

export class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {
  state: ApplicationErrorBoundaryState = {
    failed: false,
    observedResetKey: this.props.resetKey,
    retryEpoch: 0,
  };

  static getDerivedStateFromError(): Pick<
    ApplicationErrorBoundaryState,
    "failed"
  > {
    return { failed: true };
  }

  static getDerivedStateFromProps(
    props: ApplicationErrorBoundaryProps,
    state: ApplicationErrorBoundaryState,
  ): Partial<ApplicationErrorBoundaryState> | null {
    if (props.resetKey === state.observedResetKey) return null;
    return {
      failed: false,
      observedResetKey: props.resetKey,
      retryEpoch: state.failed ? state.retryEpoch + 1 : state.retryEpoch,
    };
  }

  private readonly reset = (): void => {
    this.setState((state) => ({
      failed: false,
      observedResetKey: this.props.resetKey,
      retryEpoch: state.retryEpoch + 1,
    }));
  };

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback({ onReset: this.reset });
    return (
      <Fragment key={this.state.retryEpoch}>{this.props.children}</Fragment>
    );
  }
}
```

Do not add `componentDidCatch`, logging, telemetry, or an `error` field to state or props.

- [ ] **Step 5: Run focused static and behavior checks**

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorBoundary.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/app/errors/ApplicationErrorBoundary.tsx src/app/errors/ApplicationErrorBoundary.test.tsx
npm exec prettier -- --check src/app/errors/ApplicationErrorBoundary.tsx src/app/errors/ApplicationErrorBoundary.test.tsx
git diff --check
```

Run the Prettier command from `apps/web`. Expected: all commands pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add apps/web/src/app/errors/ApplicationErrorBoundary.tsx apps/web/src/app/errors/ApplicationErrorBoundary.test.tsx
git commit -m "feat: add application error boundary"
```

---

### Task 2: Add the router-independent global recovery screen

**Files:**

- Create: `apps/web/src/app/errors/ApplicationErrorScreen.tsx`
- Create: `apps/web/src/app/errors/ApplicationErrorScreen.test.tsx`
- Modify: `apps/web/src/main.tsx:1-28`

**Interfaces:**

- Consumes: `ApplicationErrorBoundary` from Task 1, Mantine theme/components, `window.location.reload`.
- Produces:

```ts
export interface ApplicationErrorScreenProps {
  onReload?: () => void;
}

export function ApplicationErrorScreen(
  props: ApplicationErrorScreenProps,
): JSX.Element;
```

- [ ] **Step 1: Write the failing global-screen tests**

Create `ApplicationErrorScreen.test.tsx` with a Mantine-only wrapper and no router:

```tsx
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import { ApplicationErrorBoundary } from "./ApplicationErrorBoundary";
import { ApplicationErrorScreen } from "./ApplicationErrorScreen";

afterEach(() => vi.restoreAllMocks());

function Tree({ children }: { children: ReactNode }) {
  return <MantineProvider theme={theme}>{children}</MantineProvider>;
}

function BrokenApplication(): never {
  throw new Error("RAW_GLOBAL_ERROR_SENTINEL RAW_GLOBAL_PATH_SENTINEL");
}

test("renders safe full-screen recovery without router context or raw details", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const onReload = vi.fn();
  const { container } = render(
    <ApplicationErrorBoundary
      fallback={() => <ApplicationErrorScreen onReload={onReload} />}
    >
      <BrokenApplication />
    </ApplicationErrorBoundary>,
    { wrapper: Tree },
  );

  const heading = screen.getByRole("heading", {
    name: "EasySynQ couldn't be displayed",
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(screen.getByRole("button", { name: "Reload EasySynQ" })).toHaveStyle({
    minHeight: "44px",
  });
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
    "href",
    "/",
  );
  expect(container).not.toHaveTextContent("RAW_GLOBAL_ERROR_SENTINEL");
  expect(container).not.toHaveTextContent("RAW_GLOBAL_PATH_SENTINEL");
  expect(await axe(container)).toHaveNoViolations();
});

test("reload invokes exactly the injected browser seam", async () => {
  const user = userEvent.setup();
  const onReload = vi.fn();
  render(<ApplicationErrorScreen onReload={onReload} />, { wrapper: Tree });

  await user.click(screen.getByRole("button", { name: "Reload EasySynQ" }));
  expect(onReload).toHaveBeenCalledTimes(1);
});

test("uses bounded narrow-screen geometry", () => {
  render(<ApplicationErrorScreen onReload={() => undefined} />, {
    wrapper: Tree,
  });

  const main = screen.getByRole("main");
  expect(main.parentElement).toHaveStyle({
    paddingInline: "var(--mantine-spacing-lg)",
  });
  expect(main).toHaveStyle({ minWidth: "0rem", width: "100%" });
});
```

- [ ] **Step 2: Run the screen tests and verify RED**

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorScreen.test.tsx
```

Expected: FAIL because `ApplicationErrorScreen` does not exist.

- [ ] **Step 3: Implement the full-screen recovery component**

Create `ApplicationErrorScreen.tsx`:

```tsx
import {
  Button,
  Center,
  Image,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useRef } from "react";

export interface ApplicationErrorScreenProps {
  onReload?: () => void;
}

export function ApplicationErrorScreen({
  onReload = () => window.location.reload(),
}: ApplicationErrorScreenProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    document.title = "EasySynQ — Unavailable";
    headingRef.current?.focus();
  }, []);

  return (
    <Center mih="100dvh" px="lg" py="lg" bg="var(--es-bg)">
      <Paper
        component="main"
        w="100%"
        maw={440}
        miw={0}
        p={{ base: "xl", sm: 48 }}
        radius="md"
        withBorder
        shadow="xs"
        bg="var(--es-surface)"
        aria-live="assertive"
      >
        <Stack align="stretch" gap="xl">
          <Image
            src="/easysynq-mark.svg"
            alt="EasySynQ"
            w={64}
            h={64}
            fit="contain"
            mx="auto"
          />
          <Stack align="center" gap="sm">
            <Title
              ref={headingRef}
              order={1}
              size="h2"
              ta="center"
              tabIndex={-1}
            >
              EasySynQ couldn't be displayed
            </Title>
            <Text c="var(--es-text-2)" ta="center">
              Reload EasySynQ to start again. If the problem continues, contact
              your EasySynQ administrator.
            </Text>
          </Stack>
          <Stack gap="xs">
            <Button
              fullWidth
              color="indigo"
              style={{ minHeight: 44 }}
              onClick={onReload}
            >
              Reload EasySynQ
            </Button>
            <Button
              component="a"
              href="/"
              fullWidth
              variant="subtle"
              color="gray"
              style={{ minHeight: 44 }}
            >
              Go to dashboard
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Center>
  );
}
```

- [ ] **Step 4: Place the global boundary at the approved root seam**

In `main.tsx`, import both error components and replace only the provider nesting inside Mantine:

```tsx
import { ApplicationErrorBoundary } from "./app/errors/ApplicationErrorBoundary";
import { ApplicationErrorScreen } from "./app/errors/ApplicationErrorScreen";
```

```tsx
<MantineProvider theme={theme} defaultColorScheme="auto">
  <ApplicationErrorBoundary fallback={() => <ApplicationErrorScreen />}>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </ApplicationErrorBoundary>
</MantineProvider>
```

Keep `React.StrictMode`, the root lookup, and provider construction unchanged.

- [ ] **Step 5: Run focused and startup-adjacent checks**

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/ApplicationErrorScreen.test.tsx src/app/startup/AuthStartupScreen.test.tsx src/app/startup/SetupStartupScreen.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/main.tsx src/app/errors/ApplicationErrorBoundary.tsx src/app/errors/ApplicationErrorScreen.tsx src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/ApplicationErrorScreen.test.tsx
npm exec prettier -- --check src/main.tsx src/app/errors/ApplicationErrorBoundary.tsx src/app/errors/ApplicationErrorScreen.tsx src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/ApplicationErrorScreen.test.tsx
git diff --check
```

Run Prettier from `apps/web`. Expected: all commands pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add apps/web/src/main.tsx apps/web/src/app/errors/ApplicationErrorScreen.tsx apps/web/src/app/errors/ApplicationErrorScreen.test.tsx
git commit -m "feat: add global application recovery"
```

---

### Task 3: Preserve the shell through routed-page render failures

**Files:**

- Create: `apps/web/src/app/errors/RouteErrorPage.tsx`
- Create: `apps/web/src/app/errors/RouteErrorPage.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx:1-58`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx:1-39`

**Interfaces:**

- Consumes: `ApplicationErrorBoundary`, React Router `Link`, `Outlet`, `useLocation`, existing AppShell.
- Produces:

```ts
export interface RouteErrorPageProps {
  onRetry: () => void;
  onReload?: () => void;
}

export function RouteErrorPage(props: RouteErrorPageProps): JSX.Element;
```

- `AppShell` derives `resetKey` as `${pathname}${search}${hash}` and passes `onReset` only to
  `RouteErrorPage`.

- [ ] **Step 1: Write failing route-error presentation tests**

Create `RouteErrorPage.test.tsx`:

```tsx
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import { RouteErrorPage } from "./RouteErrorPage";

afterEach(() => vi.restoreAllMocks());

function Tree({ children }: { children: ReactNode }) {
  return (
    <MantineProvider theme={theme}>
      <MemoryRouter>{children}</MemoryRouter>
    </MantineProvider>
  );
}

test("renders fixed safe recovery, focuses the heading, and restores the prior title", async () => {
  document.title = "EasySynQ — Library";
  const rendered = render(
    <RouteErrorPage onRetry={() => undefined} onReload={() => undefined} />,
    { wrapper: Tree },
  );

  const heading = screen.getByRole("heading", {
    name: "This page couldn't be displayed",
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Page unavailable");
  expect(document.body).not.toHaveTextContent("RAW_ROUTE_ERROR_SENTINEL");
  expect(
    screen.getByRole("button", { name: "Try this page again" }),
  ).toHaveStyle({
    minHeight: "44px",
  });
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
    "href",
    "/",
  );

  rendered.unmount();
  expect(document.title).toBe("EasySynQ — Library");
});

test("retry and reload invoke only their explicit callbacks", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  const onReload = vi.fn();
  render(<RouteErrorPage onRetry={onRetry} onReload={onReload} />, {
    wrapper: Tree,
  });

  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  await user.click(screen.getByRole("button", { name: "Reload EasySynQ" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(onReload).toHaveBeenCalledTimes(1);
});

test("has bounded geometry and no axe violations", async () => {
  const { container } = render(
    <RouteErrorPage onRetry={() => undefined} onReload={() => undefined} />,
    { wrapper: Tree },
  );
  expect(screen.getByRole("region")).toHaveStyle({
    minWidth: "0rem",
    width: "100%",
  });
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Write failing AppShell boundary integration tests**

Extend `AppShell.test.tsx`. Import `QueryClient`, `vi`, and `afterEach`; restore spies after each test.
Add transient and deterministic route fixtures:

```tsx
test("a routed-page render failure preserves shell chrome and retry remounts only content", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  let shouldThrow = true;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["preserved-route-data"], { value: "still here" });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");

  function TransientRoute() {
    if (shouldThrow) throw new Error("RAW_ROUTE_ERROR_SENTINEL");
    return <h1>Recovered route</h1>;
  }

  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route path="broken" element={<TransientRoute />} />
      </Route>
    </Routes>,
    { route: "/broken", queryClient },
  );

  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByLabelText("Breadcrumb")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /skip to content/i }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("RAW_ROUTE_ERROR_SENTINEL");

  shouldThrow = false;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(
    screen.getByRole("heading", { name: "Recovered route" }),
  ).toBeInTheDocument();
  expect(queryClient.getQueryData(["preserved-route-data"])).toEqual({
    value: "still here",
  });
  expect(invalidate).not.toHaveBeenCalled();
});

test("dashboard navigation clears a deterministic route failure", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();

  function BrokenRoute(): never {
    throw new Error("always broken");
  }

  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<h1>Safe dashboard</h1>} />
        <Route path="broken" element={<BrokenRoute />} />
      </Route>
    </Routes>,
    { route: "/broken" },
  );

  await user.click(screen.getByRole("link", { name: "Go to dashboard" }));
  expect(
    screen.getByRole("heading", { name: "Safe dashboard" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByText("This page couldn't be displayed"),
  ).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run the focused tests and verify RED**

```bash
npm --prefix apps/web run test -- src/app/errors/RouteErrorPage.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: FAIL because `RouteErrorPage` is missing and AppShell does not catch route content.

- [ ] **Step 4: Implement `RouteErrorPage`**

Create the component with fixed copy, title restoration, and injected reload:

```tsx
import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

export interface RouteErrorPageProps {
  onRetry: () => void;
  onReload?: () => void;
}

export function RouteErrorPage({
  onRetry,
  onReload = () => window.location.reload(),
}: RouteErrorPageProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "EasySynQ — Page unavailable";
    headingRef.current?.focus();
    return () => {
      document.title = previousTitle;
    };
  }, []);

  return (
    <Paper
      component="section"
      role="region"
      aria-live="assertive"
      aria-labelledby="route-error-heading"
      w="100%"
      maw={560}
      miw={0}
      mx="auto"
      mt="xl"
      p={{ base: "xl", sm: 40 }}
      radius="md"
      withBorder
      bg="var(--es-surface)"
    >
      <Stack gap="lg">
        <Stack gap="sm">
          <Title
            id="route-error-heading"
            ref={headingRef}
            order={1}
            size="h2"
            tabIndex={-1}
          >
            This page couldn't be displayed
          </Title>
          <Text c="var(--es-text-2)">
            EasySynQ encountered a problem while displaying this page. Your
            shared application data has not been cleared.
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button color="indigo" style={{ minHeight: 44 }} onClick={onRetry}>
            Try this page again
          </Button>
          <Button
            component={Link}
            to="/"
            variant="light"
            color="indigo"
            style={{ minHeight: 44 }}
          >
            Go to dashboard
          </Button>
          <Button
            variant="subtle"
            color="gray"
            style={{ minHeight: 44 }}
            onClick={onReload}
          >
            Reload EasySynQ
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
```

- [ ] **Step 5: Wrap only AppShell content**

In `AppShell.tsx`, import `useLocation`, `ApplicationErrorBoundary`, and `RouteErrorPage`. Derive the full
location reset key and replace only the direct `<Outlet />`:

```tsx
const { pathname, search, hash } = useLocation();
const routeResetKey = `${pathname}${search}${hash}`;
```

```tsx
<ApplicationErrorBoundary
  resetKey={routeResetKey}
  fallback={({ onReset }) => <RouteErrorPage onRetry={onReset} />}
>
  <Outlet />
</ApplicationErrorBoundary>
```

Keep `Breadcrumb` outside the route boundary. Keep header, navbar, skip link, and command palette
unchanged.

- [ ] **Step 6: Run focused and neighboring checks**

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/RouteErrorPage.test.tsx src/app/shell/AppShell.test.tsx src/app/shell/Breadcrumb.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/app/errors/RouteErrorPage.tsx src/app/errors/RouteErrorPage.test.tsx src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx
npm exec prettier -- --check src/app/errors/RouteErrorPage.tsx src/app/errors/RouteErrorPage.test.tsx src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx
git diff --check
```

Run Prettier from `apps/web`. Expected: all commands pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add apps/web/src/app/errors/RouteErrorPage.tsx apps/web/src/app/errors/RouteErrorPage.test.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/app/shell/AppShell.test.tsx
git commit -m "feat: recover routed page failures"
```

---

### Task 4: Replace the wildcard redirect with a safe shell-contained 404

**Files:**

- Create: `apps/web/src/app/errors/NotFoundPage.tsx`
- Create: `apps/web/src/app/errors/NotFoundPage.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx:1-70`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`
- Modify: `apps/web/src/app/shell/Breadcrumb.tsx:1-108`
- Modify: `apps/web/src/app/shell/Breadcrumb.test.tsx`
- Modify: `apps/web/src/lib/routeChrome.ts:1-56`
- Modify: `apps/web/src/lib/routeChrome.test.tsx`
- Modify: `apps/web/src/App.tsx:187-251`
- Modify: `apps/web/src/App.test.tsx`

**Interfaces:**

- Consumes: Task 3 AppShell route boundary, Router `Link`, current App setup/auth guards.
- Produces:

```ts
export function NotFoundPage(): JSX.Element;
export interface AppShellProps {
  notFound?: boolean;
}
export interface BreadcrumbProps {
  notFound?: boolean;
}
```

- `useRouteChrome` uses exact React Router path patterns rather than prefix inference so an unmatched
  child such as `/library/not-a-route` receives `Page not found`, not `Library`.

- [ ] **Step 1: Write failing `NotFoundPage` tests**

Create `NotFoundPage.test.tsx`:

```tsx
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { NotFoundPage } from "./NotFoundPage";

function LocationProbe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function Tree({ children }: { children: ReactNode }) {
  return (
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/missing/private-segment"]}>
        {children}
      </MemoryRouter>
    </MantineProvider>
  );
}

test("renders fixed safe 404 copy, focus, targets, and no raw pathname", async () => {
  const { container } = render(<NotFoundPage />, { wrapper: Tree });
  const heading = screen.getByRole("heading", { name: "Page not found" });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(
    screen.getByText("The page you requested isn't available in EasySynQ."),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
    "href",
    "/",
  );
  expect(
    screen.getByRole("link", { name: "Open document library" }),
  ).toHaveAttribute("href", "/library");
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveStyle({
    minHeight: "44px",
  });
  expect(container).not.toHaveTextContent("private-segment");
  expect(await axe(container)).toHaveNoViolations();
});

test("safe links navigate to exact internal destinations", async () => {
  const user = userEvent.setup();
  render(
    <>
      <NotFoundPage />
      <LocationProbe />
    </>,
    { wrapper: Tree },
  );
  await user.click(screen.getByRole("link", { name: "Open document library" }));
  expect(screen.getByLabelText("location")).toHaveTextContent("/library");
});
```

- [ ] **Step 2: Write failing breadcrumb and route-title tests**

Add to `Breadcrumb.test.tsx`:

```tsx
test("not-found mode renders only the fixed safe breadcrumb", () => {
  const client = new QueryClient();
  renderCrumb(client, "/missing/private-segment", true);
  const breadcrumb = screen.getByLabelText("Breadcrumb");
  expect(
    within(breadcrumb).getByRole("link", { name: "Home" }),
  ).toHaveAttribute("href", "/");
  expect(within(breadcrumb).getByText("Page not found")).toBeInTheDocument();
  expect(
    within(breadcrumb).queryByText(/private-segment/i),
  ).not.toBeInTheDocument();
});
```

Update `renderCrumb` to accept `notFound = false` and render `<Breadcrumb notFound={notFound} />`.

Replace the current unmapped-title expectation in `routeChrome.test.tsx` and add a known-prefix unknown
case:

```tsx
it.each(["/totally-unknown", "/library/not-a-real-route"])(
  "uses the not-found title for unmatched route %s",
  (route) => {
    render(
      <MemoryRouter initialEntries={[route]}>
        <Harness />
      </MemoryRouter>,
    );
    expect(document.title).toBe("EasySynQ — Page not found");
  },
);
```

- [ ] **Step 3: Write failing App routing tests**

In `App.test.tsx`, reuse the existing hoisted `LocationProbe` declaration near the legacy-ingestion tests
and add:

```tsx
test("an unknown operational URL remains visible and renders a safe shell-contained 404", async () => {
  renderWithProviders(
    <>
      <App />
      <LocationProbe />
    </>,
    { route: "/missing/private-segment?view=private-segment" },
  );

  expect(
    await screen.findByRole("heading", { name: "Page not found" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/missing/private-segment?view=private-segment",
  );
  expect(screen.getByRole("main")).not.toHaveTextContent("private-segment");
  expect(screen.getByRole("main")).not.toHaveTextContent(
    "view=private-segment",
  );
  expect(document.title).toBe("EasySynQ — Page not found");
});

test.each(["UNINITIALIZED", "IN_SETUP"] as const)(
  "unknown %s routes retain the setup authorization boundary",
  async (setup_state) => {
    server.use(
      http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state })),
    );
    renderWithProviders(<App />, {
      route: "/missing/private-segment",
      auth: noTokenAuth(),
    });
    expect(
      await screen.findByRole("heading", { name: "Welcome to EasySynQ" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Page not found" }),
    ).not.toBeInTheDocument();
  },
);

test("404 recovery links reach dashboard and library without a browser-back escape", async () => {
  const user = userEvent.setup();
  renderWithProviders(<App />, { route: "/missing" });
  await user.click(
    await screen.findByRole("link", { name: "Open document library" }),
  );
  expect(await screen.findByText("Document Library")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /back/i })).not.toBeInTheDocument();
});
```

- [ ] **Step 4: Run the focused tests and verify RED**

```bash
npm --prefix apps/web run test -- src/app/errors/NotFoundPage.test.tsx src/app/shell/Breadcrumb.test.tsx src/lib/routeChrome.test.tsx src/App.test.tsx
```

Expected: FAIL because `NotFoundPage`, not-found props, exact route matching, and wildcard behavior do not
exist.

- [ ] **Step 5: Implement `NotFoundPage`**

Create `NotFoundPage.tsx`:

```tsx
import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

export function NotFoundPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headingRef.current?.focus(), []);

  return (
    <Paper
      component="section"
      aria-labelledby="not-found-heading"
      w="100%"
      maw={560}
      miw={0}
      mx="auto"
      mt="xl"
      p={{ base: "xl", sm: 40 }}
      radius="md"
      withBorder
      bg="var(--es-surface)"
    >
      <Stack gap="lg">
        <Stack gap="sm">
          <Title
            id="not-found-heading"
            ref={headingRef}
            order={1}
            size="h2"
            tabIndex={-1}
          >
            Page not found
          </Title>
          <Text c="var(--es-text-2)">
            The page you requested isn't available in EasySynQ.
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button
            component={Link}
            to="/"
            color="indigo"
            style={{ minHeight: 44 }}
          >
            Go to dashboard
          </Button>
          <Button
            component={Link}
            to="/library"
            variant="light"
            color="indigo"
            style={{ minHeight: 44 }}
          >
            Open document library
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
```

- [ ] **Step 6: Add fixed not-found shell and breadcrumb modes**

Change `Breadcrumb` to accept `BreadcrumbProps`. Keep its hook order unconditional: derive `docId` as
`null` in not-found mode, call the existing disabled `useQuery`, and only then return the fixed crumbs.
This allows React Router to reuse the same `Breadcrumb` instance safely across known and unknown routes:

```tsx
export interface BreadcrumbProps {
  notFound?: boolean;
}

export function Breadcrumb({ notFound = false }: BreadcrumbProps) {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);
  const docIdx = segments.indexOf("documents");
  const docId = !notFound && docIdx >= 0 && docIdx + 1 < segments.length
    ? segments[docIdx + 1]
    : null;
  const { data: doc } = useQuery<DocumentSummary>({
    queryKey: ["document", docId],
    queryFn: () => Promise.reject(new Error("breadcrumb does not fetch")),
    enabled: false,
  });

  if (notFound) {
    return (
      <Breadcrumbs aria-label="Breadcrumb">
        <Anchor component={Link} to="/">
          Home
        </Anchor>
        <Text c="dimmed">Page not found</Text>
      </Breadcrumbs>
    );
  }
```

Remove the old duplicate `docIdx` / `docId` / `useQuery` block and keep the existing normal crumb mapping
below that branch.

Change `AppShell` to accept `notFound`, pass it to `Breadcrumb`, and select fixed content inside the
existing route boundary:

```tsx
export interface AppShellProps {
  notFound?: boolean;
}

export function AppShell({ notFound = false }: AppShellProps) {
```

```tsx
<Breadcrumb notFound={notFound} />
<ApplicationErrorBoundary
  resetKey={routeResetKey}
  fallback={({ onReset }) => <RouteErrorPage onRetry={onReset} />}
>
  {notFound ? <NotFoundPage /> : <Outlet />}
</ApplicationErrorBoundary>
```

- [ ] **Step 7: Make route titles exact and deterministic**

In `routeChrome.ts`, import `matchPath`. Replace prefix matching with exact patterns covering every
mounted route. Order dynamic patterns after their more specific siblings:

```tsx
import { matchPath, useLocation } from "react-router-dom";

const TITLES: readonly (readonly [string, string])[] = [
  ["/", "Dashboard"],
  ["/setup", "Setup"],
  ["/admin", "Administration"],
  ["/admin/users", "Administration"],
  ["/admin/roles", "Administration"],
  ["/admin/processes", "Administration"],
  ["/admin/config", "Administration"],
  ["/library", "Library"],
  ["/library/new", "New document"],
  ["/documents/:id", "Document"],
  ["/tasks", "Tasks"],
  ["/tasks/:id", "Task"],
  ["/notifications", "Notifications"],
  ["/settings/notifications", "Notification settings"],
  ["/search", "Search"],
  ["/compliance", "Compliance"],
  ["/reports/document-control", "Document register"],
  ["/capa", "CAPA"],
  ["/capa/complaints", "Complaints"],
  ["/capa/ncrs", "NCRs"],
  ["/audits", "Audits"],
  ["/audits/programme", "Audit programme"],
  ["/audits/:id", "Audit"],
  ["/imports", "Import"],
  ["/imports/:runId", "Import run"],
  ["/ingestion", "Import"],
  ["/ingestion/:runId", "Import run"],
  ["/drift", "Drift"],
  ["/drift/superseded-copies", "Superseded copies"],
  ["/objectives", "Objectives"],
  ["/objectives/:id", "Objective"],
  ["/management-reviews", "Management reviews"],
  ["/management-reviews/:id", "Management review"],
  ["/dcrs", "Document change requests"],
  ["/dcrs/:id/diff", "Document change request"],
  ["/improvement", "Improvement"],
  ["/risks", "Risks"],
  ["/context", "Context"],
  ["/interested-parties", "Interested parties"],
];

function labelFor(pathname: string): string {
  for (const [pattern, label] of TITLES) {
    if (matchPath({ path: pattern, end: true }, pathname)) return label;
  }
  return "Page not found";
}
```

Set `document.title` unconditionally from the returned label. Preserve the existing pathname-only focus
behavior.

- [ ] **Step 8: Replace only the wildcard route**

In `App.tsx`, replace:

```tsx
<Route path="*" element={<Navigate to="/" replace />} />
```

with:

```tsx
<Route
  path="*"
  element={
    operational ? <AppShell notFound /> : <Navigate to="/setup" replace />
  }
/>
```

Do not move the auth/setup/finalization checks or alter any named route.

- [ ] **Step 9: Run focused and complete affected tests**

```bash
npm --prefix apps/web run test -- src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/ApplicationErrorScreen.test.tsx src/app/errors/RouteErrorPage.test.tsx src/app/errors/NotFoundPage.test.tsx src/app/shell/AppShell.test.tsx src/app/shell/Breadcrumb.test.tsx src/lib/routeChrome.test.tsx src/App.test.tsx src/app/startup/AuthStartupScreen.test.tsx src/app/startup/SetupStartupScreen.test.tsx src/SetupWizard.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/App.tsx src/App.test.tsx src/app/errors src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx src/app/shell/Breadcrumb.tsx src/app/shell/Breadcrumb.test.tsx src/lib/routeChrome.ts src/lib/routeChrome.test.tsx
npm exec prettier -- --check src/App.tsx src/App.test.tsx src/app/errors/ApplicationErrorBoundary.tsx src/app/errors/ApplicationErrorBoundary.test.tsx src/app/errors/ApplicationErrorScreen.tsx src/app/errors/ApplicationErrorScreen.test.tsx src/app/errors/RouteErrorPage.tsx src/app/errors/RouteErrorPage.test.tsx src/app/errors/NotFoundPage.tsx src/app/errors/NotFoundPage.test.tsx src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx src/app/shell/Breadcrumb.tsx src/app/shell/Breadcrumb.test.tsx src/lib/routeChrome.ts src/lib/routeChrome.test.tsx src/main.tsx
git diff --check
```

Run Prettier from `apps/web`. Expected: all commands pass.

- [ ] **Step 10: Commit Task 4**

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/app/errors/NotFoundPage.tsx apps/web/src/app/errors/NotFoundPage.test.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/app/shell/AppShell.test.tsx apps/web/src/app/shell/Breadcrumb.tsx apps/web/src/app/shell/Breadcrumb.test.tsx apps/web/src/lib/routeChrome.ts apps/web/src/lib/routeChrome.test.tsx
git commit -m "feat: add safe not found routing"
```

---

### Task 5: Run whole-slice verification and record fresh evidence

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`

**Interfaces:**

- Consumes: all reviewed green implementation commits and exact command output.
- Produces: shipped `S-app-route-boundary` snapshot and durable historical evidence.

- [ ] **Step 1: Format touched web files and run the complete affected gates**

Run scoped Prettier write/check from `apps/web` on every web file touched by Tasks 1–4. Keep any
formatter-only changes in a separate implementation checkpoint before collecting the baseline evidence.
Then run:

```bash
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
bash scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
bash scripts/check-no-site-data.sh
git diff --check
git status --short
```

Record exact test counts, command results, implementation commit, date, build module count, and warnings.
Do not convert a timeout, killed process, skip, rerun, partial suite, or unavailable check into a pass.
Use the durable process-job workflow if the full Vitest command exceeds the interactive execution window.

- [ ] **Step 2: Update current authority from fresh evidence only**

In `docs/current-status.md`:

- set `last_shipped_slice` to `S-app-route-boundary`;
- set `baseline_commit` to the implementation/format checkpoint where the complete web evidence ran;
- update only freshly verified web file/test counts; and
- describe shell-preserving route recovery, the global last-resort boundary, and visible 404 behavior.

Preserve migration `0085` / next `0086`, API/contract/integration counts, CI topology, Vite advisory,
pending Fedora proof, PostgreSQL MCP disablement, and current residuals unless fresh in-scope evidence
changes one.

In `docs/slice-history.md`, record:

- the two-tier boundary placement and exact failure ownership;
- route retry remount-only/no-cache-invalidation/no-mutation behavior;
- global router-independent recovery;
- visible operational 404, preserved URL, fixed breadcrumb/title, and safe links;
- auth/setup/known-route/legacy-redirect regression results;
- accessibility evidence; and
- exact commands, counts, commit, date, warnings, and limitations.

- [ ] **Step 3: Re-run documentation and repository guards**

Run the new spec/plan/current-status documents through the app-local Prettier executable. Check
`slice-history.md` separately and report its known whole-file failure honestly if the base still fails
identically; do not mass-format historical content.

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
bash scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
bash scripts/check-no-site-data.sh
git diff --check
git status --short
```

- [ ] **Step 4: Commit the evidence checkpoint**

```bash
git add docs/current-status.md docs/slice-history.md
git commit -m "docs: record application route boundary"
```

- [ ] **Step 5: Perform an independent whole-branch review**

Review `ae84951..HEAD` against
`docs/superpowers/specs/2026-08-09-s-app-route-boundary-design.md`. Inspect boundary placement, reset
state, fallback-failure promotion, query-cache and mutation falsifiers, location-key behavior, title
restore, 404 URL preservation, raw-path exclusion through breadcrumbs, auth/setup precedence, exact route
title coverage, accessibility, full verification, documentation truth, and scope.

Address every Critical or Important finding with a new failing test, minimal fix, focused green proof,
and a separate correction commit. After any correction, repeat Step 1 and refresh recorded evidence if
counts or the implementation baseline changed.

- [ ] **Step 6: Confirm final branch state for owner selection**

```bash
git log --oneline --decorate ae84951..HEAD
git diff --stat ae84951..HEAD
git diff --check ae84951..HEAD
git status --short --branch
```

Expected: scoped commits only and a clean `codex/app-route-boundary`. Do not push or open a draft PR until
the owner selects publication.

---

### Task 6: Apply the owner-approved QueryClient provider clarification

**Clarification base:** `67eac41`

**Files:**

- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`
- Delete: `apps/web/src/app/errors/RouteRetryQueryClient.ts`
- Modify: this plan, the approved design, `docs/current-status.md`, and `docs/slice-history.md`

- [ ] **Step 1: Capture focused RED against the retry proxy**

Prove in one integration regression that every route-side `useQueryClient()` read is the exact source
client, the source client receives only its root-provider mount/unmount lifecycle, repeated failure and
successful Retry remount only route content, cached data remains continuous, and Retry invokes none of
`invalidateQueries`, `refetchQueries`, `resetQueries`, `removeQueries`, `cancelQueries`, or `clear`.

In an independent regression, seed stale cached data under `refetchOnMount: "always"`, recover the route,
hold the response so the cached value remains observable, and require exactly one normal observer-driven
request. Against the superseded proxy implementation, the first proof must expose extra provider mounts,
client identities, and lifecycle churn; the second must expose the suppressed request.

- [ ] **Step 2: Remove provider/client switching**

Render `ApplicationErrorBoundary` directly under the existing `AppShell` content seam and pass `onReset`
directly to `RouteErrorPage`. Remove all retry QueryClient state/effects/imports and delete
`RouteRetryQueryClient.ts` explicitly. Do not add a replacement provider, default-options mutation,
timer, cache operation, or global side effect.

- [ ] **Step 3: Prove focused and affected GREEN**

Run each new falsifier independently, the complete `AppShell.test.tsx`, and the 12-file affected
route/startup/test-harness selection from Task 5. Then run web typecheck and lint, the production build,
and scoped Prettier checks for every touched web file.

- [ ] **Step 4: Reconcile authority without replacing complete-suite evidence**

Record the 2026-08-10 owner approval and current provider contract in the design, plan, current snapshot,
and slice history. Preserve `6f5676e`, 257 files, and 1,596 tests as the latest complete web evidence and
label it pre-clarification. Record correction-wave evidence separately; do not imply that a focused or
affected selection was the complete suite.

- [ ] **Step 5: Run correction-wave repository guards and hand off cleanly**

Run the repository-authority fixture suite, Claude-hook compatibility checks, repository authority, both
site-data guards, documentation/scoped formatting checks, build, `git diff --check`, and final branch
scope/status inspection. The historical whole `slice-history.md` Prettier baseline may remain failing if
it matches the known base limitation; do not mass-format it. Do not launch the complete web suite, push,
or update evidence as though the complete suite reran.
