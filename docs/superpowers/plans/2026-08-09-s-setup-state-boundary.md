# S-setup-state-boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public setup-state probe a bounded, runtime-validated, fail-closed routing boundary that can recover by rereading state without exposing or replaying setup after an untrusted result.

**Architecture:** A dedicated `setupState.ts` module owns the three-value response contract and exact 15-second request deadline. `App` orders the shipped auth boundary ahead of a separate `SetupStartupScreen`, routes only from validated setup state, and records successful finalization as a verification-only phase in which recovery can issue state GETs but cannot remount the wizard or repeat the mutation.

**Tech Stack:** React 19, TypeScript 6, React Router 7, Mantine 7, TanStack Query 5, Vitest 4, Testing Library, MSW 2, jest-axe 11. Design authority: `docs/superpowers/specs/2026-08-09-s-setup-state-boundary-design.md`.

## Global Constraints

- The setup-state deadline is exactly 15,000 ms. Timeout tests use controlled promises and fake timers; never wait 15 real seconds.
- Accept exactly `UNINITIALIZED`, `IN_SETUP`, and `OPERATIONAL`. Never coerce, default, or infer `UNINITIALIZED`.
- Configure the root query with `retry: false`, `staleTime: Infinity`, `refetchOnWindowFocus: false`, `refetchOnReconnect: false`, and no interval polling.
- Auth startup and recovery always render before the setup boundary. Do not modify `AuthProvider`, `AuthStartupScreen`, the redirect latch, callback behavior, or memory-only tokens.
- Query failure always outranks cached data. Pending, failed, malformed, unknown, and post-finalization contradictory state authorize neither wizard, shell, nor automatic sign-in.
- The setup-state UI receives only `{ kind, phase }`; raw exceptions, response bodies, problem details/codes, statuses, URLs, malformed values, and stack traces must never render.
- Copy, 44 px targets, focus behavior, live regions, 24 px narrow padding, 320 CSS px layout, reduced motion, and forced-colors behavior must match the approved design verbatim.
- A successful finalize POST enters a verification-only phase before refetch. No recovery action may call `/api/v1/setup/finalize` or remount `SetupWizard` during that phase.
- A failed finalize POST retains existing wizard mutation behavior; broader ambiguous-mutation handling is out of scope.
- Do not change API, OpenAPI, generated contracts, Keycloak, migrations, setup-detail semantics, general query primitives, route-404 behavior, mutation feedback, URL state, Playwright, or unrelated residuals.
- Preserve the user-owned `.superdesign/` directory in the primary checkout and both pre-existing `/tmp` worktrees. Work only in `/tmp/EasySynQ-setup-state-boundary`.
- Every behavior task follows RED→GREEN, runs focused tests before commit, and receives a fresh review before the next task.
- Use scoped Prettier only on touched files; do not mass-format the repository.
- Run `git diff --check` and inspect `git status --short` after each task.

---

## File and ownership map

| File                                                   | Responsibility                                                                                                 |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `apps/web/src/app/startup/setupState.ts`               | Closed setup-state type, parser, request cancellation, exact deadline, safe rejection                          |
| `apps/web/src/app/startup/setupState.test.ts`          | Parser table, HTTP/network/JSON/schema cases, timeout, late result, cancellation, timer cleanup                |
| `apps/web/src/app/startup/SetupStartupScreen.tsx`      | Pure setup loading/recovery panel, safe copy, focus, single-flight retry, reload                               |
| `apps/web/src/app/startup/SetupStartupScreen.test.tsx` | Copy, interface, focus, busy/retry, reload, target size, narrow layout, axe                                    |
| `apps/web/src/App.tsx`                                 | Query policy, boundary precedence, strict routing, explicit retry, post-finalization verification              |
| `apps/web/src/App.test.tsx`                            | Failure/malformed/success routing, no-mutation proof, retry counts, auth regressions, finalization integration |
| `apps/web/src/SetupWizard.tsx`                         | Promise-returning finalization callback awaited only after finalize succeeds                                   |
| `apps/web/src/SetupWizard.test.tsx`                    | Callback ordering and failed-finalize non-callback proof                                                       |
| `docs/current-status.md`                               | Fresh post-implementation execution snapshot only                                                              |
| `docs/slice-history.md`                                | Durable shipped-slice narrative and exact evidence                                                             |

Production interfaces settle on these names:

```ts
export const SETUP_STATE_TIMEOUT_MS = 15_000;

export type SetupState = "UNINITIALIZED" | "IN_SETUP" | "OPERATIONAL";

export interface SetupStateResponse {
  setup_state: SetupState;
}

export function parseSetupState(value: unknown): SetupStateResponse;
export function fetchSetupState(
  signal?: AbortSignal,
): Promise<SetupStateResponse>;

export type SetupStartupPhase = "initial" | "post-finalization";

export type SetupStartupStatus =
  | { kind: "loading"; phase: SetupStartupPhase }
  | { kind: "error"; phase: SetupStartupPhase };

export interface SetupStartupScreenProps {
  status: SetupStartupStatus;
  onRetry: () => Promise<void>;
  onReload: () => void;
}
```

`SetupWizard` changes only this callback type:

```ts
onFinalized: () => Promise<void>;
```

---

### Task 1: Build the closed, bounded setup-state request boundary

**Files:**

- Create: `apps/web/src/app/startup/setupState.ts`
- Create: `apps/web/src/app/startup/setupState.test.ts`

**Interfaces:**

- Consumes: browser `fetch`, optional TanStack Query `AbortSignal`, published OpenAPI setup-state enum
- Produces: `SETUP_STATE_TIMEOUT_MS`, `SetupState`, `SetupStateResponse`, `parseSetupState`, `fetchSetupState`

- [ ] **Step 1: Write the parser falsifiers**

Create `setupState.test.ts` with exact success and rejection tables:

```ts
import { afterEach, expect, test, vi } from "vitest";
import {
  SETUP_STATE_TIMEOUT_MS,
  fetchSetupState,
  parseSetupState,
  type SetupState,
} from "./setupState";

test.each(["UNINITIALIZED", "IN_SETUP", "OPERATIONAL"] as const)(
  "accepts the published %s state",
  (setup_state: SetupState) => {
    expect(parseSetupState({ setup_state })).toEqual({ setup_state });
  },
);

test.each([
  null,
  [],
  {},
  { setup_state: null },
  { setup_state: 1 },
  { setup_state: "UNKNOWN" },
])("rejects untrusted setup-state payload %#", (payload) => {
  expect(() => parseSetupState(payload)).toThrow(
    "invalid setup state response",
  );
});
```

- [ ] **Step 2: Run the parser tests and observe RED**

```bash
npm --prefix apps/web run test -- src/app/startup/setupState.test.ts
```

Expected: FAIL because `./setupState` does not exist.

- [ ] **Step 3: Implement the closed parser**

```ts
export const SETUP_STATE_TIMEOUT_MS = 15_000;

const SETUP_STATES = new Set([
  "UNINITIALIZED",
  "IN_SETUP",
  "OPERATIONAL",
] as const);

export type SetupState = "UNINITIALIZED" | "IN_SETUP" | "OPERATIONAL";

export interface SetupStateResponse {
  setup_state: SetupState;
}

export function parseSetupState(value: unknown): SetupStateResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("invalid setup state response");
  }
  const setupState = (value as Record<string, unknown>).setup_state;
  if (
    typeof setupState !== "string" ||
    !SETUP_STATES.has(setupState as SetupState)
  ) {
    throw new Error("invalid setup state response");
  }
  return { setup_state: setupState as SetupState };
}
```

- [ ] **Step 4: Run the parser tests and observe GREEN**

```bash
npm --prefix apps/web run test -- src/app/startup/setupState.test.ts
```

Expected: both tables pass.

- [ ] **Step 5: Add request failure, cancellation, and timeout falsifiers**

Add a local deferred helper and restore timers/mocks after each test. Cover network rejection, non-2xx,
invalid JSON, unknown decoded state, caller cancellation, successful cleanup, and the exact deadline.
The deadline test must contain:

```ts
vi.useFakeTimers();
const response = deferred<Response>();
vi.spyOn(globalThis, "fetch").mockReturnValue(response.promise);

const attempt = fetchSetupState();
const rejected = expect(attempt).rejects.toThrow(
  "setup state request timed out",
);
await vi.advanceTimersByTimeAsync(SETUP_STATE_TIMEOUT_MS - 1);
expect(vi.getTimerCount()).toBe(1);
await vi.advanceTimersByTimeAsync(1);
await rejected;
expect(vi.getTimerCount()).toBe(0);

response.resolve(Response.json({ setup_state: "OPERATIONAL" }));
await vi.runAllTicks();
await expect(attempt).rejects.toThrow("setup state request timed out");
```

Assert each call targets `/api/v1/setup/state` and receives a composed signal. Error expectations use
generic messages only; never compare against response body, URL, status number, or malformed value.

- [ ] **Step 6: Run the request tests and observe RED**

```bash
npm --prefix apps/web run test -- src/app/startup/setupState.test.ts
```

Expected: parser cases pass and request cases fail because `fetchSetupState` is absent.

- [ ] **Step 7: Implement the deadline and request race**

```ts
export async function fetchSetupState(
  signal?: AbortSignal,
): Promise<SetupStateResponse> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectCancellation: ((reason: unknown) => void) | undefined;

  const cancelled = new Promise<never>((_resolve, reject) => {
    rejectCancellation = reject;
  });
  const cancel = () => {
    controller.abort();
    rejectCancellation?.(
      new DOMException("Setup state request cancelled", "AbortError"),
    );
  };
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });

  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("setup state request timed out"));
    }, SETUP_STATE_TIMEOUT_MS);
  });

  const request = (async () => {
    const response = await fetch("/api/v1/setup/state", {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("setup state request failed");
    return parseSetupState(await response.json());
  })();

  try {
    return await Promise.race([request, timeout, cancelled]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
```

The explicit race ensures a test double that ignores abort cannot win after timeout or cancellation.

- [ ] **Step 8: Run focused verification and commit Task 1**

```bash
npm --prefix apps/web run test -- src/app/startup/setupState.test.ts
npm --prefix apps/web run typecheck
npx --prefix apps/web prettier --check apps/web/src/app/startup/setupState.ts apps/web/src/app/startup/setupState.test.ts
git diff --check
git status --short
```

```bash
git add apps/web/src/app/startup/setupState.ts apps/web/src/app/startup/setupState.test.ts
git commit -m "feat: bound setup state reads"
```

---

### Task 2: Build the setup-specific startup and recovery screen

**Files:**

- Create: `apps/web/src/app/startup/SetupStartupScreen.tsx`
- Create: `apps/web/src/app/startup/SetupStartupScreen.test.tsx`

**Interfaces:**

- Consumes: one `SetupStartupStatus`, one promise-returning read retry, one injected reload callback
- Produces: `SetupStartupScreen`, `SetupStartupPhase`, `SetupStartupStatus`, `SetupStartupScreenProps`

- [ ] **Step 1: Write loading and safe-copy rendering tests**

Use this exact table:

```ts
const CASES = [
  [
    "loading",
    "initial",
    "Checking setup status",
    "Please wait while EasySynQ verifies this installation.",
  ],
  [
    "loading",
    "post-finalization",
    "Verifying setup",
    "Setup was saved. EasySynQ is confirming that the installation is ready.",
  ],
  [
    "error",
    "initial",
    "Setup status is unavailable",
    "EasySynQ could not confirm whether this installation is ready. Setup changes are disabled until the status can be verified.",
  ],
  [
    "error",
    "post-finalization",
    "Setup was saved, but could not be verified",
    "Try checking the setup status again. EasySynQ will not repeat finalization.",
  ],
] as const;
```

Loading asserts a named `role="status"`, logo, guidance, and no actions/navigation. Error asserts the
level-one heading, guidance, both actions, administrator hint, and absence of
`unsafe database host https://internal.invalid`.

- [ ] **Step 2: Add interface, focus, interaction, geometry, and axe falsifiers**

Add type assertions with `@ts-expect-error` proving props reject `error` and `detail`. Add tests for:

```ts
const retry = deferred<void>();
const onRetry = vi.fn(() => retry.promise);
renderScreen({ kind: "error", phase: "initial" }, onRetry);

const button = screen.getByRole("button", { name: "Try again" });
await user.click(button);
await user.click(button);
expect(onRetry).toHaveBeenCalledTimes(1);
expect(button).toBeDisabled();
expect(button).toHaveAttribute("aria-busy", "true");
expect(button).toHaveStyle({ minHeight: "44px" });
```

Also prove the error `h1` receives focus with `tabIndex={-1}`, reload calls once, both actions are 44 px,
the canvas uses `lg` inline padding, the panel has maximum width 440/minimum width zero, and all four
statuses pass `axe(container)`.

- [ ] **Step 3: Run the screen suite and observe RED**

```bash
npm --prefix apps/web run test -- src/app/startup/SetupStartupScreen.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 4: Implement the pure setup screen**

Use the Auth screen's established geometry without importing or modifying it:

```tsx
import {
  Button,
  Center,
  Image,
  Loader,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";

export type SetupStartupPhase = "initial" | "post-finalization";
export type SetupStartupStatus =
  | { kind: "loading"; phase: SetupStartupPhase }
  | { kind: "error"; phase: SetupStartupPhase };

export interface SetupStartupScreenProps {
  status: SetupStartupStatus;
  onRetry: () => Promise<void>;
  onReload: () => void;
}

const COPY = {
  initial: {
    loading: {
      label: "Checking setup status",
      status: "Checking setup status…",
      guidance: "Please wait while EasySynQ verifies this installation.",
    },
    error: {
      heading: "Setup status is unavailable",
      guidance:
        "EasySynQ could not confirm whether this installation is ready. Setup changes are disabled until the status can be verified.",
    },
  },
  "post-finalization": {
    loading: {
      label: "Verifying setup",
      status: "Verifying setup…",
      guidance:
        "Setup was saved. EasySynQ is confirming that the installation is ready.",
    },
    error: {
      heading: "Setup was saved, but could not be verified",
      guidance:
        "Try checking the setup status again. EasySynQ will not repeat finalization.",
    },
  },
} as const;

export function SetupStartupScreen({
  status,
  onRetry,
  onReload,
}: SetupStartupScreenProps) {
  const [retryBusy, setRetryBusy] = useState(false);
  const retryPromiseRef = useRef<Promise<void> | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusedPhaseRef = useRef<SetupStartupPhase | null>(null);

  useEffect(() => {
    if (status.kind !== "error") {
      focusedPhaseRef.current = null;
      return;
    }
    if (focusedPhaseRef.current !== status.phase) {
      headingRef.current?.focus();
      focusedPhaseRef.current = status.phase;
    }
  }, [status]);

  const handleRetry = async (): Promise<void> => {
    if (retryPromiseRef.current) return retryPromiseRef.current;
    setRetryBusy(true);
    const attempt = Promise.resolve().then(onRetry);
    retryPromiseRef.current = attempt;
    try {
      await attempt;
    } catch {
      // The next setup query state owns failure presentation.
    } finally {
      if (retryPromiseRef.current === attempt) retryPromiseRef.current = null;
      setRetryBusy(false);
    }
  };

  const phaseCopy = COPY[status.phase];
  const content =
    status.kind === "loading" ? (
      <Stack
        align="center"
        gap="sm"
        role="status"
        aria-live="polite"
        aria-label={phaseCopy.loading.label}
      >
        <Loader color="indigo" aria-hidden="true" />
        <Text fw={600}>{phaseCopy.loading.status}</Text>
        <Text c="var(--es-text-2)" size="sm" ta="center">
          {phaseCopy.loading.guidance}
        </Text>
      </Stack>
    ) : (
      <Stack align="stretch" gap="lg" aria-live="polite">
        <Stack align="center" gap="sm">
          <Title ref={headingRef} order={1} size="h2" ta="center" tabIndex={-1}>
            {phaseCopy.error.heading}
          </Title>
          <Text c="var(--es-text-2)" ta="center">
            {phaseCopy.error.guidance}
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button
            fullWidth
            color="indigo"
            loading={retryBusy}
            disabled={retryBusy}
            aria-busy={retryBusy || undefined}
            style={{ minHeight: 44 }}
            onClick={() => void handleRetry()}
          >
            Try again
          </Button>
          <Button
            fullWidth
            variant="subtle"
            color="gray"
            style={{ minHeight: 44 }}
            onClick={onReload}
          >
            Reload EasySynQ
          </Button>
        </Stack>
        <Text c="var(--es-text-2)" size="sm" ta="center">
          If this keeps happening, contact your EasySynQ administrator.
        </Text>
      </Stack>
    );

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
      >
        <Stack align="center" gap="xl">
          <Image
            src="/easysynq-mark.svg"
            alt="EasySynQ"
            w={64}
            h={64}
            fit="contain"
          />
          {content}
        </Stack>
      </Paper>
    </Center>
  );
}
```

- [ ] **Step 5: Run focused verification and commit Task 2**

```bash
npm --prefix apps/web run test -- src/app/startup/SetupStartupScreen.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/app/startup/SetupStartupScreen.tsx src/app/startup/SetupStartupScreen.test.tsx
npx --prefix apps/web prettier --check apps/web/src/app/startup/SetupStartupScreen.tsx apps/web/src/app/startup/SetupStartupScreen.test.tsx
git diff --check
git status --short
```

```bash
git add apps/web/src/app/startup/SetupStartupScreen.tsx apps/web/src/app/startup/SetupStartupScreen.test.tsx
git commit -m "feat: add setup state recovery screen"
```

---

### Task 3: Enforce fail-closed setup routing in App

**Files:**

- Modify: `apps/web/src/App.tsx:1-140`
- Modify: `apps/web/src/App.test.tsx:1-130`

**Interfaces:**

- Consumes: `fetchSetupState`, `SetupStartupScreen`, existing `AuthState`
- Produces: exact query policy, auth-first/setup-second boundary order, strict route authorization,
  single-read retry, unchanged operational redirect behavior

- [ ] **Step 1: Add network/server failure and no-mutation falsifiers**

In `App.test.tsx`, import `http`, `HttpResponse`, and the MSW server. Install a 503 state handler whose
body contains unsafe detail. Install spies for every current setup POST/PATCH endpoint. Render both
`/setup` and `/library` and assert:

```ts
expect(
  await screen.findByRole("heading", { name: "Setup status is unavailable" }),
).toBeInTheDocument();
expect(
  screen.queryByRole("heading", { name: "Welcome to EasySynQ" }),
).not.toBeInTheDocument();
expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
expect(login).not.toHaveBeenCalled();
expect(setupMutation).not.toHaveBeenCalled();
expect(document.body).not.toHaveTextContent("unsafe database host");
```

Repeat the failure proof with `HttpResponse.error()`.

- [ ] **Step 2: Add malformed and unknown response falsifiers**

Use a table containing invalid JSON, `{}`, `{ setup_state: null }`, and
`{ setup_state: "MYSTERY" }`. Each case renders setup recovery and mounts neither wizard nor shell.

- [ ] **Step 3: Pin all three valid routes**

- `OPERATIONAL` + token renders Document Library.
- `OPERATIONAL` + no token calls `login` once and preserves the shipped redirect screen/latch.
- `UNINITIALIZED` + no token redirects to setup and renders **Welcome to EasySynQ**.
- `IN_SETUP` + no token redirects to setup and renders **Welcome to EasySynQ**.

Using no token for the valid pre-operational fixtures keeps the sensitive detail query disabled.

- [ ] **Step 4: Add explicit-retry request-count and recovery falsifiers**

Use a state handler that returns 503 on read one and `OPERATIONAL` on read two. Render with a token,
assert one initial GET, click **Try again**, and wait for Document Library plus exactly two GETs. Add a
controlled second response and rapid double activation; the second logical attempt contributes one GET.

- [ ] **Step 5: Run the App falsifiers and observe RED**

```bash
npm --prefix apps/web run test -- src/App.test.tsx -t "setup state|UNINITIALIZED|IN_SETUP|OPERATIONAL"
```

Expected: failed and malformed responses currently route to setup and the named recovery view is absent.

- [ ] **Step 6: Install the typed query and setup screen**

```ts
const setupState = useQuery({
  queryKey: ["setup-state"],
  queryFn: ({ signal }) => fetchSetupState(signal),
  retry: false,
  staleTime: Infinity,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  refetchInterval: false,
});

const setupValue = setupState.data?.setup_state;
const operational = setupValue === "OPERATIONAL";
const preOperational =
  setupValue === "UNINITIALIZED" || setupValue === "IN_SETUP";
```

After the unchanged auth branch, render initial loading for `setupState.isPending`, then initial error
for `setupState.isError || (!operational && !preOperational)`. The error retry awaits:

```ts
await setupState.refetch({ cancelRefetch: false });
```

The invalid-success check is intentionally redundant with the parser: it preserves fail-closed rendering
if later code widens the query type. Remove the bare `Container`/`Loader` and generic `apiGet` call.

- [ ] **Step 7: Preserve auth effect and exhaustive routing**

Require `setupState.status === "success"` before automatic sign-in. Keep the sessionStorage operations and
Auth screen props unchanged. Reach routes only after `operational || preOperational`; keep existing route
conditions based on `operational`, never a falsy fallback.

- [ ] **Step 8: Run focused regressions and commit Task 3**

```bash
npm --prefix apps/web run test -- src/App.test.tsx src/app/startup/setupState.test.ts src/app/startup/SetupStartupScreen.test.tsx src/app/startup/AuthStartupScreen.test.tsx src/lib/auth.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/App.tsx src/App.test.tsx
npx --prefix apps/web prettier --check apps/web/src/App.tsx apps/web/src/App.test.tsx
git diff --check
git status --short
```

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx
git commit -m "fix: fail closed on untrusted setup state"
```

---

### Task 4: Make post-finalization recovery read-only

**Files:**

- Modify: `apps/web/src/SetupWizard.tsx:55-127,180-181`
- Modify: `apps/web/src/SetupWizard.test.tsx:1-56`
- Modify: `apps/web/src/App.tsx:58-140`
- Modify: `apps/web/src/App.test.tsx`

**Interfaces:**

- Consumes: Task 3 query/screen and current finalize mutation
- Produces: `onFinalized: () => Promise<void>`, App-local `FinalizationVerification`, read-only
  post-finalization recovery and contradictory-state guard

```ts
type FinalizationVerification = "idle" | "checking" | "error";
```

- [ ] **Step 1: Add SetupWizard callback-ordering falsifiers**

Create a finalization-ready detail fixture with every gate true. On finalize success, use a controlled
`onFinalized` promise and assert the finalize POST occurs once, callback occurs once after the response,
and the button remains busy until that promise settles. With a 409 finalize handler, assert the callback
is never called and the existing wizard error renders.

- [ ] **Step 2: Run the wizard tests and observe RED**

```bash
npm --prefix apps/web run test -- src/SetupWizard.test.tsx -t "onFinalized|finalize"
```

Expected: the current void callback is not awaited.

- [ ] **Step 3: Make only the after-success seam promise-aware**

```ts
onFinalized: () => Promise<void>;

const run = async (
  fn: () => Promise<unknown>,
  after?: () => void | Promise<void>,
): Promise<void> => {
  setBusy(true);
  setError(null);
  try {
    await fn();
    await after?.();
  } catch (e) {
    setError(e instanceof ApiError ? e.message : String(e));
  } finally {
    setBusy(false);
  }
};
```

Leave every other wizard callback semantically unchanged.

- [ ] **Step 4: Add App finalization/refetch-failure falsifiers**

Configure initial `IN_SETUP`, finalization-ready detail, one successful finalize POST, a failed state GET,
then an `OPERATIONAL` retry GET. Assert **Verifying setup** while pending, then the approved
post-finalization failure heading, no wizard/finalize button, one finalize call, and recovery to the
**QMS health** heading after **Try again** with three total state reads and still one finalize call.

Add a second case where the post-finalization read succeeds with contradictory `IN_SETUP`; it must show
the same post-finalization recovery and keep the wizard hidden.

- [ ] **Step 5: Run finalization tests and observe RED**

```bash
npm --prefix apps/web run test -- src/App.test.tsx -t "finalization|finalize|verification"
```

Expected: the current callback leaves the wizard available when refetch fails.

- [ ] **Step 6: Implement App-local verification state**

```ts
const [finalizationVerification, setFinalizationVerification] =
  useState<FinalizationVerification>("idle");

const verifyFinalization = async (): Promise<void> => {
  setFinalizationVerification("checking");
  const result = await setupState.refetch({ cancelRefetch: false });
  if (
    result.status === "success" &&
    result.data.setup_state === "OPERATIONAL"
  ) {
    setFinalizationVerification("idle");
    return;
  }
  setFinalizationVerification("error");
};
```

After auth but before ordinary setup query branches, render post-finalization loading for `checking` and
post-finalization recovery for `error`; that recovery passes `verifyFinalization` as its only retry.
Pass `onFinalized={verifyFinalization}` to `SetupWizard`. Local verification state must outrank cached
query data and status.

- [ ] **Step 7: Run focused setup/auth verification and commit Task 4**

```bash
npm --prefix apps/web run test -- src/App.test.tsx src/SetupWizard.test.tsx src/app/startup/setupState.test.ts src/app/startup/SetupStartupScreen.test.tsx src/app/startup/AuthStartupScreen.test.tsx src/lib/auth.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/App.tsx src/App.test.tsx src/SetupWizard.tsx src/SetupWizard.test.tsx
npx --prefix apps/web prettier --check apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/SetupWizard.tsx apps/web/src/SetupWizard.test.tsx
git diff --check
git status --short
```

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/SetupWizard.tsx apps/web/src/SetupWizard.test.tsx
git commit -m "fix: make setup finalization recovery read only"
```

---

### Task 5: Run whole-slice verification and record fresh evidence

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`

**Interfaces:**

- Consumes: all green implementation commits and exact command output
- Produces: shipped `S-setup-state-boundary` current snapshot and durable historical evidence

- [ ] **Step 1: Format touched files and run complete affected gates**

Run scoped Prettier write/check on the eight touched web files, then:

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

Record exact test counts, command results, implementation commit, and date. Do not convert a skipped,
unavailable, rerun, or partial result into a pass. Preserve the Vite advisory and current residuals unless
fresh in-scope evidence establishes a change.

- [ ] **Step 2: Update current authority from fresh evidence only**

In `docs/current-status.md`, set `last_shipped_slice` to `S-setup-state-boundary`, set
`baseline_commit` to the implementation commit where complete web evidence ran, update only fresh web
counts, and describe the new boundary. Preserve migration `0085` / next `0086`, non-web suite counts,
CI topology, and pending Fedora proof unless freshly verified.

In `docs/slice-history.md`, record the parser/deadline, no-hidden-retry policy, setup recovery UI, strict
routing/no-mutation proof, finalization verification-only behavior, auth regression result, and exact
commands/counts.

- [ ] **Step 3: Re-run documentation and repository guards**

```bash
npx --prefix apps/web prettier --check docs/current-status.md docs/slice-history.md
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
git commit -m "docs: record setup state boundary"
```

- [ ] **Step 5: Perform an independent whole-branch review**

Review `a21128b..HEAD` against the approved design. Inspect parser closure, timeout/cancellation cleanup,
query policy, error precedence, auth-first behavior, no-wizard/no-mutation falsifiers, request counts,
finalization non-replay, raw-detail exclusion, accessibility evidence, full verification, and scope.

Address every Critical or Important finding with a new failing test, minimal fix, focused green proof,
and a separate correction commit. After any correction, repeat Step 1 and refresh recorded evidence if
counts or the implementation evidence commit changed.

- [ ] **Step 6: Confirm final branch state for owner selection**

```bash
git log --oneline --decorate a21128b..HEAD
git diff --stat a21128b..HEAD
git diff --check a21128b..HEAD
git status --short --branch
```

Expected: scoped commits only and a clean branch. Do not push or open a draft PR until the owner selects
publication.
