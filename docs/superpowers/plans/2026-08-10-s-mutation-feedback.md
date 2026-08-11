# S-mutation-feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make notification mark-one, mark-all, and preference-save failures visible, announced, intent-preserving, and explicitly retryable only under the approved repeat-safe contract.

**Architecture:** Extend the existing inline mutation Alert for accessible actions, and add a keyed operational-route feedback provider for the one failure that outlives its source component. Notification hooks retain immutable retry intent and reuse feature request functions; existing API behavior is unchanged and gains repeated-write integration proof.

**Tech Stack:** React 19, TypeScript 6, React Router 7, TanStack Query 5, Mantine 7, Vitest 4, Testing Library, MSW, jest-axe, FastAPI, SQLAlchemy, pytest, Ruff, mypy.

## Global Constraints

- Baseline is `323c79b`; the design-only commit is `36a8d74`.
- Scope is only mark-one notification read, mark-all notification read, notification-preferences save, the shared feedback primitives they require, executable API repeat-safety proof, and evidence docs.
- Notification-link navigation remains immediate; it never waits for mark-read persistence.
- Persistent feedback has no timer and remains until Dismiss or successful Retry.
- No mutation retries automatically.
- Retry is shown only when both gates pass: the caller names one of these proven repeat-safe operations, and the failure is a fetch `TypeError`, HTTP 408/429, or HTTP 5xx.
- HTTP 400/401/403/404/409/422 feedback has no Retry action.
- Persistent state stores normalized safe display text, never raw exceptions, stacks, URLs, tokens, response bodies, or notification IDs in visible copy.
- Existing auth/setup gates, route focus/title ownership, error boundaries, QueryClient identity/lifecycle, notification delivery, SSE, and query semantics remain unchanged.
- No API handler, OpenAPI/generated contract, dependency, migration, database schema, Keycloak, permission, or idempotency-key change.
- Use existing Mantine components/tokens; add no palette, animation, toast dependency, or timer.
- Affected mutation actions have at least a 44 CSS px target.
- Preserve the primary checkout's owner-owned `.superdesign/`; work only in `/tmp/EasySynQ-mutation-feedback`.
- Run `bash scripts/check-no-site-data.sh` before handoff and commit no site data or generated artifacts.

---

### Task 1: Accessible inline mutation error actions

**Files:**

- Modify: `apps/web/src/lib/states.tsx`
- Modify: `apps/web/src/lib/states.test.tsx`

**Interfaces:**

- Consumes: existing `ApiError`, Mantine `Alert`, `Button`, `Group`, `Stack`, `Text`.
- Produces: backward-compatible `MutationErrorState` props plus `message`, `onRetry`, `retrying`, `onDismiss`, `retryLabel`, and `dismissLabel`.
- Invariant: every existing caller that passes only `title` and `error` renders the same title and safe message.

- [ ] **Step 1: Write failing action and announcement tests**

Add tests that require the new semantic and action contract:

```tsx
test("MutationErrorState announces the error and wires named retry and dismiss actions", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  const onDismiss = vi.fn();
  renderWithProviders(
    <MutationErrorState
      title="Couldn't mark notifications read"
      message="The request didn't complete. Please try again."
      onRetry={onRetry}
      onDismiss={onDismiss}
      retryLabel="Try marking all notifications read again"
      dismissLabel="Dismiss mark-all error"
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert).toHaveAttribute("aria-atomic", "true");
  expect(alert).toHaveTextContent("Couldn't mark notifications read");
  await user.click(
    screen.getByRole("button", {
      name: "Try marking all notifications read again",
    }),
  );
  await user.click(
    screen.getByRole("button", { name: "Dismiss mark-all error" }),
  );
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

test("MutationErrorState disables retry while the retained intent is running", () => {
  renderWithProviders(
    <MutationErrorState
      title="Couldn't save"
      error={new TypeError("network")}
      onRetry={() => undefined}
      retrying
    />,
  );
  expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
});
```

Extend the primitive axe fixture so it renders both Retry and Dismiss.

- [ ] **Step 2: Run the focused RED test**

Run:

```bash
npm --prefix apps/web test -- src/lib/states.test.tsx
```

Expected: FAIL because `message`, action props, and `role="alert"` are absent.

- [ ] **Step 3: Implement the backward-compatible presentation**

Extend imports to include `Group`. Add a discriminated content type and action props:

```tsx
type MutationErrorContent =
  | { error: unknown; message?: never; fallback?: ReactNode }
  | { error?: never; message: ReactNode; fallback?: never };

type MutationErrorActions = {
  onRetry?: () => void;
  retrying?: boolean;
  onDismiss?: () => void;
  retryLabel?: string;
  dismissLabel?: string;
};

export type MutationErrorStateProps = {
  title: string;
} & MutationErrorContent &
  MutationErrorActions;
```

Replace the existing implementation with:

```tsx
export function MutationErrorState(props: MutationErrorStateProps) {
  const {
    title,
    onRetry,
    retrying = false,
    onDismiss,
    retryLabel = "Try again",
    dismissLabel = "Dismiss",
  } = props;
  const content =
    "message" in props
      ? props.message
      : props.error instanceof ApiError
        ? props.error.message
        : (props.fallback ?? "Please try again.");

  return (
    <Alert color="red" title={title} role="alert" aria-atomic="true">
      <Stack gap="sm" align="flex-start">
        <Text size="sm">{content}</Text>
        {(onRetry || onDismiss) && (
          <Group gap="xs">
            {onRetry && (
              <Button
                variant="light"
                color="red"
                mih={44}
                loading={retrying}
                disabled={retrying}
                onClick={onRetry}
              >
                {retryLabel}
              </Button>
            )}
            {onDismiss && (
              <Button
                variant="subtle"
                color="gray"
                mih={44}
                onClick={onDismiss}
              >
                {dismissLabel}
              </Button>
            )}
          </Group>
        )}
      </Stack>
    </Alert>
  );
}
```

- [ ] **Step 4: Run focused GREEN and compatibility tests**

Run:

```bash
npm --prefix apps/web test -- src/lib/states.test.tsx src/features/audits/ProgramForm.test.tsx src/admin/ConfigAdmin.test.tsx
```

Expected: PASS; old error-only callers remain compatible and the new actions are axe-clean.

- [ ] **Step 5: Format, inspect, and commit Task 1**

Run:

```bash
cd apps/web
npx prettier --write src/lib/states.tsx src/lib/states.test.tsx
npx eslint src/lib/states.tsx src/lib/states.test.tsx
npx tsc --noEmit
cd ../..
git diff --check
git diff -- apps/web/src/lib/states.tsx apps/web/src/lib/states.test.tsx
git add apps/web/src/lib/states.tsx apps/web/src/lib/states.test.tsx
git commit -m "feat: add accessible mutation error actions"
```

---

### Task 2: Persistent keyed feedback provider

**Files:**

- Create: `apps/web/src/lib/mutationFeedback.tsx`
- Create: `apps/web/src/lib/mutationFeedback.test.tsx`
- Modify: `apps/web/src/test/render.tsx`

**Interfaces:**

- Consumes: `MutationErrorState`, `ApiError`, React context/state/ref APIs.
- Produces:
  - `isRetryableMutationError(error: unknown): boolean`
  - `MutationFeedbackInput`
  - `MutationFeedbackProvider`
  - `useMutationFeedback(): { report(input): void; dismiss(key): void }`
  - `MutationFeedbackOutlet`
- Invariant: `report` normalizes the visible message before storage and retains a retry callback only when the initial failure is retry-eligible.

- [ ] **Step 1: Write retry-classification RED tests**

Create `mutationFeedback.test.tsx` with a table that requires the exact status policy:

```tsx
test.each([
  [new TypeError("network"), true],
  [new ApiError(408, "timeout", "Timed out"), true],
  [new ApiError(429, "rate_limited", "Slow down"), true],
  [new ApiError(500, "error", "Unavailable"), true],
  [new ApiError(599, "error", "Unavailable"), true],
  [new ApiError(400, "bad", "Bad request"), false],
  [new ApiError(401, "unauthorized", "Unauthorized"), false],
  [new ApiError(403, "forbidden", "Forbidden"), false],
  [new ApiError(404, "not_found", "Missing"), false],
  [new ApiError(409, "conflict", "Conflict"), false],
  [new ApiError(422, "invalid", "Invalid"), false],
  [new Error("programming"), false],
  [undefined, false],
])("classifies mutation retry eligibility", (error, expected) => {
  expect(isRetryableMutationError(error)).toBe(expected);
});
```

- [ ] **Step 2: Write provider/outlet RED tests**

Use a small harness component that calls `useMutationFeedback().report`. Cover:

```tsx
const firstRetry = vi.fn(async () => undefined);
const secondRetry = vi.fn(async () => undefined);

report({
  key: "mark-read:n1",
  title: "Couldn't mark First read",
  error: new ApiError(503, "down", "Service unavailable"),
  retry: firstRetry,
  retryLabel: "Try marking First read again",
  dismissLabel: "Dismiss mark-read error for First",
  successMessage: "Notification marked read",
});
report({
  key: "mark-read:n2",
  title: "Couldn't mark Second read",
  error: new ApiError(503, "down", "Service unavailable"),
  retry: secondRetry,
  retryLabel: "Try marking Second read again",
  dismissLabel: "Dismiss mark-read error for Second",
  successMessage: "Notification marked read",
});
```

Assert distinct entries render; reporting `mark-read:n1` again updates instead of duplicating; Dismiss removes only one. Add a deferred Retry test that clicks twice while pending and asserts one callback, then resolves and asserts the entry clears and the polite announcer contains `Notification marked read`. Add a rejection test where Retry throws a 404 and assert the updated entry keeps safe API copy but loses its Retry button. Run axe on two simultaneous entries.

- [ ] **Step 3: Run the provider RED test**

Run:

```bash
npm --prefix apps/web test -- src/lib/mutationFeedback.test.tsx
```

Expected: FAIL because the module and interfaces do not exist.

- [ ] **Step 4: Implement the provider and conservative classifier**

Define the public input:

```tsx
export interface MutationFeedbackInput {
  key: string;
  title: string;
  error: unknown;
  retry?: () => Promise<void>;
  retryLabel?: string;
  dismissLabel: string;
  successMessage?: string;
}

export function isRetryableMutationError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  return (
    error instanceof ApiError &&
    (error.status === 408 ||
      error.status === 429 ||
      (error.status >= 500 && error.status <= 599))
  );
}
```

Normalize messages with:

```tsx
function safeMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The request didn't complete. Please try again.";
}
```

Store entries as safe strings plus the approved callback. Use a `Set<string>` ref as a synchronous
in-flight guard. `report` upserts by key. `retryEntry` marks the entry pending, awaits the callback,
removes it and updates the polite announcement on success, or replaces the safe message and drops Retry
when the new error is not retryable. Always clear the in-flight key in `finally`.

Render each entry through:

```tsx
<MutationErrorState
  title={entry.title}
  message={entry.message}
  onRetry={entry.retry ? () => void retryEntry(entry.key) : undefined}
  retrying={entry.retrying}
  onDismiss={() => dismiss(entry.key)}
  retryLabel={entry.retryLabel}
  dismissLabel={entry.dismissLabel}
/>
```

Keep one mounted polite announcer in the outlet:

```tsx
<VisuallyHidden role="status" aria-live="polite" aria-atomic="true">
  {announcement}
</VisuallyHidden>
```

- [ ] **Step 5: Put the provider in the shared web test harness**

Import `MutationFeedbackProvider` in `src/test/render.tsx` and wrap the MemoryRouter children:

```tsx
<AuthContext.Provider value={auth}>
  <MutationFeedbackProvider>
    <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
  </MutationFeedbackProvider>
</AuthContext.Provider>
```

This mirrors the application dependency for direct component tests. Each render gets a fresh provider,
so feedback cannot cross test boundaries.

- [ ] **Step 6: Run focused GREEN and harness regression tests**

Run:

```bash
npm --prefix apps/web test -- src/lib/mutationFeedback.test.tsx src/lib/states.test.tsx src/test/render.test.tsx
```

Expected: PASS with no pending notification leak or unhandled error.

- [ ] **Step 7: Format, statically verify, inspect, and commit Task 2**

Run:

```bash
cd apps/web
npx prettier --write src/lib/mutationFeedback.tsx src/lib/mutationFeedback.test.tsx src/test/render.tsx
npx eslint src/lib/mutationFeedback.tsx src/lib/mutationFeedback.test.tsx src/test/render.tsx
npx tsc --noEmit
cd ../..
git diff --check
git diff -- apps/web/src/lib/mutationFeedback.tsx apps/web/src/lib/mutationFeedback.test.tsx apps/web/src/test/render.tsx
git add apps/web/src/lib/mutationFeedback.tsx apps/web/src/lib/mutationFeedback.test.tsx apps/web/src/test/render.tsx
git commit -m "feat: retain route mutation feedback"
```

---

### Task 3: Mark-one local and cross-route feedback

**Files:**

- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Modify: `apps/web/src/features/notifications/mutations.ts`
- Modify: `apps/web/src/features/notifications/hooks.test.tsx`
- Modify: `apps/web/src/features/notifications/NotificationItem.tsx`
- Modify: `apps/web/src/features/notifications/NotificationItem.test.tsx`

**Interfaces:**

- Consumes: Task 2 `useMutationFeedback`, `MutationFeedbackOutlet`, and retry classifier.
- Produces:
  - `markNotificationRead(api, notificationId): Promise<{ status: string }>`
  - backward-compatible `useMarkRead(options?)`
  - `useMarkReadOnOpen(notificationTitle)` for hook-level cross-route failure ownership.
- Invariant: explicit mark-one is local; link-triggered mark-one is persistent; both issue the same endpoint and invalidate the same query prefix on success.

- [ ] **Step 1: Write explicit mark-one RED tests**

Extend `NotificationItem.test.tsx` with an MSW handler that returns 503 once and 200 on Retry. Assert:

```tsx
expect(await screen.findByRole("alert")).toHaveTextContent(
  "Couldn't mark this notification read",
);
expect(screen.getByText("Unread")).toBeInTheDocument();
await user.click(
  screen.getByRole("button", {
    name: "Try marking this notification read again",
  }),
);
await waitFor(() => expect(requestedIds).toEqual(["n1", "n1"]));
```

Add a 404 case that renders Dismiss but no Retry. Assert the explicit ActionIcon has a 44px size or
minimum target through the deliberate prop/style emitted by the implementation.

- [ ] **Step 2: Write cross-route lifetime RED test**

In `App.test.tsx`, override `GET /notifications` with one unread item whose deep link is
`http://localhost/library`. Hold `POST /notifications/:id/read` in a deferred promise. Render `<App />`
at `/notifications`, click the notification link, and assert the Library route and final route-chrome
focus arrive before resolving the request.

Resolve the first POST with a 503 response, then assert:

```tsx
const main = screen.getByRole("main");
await waitFor(() => expect(main).toHaveFocus());
const alert = await screen.findByRole("alert");
expect(alert).toHaveTextContent("remains unread");
expect(main).toHaveFocus();
```

Make the second POST return 200. Click the uniquely named persistent Retry, assert both requests used
the same notification ID, the alert disappears, and the polite announcer contains
`Notification marked read`.

- [ ] **Step 3: Run mark-one RED tests**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/NotificationItem.test.tsx src/App.test.tsx
```

Expected: FAIL because mark-one errors remain invisible and no persistent provider/outlet is wired.

- [ ] **Step 4: Extract the request seam and hook-level failure option**

In `mutations.ts`, define a structural API type and request function:

```tsx
type NotificationWriteApi = ReturnType<typeof useApi>;

export function markNotificationRead(
  api: NotificationWriteApi,
  notificationId: string,
) {
  return api.send<{ status: string }>(
    "POST",
    `/api/v1/notifications/${notificationId}/read`,
  );
}
```

Accept an optional hook-owned failure callback:

```tsx
export function useMarkRead(options?: {
  onError?: (error: unknown, id: string) => void;
}) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => markNotificationRead(api, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
    onError: options?.onError,
  });
}
```

Add `useMarkReadOnOpen(notificationTitle)` that reports a keyed entry from its hook-level `onError`.
Its retry callback calls `markNotificationRead(api, id)` and then awaits notification-prefix
invalidation. Use fixed visible copy stating that the notification remains unread; use title-derived
accessible action labels; set success copy to `Notification marked read`.

Extend `hooks.test.tsx` to prove the hook-level callback receives both the error and exact ID even when
the observer's component unmounts before the MSW response settles.

- [ ] **Step 5: Split NotificationItem's local and navigation mutations**

Use two mutation instances:

```tsx
const markRead = useMarkRead();
const markReadOnOpen = useMarkReadOnOpen(notification.title);

function open() {
  if (unread) markReadOnOpen.mutate(notification.id);
  onNavigate?.();
}
```

Wrap the existing row in a `Stack`. Render a local `MutationErrorState` only from `markRead.isError`.
Pass Retry only when `isRetryableMutationError(markRead.error)` and replay
`markRead.variables ?? notification.id`; pass `markRead.reset` as Dismiss. Size the explicit ActionIcon
to 44px and disable it while the local mutation is pending.

- [ ] **Step 6: Mount the provider and outlet at the approved lifetime**

In `App.tsx`, wrap only the final operational `<Routes>` return with `MutationFeedbackProvider`. Do not
wrap startup or setup returns. In `AppShell.tsx`, render `<MutationFeedbackOutlet />` after `Breadcrumb`
and before `ApplicationErrorBoundary`, so route-content failure/retry cannot erase retained mutation
feedback.

- [ ] **Step 7: Run focused GREEN and route-boundary regressions**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/hooks.test.tsx src/features/notifications/NotificationItem.test.tsx src/App.test.tsx src/app/shell/AppShell.test.tsx src/lib/routeChrome.test.tsx
```

Expected: PASS; navigation stays immediate, final focus remains route-owned, and late failure persists.

- [ ] **Step 8: Format, statically verify, inspect, and commit Task 3**

Run:

```bash
cd apps/web
npx prettier --write src/App.tsx src/App.test.tsx src/app/shell/AppShell.tsx src/features/notifications/mutations.ts src/features/notifications/hooks.test.tsx src/features/notifications/NotificationItem.tsx src/features/notifications/NotificationItem.test.tsx
npx eslint src/App.tsx src/App.test.tsx src/app/shell/AppShell.tsx src/features/notifications/mutations.ts src/features/notifications/hooks.test.tsx src/features/notifications/NotificationItem.tsx src/features/notifications/NotificationItem.test.tsx
npx tsc --noEmit
cd ../..
git diff --check
git diff --stat 36a8d74..HEAD
git add apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/features/notifications/mutations.ts apps/web/src/features/notifications/hooks.test.tsx apps/web/src/features/notifications/NotificationItem.tsx apps/web/src/features/notifications/NotificationItem.test.tsx
git commit -m "feat: retain failed notification read intent"
```

---

### Task 4: Mark-all feedback in bell and full page

**Files:**

- Modify: `apps/web/src/features/notifications/NotificationBell.tsx`
- Modify: `apps/web/src/features/notifications/NotificationBell.test.tsx`
- Modify: `apps/web/src/features/notifications/NotificationsPage.tsx`
- Modify: `apps/web/src/features/notifications/NotificationsPage.test.tsx`

**Interfaces:**

- Consumes: `MutationErrorState`, `isRetryableMutationError`, existing `useMarkAllRead`.
- Produces: local, announced, retryable mark-all failures in both existing surfaces.
- Invariant: neither mark-all surface publishes persistent feedback.

- [ ] **Step 1: Write bell and page RED tests**

For each component, install a handler that returns 503 once and success next. Activate Mark all read,
assert an alert appears beside that surface, and click `Try marking all notifications read again`.
Assert exactly two requests. Add one axe assertion over the failed state.

Add a 403 case to one surface and assert the server message and Dismiss render without Retry. Assert the
original Mark all read control and the error actions expose a 44px minimum target through their explicit
props/styles.

- [ ] **Step 2: Run the focused RED tests**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.test.tsx
```

Expected: FAIL because mark-all mutation errors are not rendered.

- [ ] **Step 3: Render local error actions in both surfaces**

Below each Mark all read control, render:

```tsx
{
  markAll.isError && (
    <MutationErrorState
      title="Couldn't mark notifications read"
      error={markAll.error}
      onRetry={
        isRetryableMutationError(markAll.error)
          ? () => markAll.mutate()
          : undefined
      }
      retrying={markAll.isPending}
      onDismiss={markAll.reset}
      retryLabel="Try marking all notifications read again"
      dismissLabel="Dismiss mark-all error"
    />
  );
}
```

Place the bell alert below its header `Group` and before the scroll area. Place the page alert below its
header `Group` and before the list state. Add `mih={44}` to both Mark all read buttons. Keep existing list
load/error/empty behavior unchanged.

- [ ] **Step 4: Run focused GREEN and notification regressions**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.test.tsx src/features/notifications/NotificationItem.test.tsx
```

Expected: PASS with no persistent alert created by mark-all.

- [ ] **Step 5: Format, inspect, and commit Task 4**

Run:

```bash
cd apps/web
npx prettier --write src/features/notifications/NotificationBell.tsx src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.tsx src/features/notifications/NotificationsPage.test.tsx
npx eslint src/features/notifications/NotificationBell.tsx src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.tsx src/features/notifications/NotificationsPage.test.tsx
npx tsc --noEmit
cd ../..
git diff --check
git diff -- apps/web/src/features/notifications/NotificationBell.tsx apps/web/src/features/notifications/NotificationsPage.tsx
git add apps/web/src/features/notifications/NotificationBell.tsx apps/web/src/features/notifications/NotificationBell.test.tsx apps/web/src/features/notifications/NotificationsPage.tsx apps/web/src/features/notifications/NotificationsPage.test.tsx
git commit -m "feat: surface failed mark-all actions"
```

---

### Task 5: Preserve and replace notification-preference retry intent

**Files:**

- Modify: `apps/web/src/features/notifications/NotificationSettingsPage.tsx`
- Modify: `apps/web/src/features/notifications/NotificationSettingsPage.test.tsx`

**Interfaces:**

- Consumes: Task 1 action-enabled `MutationErrorState`, Task 2 retry classifier, TanStack mutation `variables` and `reset`.
- Produces: exact-body Retry and edit-replaces-intent behavior.
- Invariant: existing partial-update calculation, successful invalidation/refetch, and `Saved.` copy remain unchanged.

- [ ] **Step 1: Write exact-intent RED test**

Return 503 for the first PUT and success for the second. Change the action-required cadence to Off, Save,
and assert all working values remain unchanged. Click `Try saving these preferences again` and assert both
request bodies equal:

```ts
{
  digest_modes: {
    action_required: "off";
  }
}
```

Assert the Save control, Retry, and Dismiss have explicit 44px minimum targets.

- [ ] **Step 2: Write edit-replaces-intent RED test**

Fail `{ email_enabled: false }`, then edit the digest hour before retrying. Assert the old Alert and Retry
disappear. Click Save and assert the second body reflects the new current diff, not an implicit replay of
the old mutation variables:

```ts
{ email_enabled: false, digest_hour: 6 }
```

- [ ] **Step 3: Run the settings RED test**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/NotificationSettingsPage.test.tsx
```

Expected: FAIL because the current error has no Retry and editing does not reset mutation intent.

- [ ] **Step 4: Centralize user edits and wire exact Retry**

Add a user-edit helper after `update` is created:

```tsx
function editWorking(next: Working) {
  if (update.isError) update.reset();
  setWorking(next);
}
```

Replace every control-originated `setWorking(next)` with `editWorking(next)`. Keep the `useEffect` that
seeds/refetches working state on `prefs.data` using `setWorking`, because server synchronization is not a
new user edit.

Render the error with immutable mutation variables:

```tsx
{
  update.isError && (
    <MutationErrorState
      title="Couldn't save your preferences"
      error={update.error}
      onRetry={
        update.variables && isRetryableMutationError(update.error)
          ? () => update.mutate(update.variables)
          : undefined
      }
      retrying={update.isPending}
      onDismiss={update.reset}
      retryLabel="Try saving these preferences again"
      dismissLabel="Dismiss preference save error"
    />
  );
}
```

Add `mih={44}` to Save. Do not disable form controls during a failed state; only the existing mutation
pending state governs Save loading.

- [ ] **Step 5: Run focused GREEN and settings matrix regressions**

Run:

```bash
npm --prefix apps/web test -- src/features/notifications/NotificationSettingsPage.test.tsx src/features/notifications/hooks.test.tsx
```

Expected: PASS for load, cadence, timezone, quiet-hours, exact Retry, and new edit replacement behavior.

- [ ] **Step 6: Format, inspect, and commit Task 5**

Run:

```bash
cd apps/web
npx prettier --write src/features/notifications/NotificationSettingsPage.tsx src/features/notifications/NotificationSettingsPage.test.tsx
npx eslint src/features/notifications/NotificationSettingsPage.tsx src/features/notifications/NotificationSettingsPage.test.tsx
npx tsc --noEmit
cd ../..
git diff --check
git diff -- apps/web/src/features/notifications/NotificationSettingsPage.tsx apps/web/src/features/notifications/NotificationSettingsPage.test.tsx
git add apps/web/src/features/notifications/NotificationSettingsPage.tsx apps/web/src/features/notifications/NotificationSettingsPage.test.tsx
git commit -m "feat: preserve notification preference retry intent"
```

---

### Task 6: Executable API repeat-safety proof

**Files:**

- Modify: `apps/api/tests/integration/test_notification_api.py`
- Modify: `apps/api/tests/integration/test_notification_preferences_api.py`

**Interfaces:**

- Consumes: existing notification API fixtures, caller-scoped auth helpers, and current production endpoints.
- Produces: explicit repeated-write evidence authorizing Retry for these three operations.
- Invariant: no production API, OpenAPI, model, schema, permission, or migration file changes.

- [ ] **Step 1: Add mark-one repeat characterization**

Seed one notification for one user, POST the same read endpoint twice, assert both status 200, then read
the row and assert `read_at is not None`. Do not assert timestamp identity because effective read state,
not its last-write timestamp, is the retry contract.

- [ ] **Step 2: Add mark-all repeat and isolation characterization**

Seed two unread rows for user A and one for user B. POST A's read-all endpoint twice. Assert the first
response reports 2, the second reports 0, A's rows are read, and B's row remains unread.

- [ ] **Step 3: Add preference partial-PUT repeat characterization**

In the full preference API suite, send this body twice:

```python
body = {
    "digest_modes": {"action_required": "immediate"},
    "digest_hour": 6,
}
```

Assert both responses have the same effective preference view, the final GET matches it, and absent
fields such as `awareness`, `timezone`, and quiet hours retain their defaults.

- [ ] **Step 4: Run the focused integration proof**

Run on a Docker-capable host:

```bash
cd apps/api
uv run pytest tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py -m integration -q
```

Expected: PASS. These are characterization proofs of existing production behavior, so unlike the UI
tasks they are expected to be green when first added. If Docker/testcontainers is unavailable, record
the exact environment failure and do not describe the proof as passed.

- [ ] **Step 5: Run API static guards**

Run:

```bash
cd apps/api
uv run ruff format --check tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py
uv run ruff check tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py
uv run mypy src
cd ../..
git diff --check
```

Expected: PASS.

- [ ] **Step 6: Inspect and commit Task 6**

Run:

```bash
git diff -- apps/api/tests/integration/test_notification_api.py apps/api/tests/integration/test_notification_preferences_api.py
git add apps/api/tests/integration/test_notification_api.py apps/api/tests/integration/test_notification_preferences_api.py
git commit -m "test: prove notification mutation repeat safety"
```

---

### Task 7: Integrated verification, independent review, and evidence docs

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Modify if review requires clarification: `docs/superpowers/specs/2026-08-10-s-mutation-feedback-design.md`
- Modify if implementation drift requires correction: `docs/superpowers/plans/2026-08-10-s-mutation-feedback.md`

**Interfaces:**

- Consumes: Tasks 1–6, repository authority guards, current status/history conventions.
- Produces: fresh closure evidence and a review-ready branch.
- Invariant: current-status records only fresh final baseline facts; slice-history preserves failed and superseded evidence honestly.

- [ ] **Step 1: Run the complete affected web group**

Run:

```bash
npm --prefix apps/web test -- src/lib/states.test.tsx src/lib/mutationFeedback.test.tsx src/test/render.test.tsx src/App.test.tsx src/app/shell/AppShell.test.tsx src/lib/routeChrome.test.tsx src/features/notifications/hooks.test.tsx src/features/notifications/NotificationItem.test.tsx src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.test.tsx src/features/notifications/NotificationSettingsPage.test.tsx
```

Record file count, test count, duration, warnings, and exit status.

- [ ] **Step 2: Run web static and build gates**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build
cd apps/web
npx prettier --check src/lib/states.tsx src/lib/states.test.tsx src/lib/mutationFeedback.tsx src/lib/mutationFeedback.test.tsx src/test/render.tsx src/App.tsx src/App.test.tsx src/app/shell/AppShell.tsx src/features/notifications/mutations.ts src/features/notifications/hooks.test.tsx src/features/notifications/NotificationItem.tsx src/features/notifications/NotificationItem.test.tsx src/features/notifications/NotificationBell.tsx src/features/notifications/NotificationBell.test.tsx src/features/notifications/NotificationsPage.tsx src/features/notifications/NotificationsPage.test.tsx src/features/notifications/NotificationSettingsPage.tsx src/features/notifications/NotificationSettingsPage.test.tsx
cd ../..
```

Expected: PASS. Record the existing Vite chunk advisory separately if it remains.

- [ ] **Step 3: Run the full web suite as a durable job if its expected duration exceeds 60 seconds**

Use the Codex Process Jobs start workflow for:

```bash
npm --prefix apps/web run test
```

After its completion notification, retrieve the bounded final result. Record exact file/test totals,
duration, warnings, unhandled errors, and exit status. Do not poll the job in its launch turn.

- [ ] **Step 4: Run final API evidence**

Run:

```bash
cd apps/api
uv run ruff format --check tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py
uv run ruff check tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py
uv run mypy src
uv run pytest tests/integration/test_notification_api.py tests/integration/test_notification_preferences_api.py -m integration -q
cd ../..
```

Expected: PASS on a Docker-capable contributor environment.

- [ ] **Step 5: Run authority, compatibility, site-data, formatting, and diff guards**

Run:

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
./scripts/check-no-site-data.sh
/tmp/EasySynQ-auth-startup-boundary/apps/web/node_modules/.bin/prettier --check docs/superpowers/specs/2026-08-10-s-mutation-feedback-design.md docs/superpowers/plans/2026-08-10-s-mutation-feedback.md docs/current-status.md
git diff --check 323c79b..HEAD
git status --short --branch
```

If the external Prettier binary path is unavailable at execution time, use the hydrated worktree's
`apps/web/node_modules/.bin/prettier`. Check `docs/slice-history.md` separately and compare any failure to
the exact baseline file; do not mass-format historical content.

- [ ] **Step 6: Perform requirements and quality review**

Invoke `superpowers:requesting-code-review` and `codex-engineering-guardrails:code-verification`. Review
the complete range `323c79b..HEAD` against every design acceptance criterion. Classify findings by
Critical/Important/Minor, fix every in-scope material finding with a focused failing proof, rerun affected
checks, and repeat review until no Critical or Important finding remains.

- [ ] **Step 7: Update current status and slice history from final evidence**

In `docs/current-status.md`, update:

- `last_shipped_slice` to `S-mutation-feedback` only at final handoff readiness;
- `baseline_commit` to the final implementation baseline used by the complete suite;
- web file/test totals and date from the fresh full run; and
- a concise shipped summary plus explicit Fedora/browser limitations.

Append a dated `S-mutation-feedback` entry to `docs/slice-history.md` that records:

- the three observable mutation feedback outcomes;
- the chosen local-versus-persistent architecture;
- exact safe-retry classification and repeat-safety evidence;
- RED/GREEN and final command results;
- review findings and fixes;
- compatibility decisions and unchanged API/schema boundaries; and
- every skipped or unavailable proof without converting it into a pass.

- [ ] **Step 8: Format, guard, and commit evidence docs**

Run the scoped documentation Prettier and authority/site-data/diff guards again, then:

```bash
git add docs/current-status.md docs/slice-history.md docs/superpowers/specs/2026-08-10-s-mutation-feedback-design.md docs/superpowers/plans/2026-08-10-s-mutation-feedback.md
git commit -m "docs: record mutation feedback evidence"
git status --short --branch
git log --oneline --decorate 323c79b..HEAD
```

Expected: clean worktree, intentionally scoped commits, no push or PR until the owner chooses publication.

## Plan self-review

- **Spec coverage:** Tasks 1–5 cover shared/local/persistent feedback and intent retention; Task 6 covers all three server repeat proofs; Task 7 covers every required gate, review, and evidence update.
- **Boundary coverage:** No task changes API production code, OpenAPI, generated output, database schema, migration, auth/setup, URL state, responsive data views, or Playwright.
- **Type consistency:** `MutationFeedbackInput`, `isRetryableMutationError`, `MutationFeedbackProvider`, `useMutationFeedback`, `MutationFeedbackOutlet`, `markNotificationRead`, `useMarkRead`, and `useMarkReadOnOpen` have one spelling and ownership throughout the plan.
- **Lifetime consistency:** the provider surrounds only the operational route table; the outlet sits inside AppShell and outside route-content recovery; direct component tests receive a fresh provider from `renderWithProviders`.
- **Retry consistency:** callers opt in only for the three proven operations; the shared status classifier is necessary but never sufficient by itself; no automatic retry exists.
- **Placeholder scan:** the plan contains no deferred implementation markers; commands, file paths, interfaces, expected failures, and expected passes are explicit.
