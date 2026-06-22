# S-notify-fe (SPA notification bell + center) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the slice-1 notification spine in the SPA — a merged TopBar bell with an unread badge, a notification center (popover + `/notifications` page), and a minimal master-email-toggle at `/settings/notifications`.

**Architecture:** A new `apps/web/src/features/notifications/` feature dir (data hooks + mutations + components) consuming the existing authenticated-self R53 endpoints. The TopBar `IconBell` becomes the notification bell (badge = unread count, opens a popover); the standalone ack-count Indicator is retired (the Tasks icon stays the work entry). Deep links (absolute URLs) are relativised and navigated in-app via react-router.

**Tech Stack:** React/TS, Mantine, `@tanstack/react-query`, `react-router-dom`, MSW + vitest + Testing Library + jest-axe.

## Global Constraints

- **FE-only.** No migration (head stays **0063**), no new permission key (catalog stays **102**), no contract change → **`packages/contracts/openapi.yaml` untouched**, no decisions-register entry.
- **Authenticated-self only.** Zero gating beyond "signed in" — **no** `usePermissions` probe, **no** `forbidden`/`NoAccessState` path. Errors are calm (loading / error-with-retry / empty), never no-access.
- **Endpoints (verified against `apps/api/src/easysynq_api/api/notifications.py::_view`):**
  - `GET /api/v1/notifications?unread_only={bool}&limit={int}` → `[{id, event_key, subject_type, subject_id|null, title, body, deep_link, created_at, read_at|null}]` (newest-first; `read_at: null` = unread; `limit` ≤ 200).
  - `POST /api/v1/notifications/{id}/read` → `{status:"ok"}` (404 on a foreign id).
  - `POST /api/v1/notifications/read-all` → `{marked:int}`.
  - `GET /api/v1/me/notification-preferences` → `{email_enabled:bool}` (default true).
  - `PUT /api/v1/me/notification-preferences` → echoes `{email_enabled}`.
- **`deep_link` is an absolute URL** (`app_base_url` + fragment) — relativise to `pathname+search` and navigate via react-router (in-app; no open-redirect). Fall back to `/tasks` on a parse failure.
- **Test discipline (load-bearing):** every test file `import { expect, it } from "vitest"` (jest-dom×vitest trap); MSW fixtures pinned via `satisfies Notification[]`; distinct `aria-label`s (no `getByLabelText` collisions); unread carried by **dot-glyph + "Unread" label + bold weight**, never colour alone; body rendered as a **text node** (never `dangerouslySetInnerHTML`); timestamps deterministic in tests (assert title/body or the absolute `title` attr, not the relative string).
- **No optimistic updates.** Mutations invalidate the `["notifications"]` query-key prefix on success; the 60 s poll backstops.
- Verify before PR: full `/check-web` (eslint + strict `tsc --noEmit` + build + the whole vitest suite).

---

### Task 1: `deepLink.ts` — absolute→relative URL util

**Files:**
- Create: `apps/web/src/features/notifications/deepLink.ts`
- Test: `apps/web/src/features/notifications/deepLink.test.ts`

**Interfaces:**
- Produces: `toRoutePath(deepLink: string): string` — a react-router-navigable `pathname+search`; `/tasks` on any parse failure.

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/features/notifications/deepLink.test.ts
import { describe, expect, it } from "vitest";
import { toRoutePath } from "./deepLink";

describe("toRoutePath", () => {
  it("strips the origin from a document deep link", () => {
    expect(toRoutePath("http://localhost/documents/abc")).toBe("/documents/abc");
  });
  it("preserves the query string for drawer-style links", () => {
    expect(toRoutePath("http://localhost/capa?capa=c1")).toBe("/capa?capa=c1");
    expect(toRoutePath("http://localhost/dcrs?dcr=d1")).toBe("/dcrs?dcr=d1");
    expect(toRoutePath("http://localhost/improvement?initiative=i1")).toBe("/improvement?initiative=i1");
  });
  it("handles the /tasks fallback link and a deployed host", () => {
    expect(toRoutePath("http://localhost/tasks")).toBe("/tasks");
    expect(toRoutePath("https://qms.example.org/management-reviews/m1")).toBe("/management-reviews/m1");
  });
  it("falls back to /tasks on a malformed or empty link", () => {
    expect(toRoutePath("not a url")).toBe("/tasks");
    expect(toRoutePath("")).toBe("/tasks");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/deepLink.test.ts`
Expected: FAIL — `toRoutePath` not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// apps/web/src/features/notifications/deepLink.ts

// Convert a server-built absolute notification deep_link (app_base_url + a route fragment) into a
// react-router-navigable "pathname + search". The link is same-origin and server-trusted, and we
// navigate IN-APP via useNavigate, so there is no open-redirect surface. Any parse failure (or an
// empty path) falls back to /tasks so a malformed/foreign link never throws or leaves a dead click.
export function toRoutePath(deepLink: string): string {
  try {
    const u = new URL(deepLink);
    return (u.pathname || "/tasks") + u.search;
  } catch {
    return "/tasks";
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/deepLink.test.ts`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/notifications/deepLink.ts apps/web/src/features/notifications/deepLink.test.ts
git commit -m "feat(s-notify-fe): deep-link relativiser util"
```

---

### Task 2: Data layer — types, MSW handlers, hooks + mutations

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append the two interfaces)
- Modify: `apps/web/src/test/msw/handlers.ts` (import `Notification`, add fixtures + 5 handlers)
- Create: `apps/web/src/features/notifications/hooks.ts`
- Create: `apps/web/src/features/notifications/mutations.ts`
- Test: `apps/web/src/features/notifications/hooks.test.tsx`

**Interfaces:**
- Consumes: `useApi()` from `lib/api` (`{ get, send }`); `Notification`/`NotificationPreferences` from `lib/types`.
- Produces:
  - `useNotificationCount(): { count: number; isError: boolean; isLoading: boolean }`
  - `useNotifications(scope: "recent" | "all", enabled?: boolean): UseQueryResult<Notification[]>`
  - `useNotificationPreferences(): UseQueryResult<NotificationPreferences>`
  - `useMarkRead()` / `useMarkAllRead()` / `useSetEmailEnabled()` (react-query mutations)
  - MSW export `notificationFixtures` (a `satisfies Notification[]` array, 1 unread + 1 read).

- [ ] **Step 1: Add the types to `lib/types.ts`**

Append:

```ts
// S-notify-fe: the in-app notification + per-user preference shapes (pinned to api/notifications.py::_view).
export interface Notification {
  id: string;
  event_key: string;
  subject_type: string;
  subject_id: string | null;
  title: string;
  body: string;
  deep_link: string;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPreferences {
  email_enabled: boolean;
}
```

- [ ] **Step 2: Add MSW fixtures + handlers to `test/msw/handlers.ts`**

Add `Notification` to the `import type { … } from "../../lib/types";` block. Then add the fixtures (near the other fixtures) and the five handlers (inside the `handlers` array):

```ts
export const notificationFixtures = [
  {
    id: "no000001-0001-0001-0001-000000000001",
    event_key: "task.assigned",
    subject_type: "DOCUMENT",
    subject_id: "d0000001-0001-0001-0001-000000000001",
    title: "Review requested: SOP-001",
    body: "You have been assigned a review task for SOP-001.",
    deep_link: "http://localhost/documents/d0000001-0001-0001-0001-000000000001",
    created_at: "2026-06-22T09:00:00Z",
    read_at: null,
  },
  {
    id: "no000002-0002-0002-0002-000000000002",
    event_key: "task.assigned",
    subject_type: "CAPA",
    subject_id: "ca000002-0002-0002-0002-000000000002",
    title: "CAPA assigned: CAPA-002",
    body: "You own a CAPA stage that needs attention.",
    deep_link: "http://localhost/capa?capa=ca000002-0002-0002-0002-000000000002",
    created_at: "2026-06-21T09:00:00Z",
    read_at: "2026-06-21T10:00:00Z",
  },
] satisfies Notification[];

// (inside the `handlers` array)
http.get("/api/v1/notifications", ({ request }) => {
  const unread = new URL(request.url).searchParams.get("unread_only") === "true";
  const rows = unread
    ? notificationFixtures.filter((n) => n.read_at === null)
    : notificationFixtures;
  return HttpResponse.json(rows);
}),
http.post("/api/v1/notifications/:id/read", () => HttpResponse.json({ status: "ok" })),
http.post("/api/v1/notifications/read-all", () => HttpResponse.json({ marked: 1 })),
http.get("/api/v1/me/notification-preferences", () => HttpResponse.json({ email_enabled: true })),
http.put("/api/v1/me/notification-preferences", async ({ request }) =>
  HttpResponse.json(await request.json()),
),
```

- [ ] **Step 3: Write the failing test**

```tsx
// apps/web/src/features/notifications/hooks.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import {
  useNotificationCount,
  useNotificationPreferences,
  useNotifications,
} from "./hooks";
import { useMarkAllRead, useMarkRead, useSetEmailEnabled } from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

describe("notification data layer", () => {
  it("useNotificationCount counts the unread fixtures", async () => {
    const { result } = renderHook(() => useNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.count).toBe(1); // one unread fixture
    expect(result.current.isError).toBe(false);
  });

  it("useNotificationCount reports isError and never a confident 0 on failure", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    const { result } = renderHook(() => useNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.count).toBe(0); // placeholder consumed only behind the isError guard
  });

  it("useNotifications('all') returns the full list", async () => {
    const { result } = renderHook(() => useNotifications("all"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
  });

  it("useNotifications('recent') is disabled until enabled", async () => {
    const { result } = renderHook(() => useNotifications("recent", false), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("useNotificationPreferences reads the master toggle", async () => {
    const { result } = renderHook(() => useNotificationPreferences(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.email_enabled).toBe(true);
  });

  it("useMarkRead POSTs the id", async () => {
    let marked = "";
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        marked = String(params.id);
        return HttpResponse.json({ status: "ok" });
      }),
    );
    const { result } = renderHook(() => useMarkRead(), { wrapper });
    result.current.mutate("abc-123");
    await waitFor(() => expect(marked).toBe("abc-123"));
  });

  it("useMarkAllRead POSTs read-all", async () => {
    let hit = false;
    server.use(
      http.post("/api/v1/notifications/read-all", () => {
        hit = true;
        return HttpResponse.json({ marked: 3 });
      }),
    );
    const { result } = renderHook(() => useMarkAllRead(), { wrapper });
    result.current.mutate();
    await waitFor(() => expect(hit).toBe(true));
  });

  it("useSetEmailEnabled PUTs the new value", async () => {
    let body: unknown = null;
    server.use(
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body);
      }),
    );
    const { result } = renderHook(() => useSetEmailEnabled(), { wrapper });
    result.current.mutate(false);
    await waitFor(() => expect(body).toEqual({ email_enabled: false }));
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/hooks.test.tsx`
Expected: FAIL — `./hooks` / `./mutations` not found.

- [ ] **Step 5: Write `hooks.ts`**

```ts
// apps/web/src/features/notifications/hooks.ts
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Notification, NotificationPreferences } from "../../lib/types";

// The unread-count badge — the ONLY polled query (60 s). Mirrors useAckCount EXACTLY: it returns the
// count ALONGSIDE isError/isLoading, so the bell reads `count` only behind the isError guard and renders
// an indeterminate state on failure — NEVER a confident 0 (the silent-zero fix). limit=100 caps the
// fetch; the bell shows "99+" when count > 99.
export function useNotificationCount(): { count: number; isError: boolean; isLoading: boolean } {
  const api = useApi();
  const query = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api.get<Notification[]>("/api/v1/notifications?unread_only=true&limit=100"),
    refetchInterval: 60_000,
    retry: false,
  });
  return { count: query.data?.length ?? 0, isError: query.isError, isLoading: query.isLoading };
}

// The center list. "recent" → the popover (15, read+unread); "all" → the page (50, read+unread).
// `enabled` gates the popover fetch on the popover being open.
export function useNotifications(
  scope: "recent" | "all",
  enabled = true,
): UseQueryResult<Notification[]> {
  const api = useApi();
  const limit = scope === "recent" ? 15 : 50;
  return useQuery({
    queryKey: ["notifications", "list", scope],
    queryFn: () => api.get<Notification[]>(`/api/v1/notifications?limit=${limit}`),
    enabled,
    retry: false,
  });
}

export function useNotificationPreferences(): UseQueryResult<NotificationPreferences> {
  const api = useApi();
  return useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => api.get<NotificationPreferences>("/api/v1/me/notification-preferences"),
    retry: false,
  });
}
```

- [ ] **Step 6: Write `mutations.ts`**

```ts
// apps/web/src/features/notifications/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { NotificationPreferences } from "../../lib/types";

// Mark one read. Self-scoped; a 404 (foreign/already-gone id) is fire-and-forget — `.mutate()` does not
// throw to the caller and we navigate regardless; the 60 s poll backstops. onSuccess invalidates the
// ["notifications"] prefix → the badge + every list refresh together.
export function useMarkRead() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.send<{ status: string }>("POST", `/api/v1/notifications/${id}/read`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useMarkAllRead() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.send<{ marked: number }>("POST", "/api/v1/notifications/read-all"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useSetEmailEnabled() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email_enabled: boolean) =>
      api.send<NotificationPreferences>("PUT", "/api/v1/me/notification-preferences", { email_enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });
}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/hooks.test.tsx`
Expected: PASS (all 8).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts apps/web/src/features/notifications/hooks.ts apps/web/src/features/notifications/mutations.ts apps/web/src/features/notifications/hooks.test.tsx
git commit -m "feat(s-notify-fe): notification data layer (hooks, mutations, MSW)"
```

---

### Task 3: `NotificationItem.tsx` — a single notification row

**Files:**
- Create: `apps/web/src/features/notifications/NotificationItem.tsx`
- Test: `apps/web/src/features/notifications/NotificationItem.test.tsx`

**Interfaces:**
- Consumes: `Notification` (`lib/types`), `toRoutePath` (`./deepLink`), `useMarkRead` (`./mutations`), `formatRelativeTime`/`formatTimestamp` (`lib/time`).
- Produces: `NotificationItem({ notification, onNavigate? }: { notification: Notification; onNavigate?: () => void })`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/notifications/NotificationItem.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Notification } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationItem } from "./NotificationItem";

const unread: Notification = {
  id: "n1",
  event_key: "task.assigned",
  subject_type: "DOCUMENT",
  subject_id: "d1",
  title: "Review requested: SOP-001",
  body: "You have a review task.",
  deep_link: "http://localhost/documents/d1",
  created_at: "2026-06-22T09:00:00Z",
  read_at: null,
};

describe("NotificationItem", () => {
  it("marks an unread row with the dot+label and a bold title, and links to the relative path", () => {
    renderWithProviders(<NotificationItem notification={unread} />);
    expect(screen.getByText("Unread")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review requested: SOP-001/ })).toHaveAttribute(
      "href",
      "/documents/d1",
    );
    expect(screen.getByLabelText("Mark read: Review requested: SOP-001")).toBeInTheDocument();
  });

  it("a read row has no unread marker and no mark-read button", () => {
    renderWithProviders(
      <NotificationItem notification={{ ...unread, read_at: "2026-06-22T10:00:00Z" }} />,
    );
    expect(screen.queryByText("Unread")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Mark read:/)).not.toBeInTheDocument();
  });

  it("renders the body as literal text (no HTML injection)", () => {
    renderWithProviders(
      <NotificationItem notification={{ ...unread, body: "<b>x</b><script>alert(1)</script>" }} />,
    );
    expect(screen.getByText("<b>x</b><script>alert(1)</script>")).toBeInTheDocument();
  });

  it("the mark-read button POSTs the id without navigating", async () => {
    let marked = "";
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        marked = String(params.id);
        return HttpResponse.json({ status: "ok" });
      }),
    );
    renderWithProviders(<NotificationItem notification={unread} />);
    await userEvent.click(screen.getByLabelText("Mark read: Review requested: SOP-001"));
    await waitFor(() => expect(marked).toBe("n1"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationItem.test.tsx`
Expected: FAIL — `./NotificationItem` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// apps/web/src/features/notifications/NotificationItem.tsx
import { ActionIcon, Anchor, Box, Group, Stack, Text, VisuallyHidden } from "@mantine/core";
import { Link } from "react-router-dom";
import { formatRelativeTime, formatTimestamp } from "../../lib/time";
import type { Notification } from "../../lib/types";
import { toRoutePath } from "./deepLink";
import { useMarkRead } from "./mutations";

// One notification row. Unread is carried by a dot glyph + a "Unread" screen-reader label + a bold
// title (never colour alone, DP-5). The row is a Link (semantic navigation) whose accessible name is
// computed from its content — including the VisuallyHidden "Unread" — so we deliberately set NO explicit
// aria-label on it (an explicit name would swallow the nested "Unread"). The "Mark read" ActionIcon is a
// SIBLING of the Link (never nested inside it) so there is no nested-interactive markup; its aria-label
// embeds the title for a unique accessible name. Clicking the row marks read + navigates (popover closes
// via onNavigate); the body is rendered as a plain text node (no dangerouslySetInnerHTML).
export function NotificationItem({
  notification,
  onNavigate,
}: {
  notification: Notification;
  onNavigate?: () => void;
}) {
  const markRead = useMarkRead();
  const unread = notification.read_at === null;

  function open() {
    if (unread) markRead.mutate(notification.id);
    onNavigate?.();
  }

  return (
    <Group wrap="nowrap" gap="xs" align="flex-start">
      <Anchor
        component={Link}
        to={toRoutePath(notification.deep_link)}
        onClick={open}
        underline="never"
        c="inherit"
        style={{ flex: 1, minWidth: 0 }}
      >
        <Group wrap="nowrap" gap="xs" align="flex-start">
          {unread && (
            <Box
              w={8}
              h={8}
              mt={6}
              style={{
                background: "var(--mantine-primary-color-filled)",
                borderRadius: "50%",
                flexShrink: 0,
              }}
            >
              <VisuallyHidden>Unread</VisuallyHidden>
            </Box>
          )}
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Text size="sm" fw={unread ? 700 : 400} lineClamp={2}>
              {notification.title}
            </Text>
            {notification.body && (
              <Text size="xs" c="dimmed" lineClamp={2}>
                {notification.body}
              </Text>
            )}
            <Text size="xs" c="dimmed" title={formatTimestamp(notification.created_at)}>
              {formatRelativeTime(notification.created_at)}
            </Text>
          </Stack>
        </Group>
      </Anchor>
      {unread && (
        <ActionIcon
          variant="subtle"
          size="sm"
          aria-label={`Mark read: ${notification.title}`}
          onClick={() => markRead.mutate(notification.id)}
        >
          <svg
            width={16}
            height={16}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path d="M5 12l5 5L20 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </ActionIcon>
      )}
    </Group>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationItem.test.tsx`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/notifications/NotificationItem.tsx apps/web/src/features/notifications/NotificationItem.test.tsx
git commit -m "feat(s-notify-fe): notification row component"
```

---

### Task 4: `NotificationBell.tsx` — the TopBar bell + popover

**Files:**
- Create: `apps/web/src/features/notifications/NotificationBell.tsx`
- Test: `apps/web/src/features/notifications/NotificationBell.test.tsx`

**Interfaces:**
- Consumes: `useNotificationCount`/`useNotifications` (`./hooks`), `useMarkAllRead` (`./mutations`), `NotificationItem`, `IconBell` (`lib/icons`), `InlineState` (`lib/states`).
- Produces: `NotificationBell()` (no props).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/notifications/NotificationBell.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationBell } from "./NotificationBell";

function unreadList(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `u${i}`,
    event_key: "task.assigned",
    subject_type: "DOCUMENT",
    subject_id: `d${i}`,
    title: `Notice ${i}`,
    body: "",
    deep_link: `http://localhost/documents/d${i}`,
    created_at: "2026-06-22T09:00:00Z",
    read_at: null,
  }));
}

describe("NotificationBell", () => {
  it("shows the unread count and names itself with it", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(3))));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications, 3 unread" })).toBeInTheDocument();
  });

  it("caps the badge at 99+", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(100))));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByText("99+")).toBeInTheDocument();
  });

  it("a failed count shows an indeterminate bell — never a confident 0", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<NotificationBell />);
    expect(
      await screen.findByRole("button", { name: "Notifications (count unavailable)" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("a genuine zero is silent", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("opens the popover with the recent list, settings and see-all links", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))));
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("Notice 0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "/notifications");
    expect(screen.getByRole("link", { name: "Notification settings" })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });

  it("mark all read POSTs read-all", async () => {
    let hit = false;
    server.use(
      http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))),
      http.post("/api/v1/notifications/read-all", () => {
        hit = true;
        return HttpResponse.json({ marked: 2 });
      }),
    );
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(hit).toBe(true));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationBell.test.tsx`
Expected: FAIL — `./NotificationBell` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// apps/web/src/features/notifications/NotificationBell.tsx
import {
  ActionIcon,
  Anchor,
  Button,
  Group,
  Indicator,
  Popover,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { IconBell } from "../../lib/icons";
import { InlineState } from "../../lib/states";
import { useNotificationCount, useNotifications } from "./hooks";
import { useMarkAllRead } from "./mutations";
import { NotificationItem } from "./NotificationItem";

// The merged TopBar bell (S-notify-fe). Awareness lives here; the Tasks icon stays the work entry. The
// badge reuses the existing ack-bell's three-state, never-confident-zero pattern: a numeric badge (99+
// past the cap) / a gray indeterminate dot on a failed count / nothing on a true zero. The Indicator is
// the OUTER wrapper so its badge overlays the bell while the Popover anchors to the ActionIcon itself.
export function NotificationBell() {
  const [opened, setOpened] = useState(false);
  const { count, isError } = useNotificationCount();
  const list = useNotifications("recent", opened);
  const markAll = useMarkAllRead();

  const hasCount = !isError && count > 0;
  const label = isError
    ? "Notifications (count unavailable)"
    : count > 0
      ? `Notifications, ${count} unread`
      : "Notifications";
  const badge = count > 99 ? "99+" : count;
  const rows = list.data ?? [];

  return (
    <Indicator
      label={hasCount ? badge : undefined}
      size={isError ? 10 : 16}
      color={isError ? "gray" : undefined}
      disabled={!hasCount && !isError}
    >
      <Popover
        position="bottom-end"
        width={360}
        opened={opened}
        onChange={setOpened}
        withArrow
        shadow="md"
      >
        <Popover.Target>
          <ActionIcon variant="subtle" aria-label={label} onClick={() => setOpened((o) => !o)}>
            <IconBell />
          </ActionIcon>
        </Popover.Target>
        <Popover.Dropdown p="xs">
          <Stack gap="xs">
            <Group justify="space-between" px="xs">
              <Text fw={600} size="sm">
                Notifications
              </Text>
              <Button
                variant="subtle"
                size="compact-xs"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
              >
                Mark all read
              </Button>
            </Group>
            <ScrollArea.Autosize mah={360}>
              {list.isLoading ? (
                <InlineState kind="loading">Loading notifications…</InlineState>
              ) : list.isError ? (
                <InlineState kind="error" onRetry={() => void list.refetch()}>
                  Couldn&apos;t load notifications.
                </InlineState>
              ) : rows.length === 0 ? (
                <InlineState kind="empty">You&apos;re all caught up.</InlineState>
              ) : (
                <Stack gap="xs">
                  {rows.map((n) => (
                    <NotificationItem key={n.id} notification={n} onNavigate={() => setOpened(false)} />
                  ))}
                </Stack>
              )}
            </ScrollArea.Autosize>
            <Group justify="space-between" px="xs">
              <Anchor
                component={Link}
                to="/settings/notifications"
                size="xs"
                onClick={() => setOpened(false)}
              >
                Notification settings
              </Anchor>
              <Anchor component={Link} to="/notifications" size="xs" onClick={() => setOpened(false)}>
                See all
              </Anchor>
            </Group>
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </Indicator>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationBell.test.tsx`
Expected: PASS (all 6). (Note: the global `scrollIntoView` stub in `test/setup.ts` covers the Mantine popover.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/notifications/NotificationBell.tsx apps/web/src/features/notifications/NotificationBell.test.tsx
git commit -m "feat(s-notify-fe): TopBar notification bell + popover"
```

---

### Task 5: `NotificationsPage.tsx` — the full `/notifications` page

**Files:**
- Create: `apps/web/src/features/notifications/NotificationsPage.tsx`
- Test: `apps/web/src/features/notifications/NotificationsPage.test.tsx`

**Interfaces:**
- Consumes: `useNotifications` (`./hooks`), `useMarkAllRead` (`./mutations`), `NotificationItem`, `lib/states` (`LoadingState`/`ErrorState`/`EmptyState`).
- Produces: `NotificationsPage()` (no props).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/notifications/NotificationsPage.test.tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationsPage } from "./NotificationsPage";

describe("NotificationsPage", () => {
  it("lists notifications", async () => {
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("Review requested: SOP-001")).toBeInTheDocument();
    expect(screen.getByText("CAPA assigned: CAPA-002")).toBeInTheDocument();
  });

  it("shows the empty state when there is nothing", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("You're all caught up.")).toBeInTheDocument();
  });

  it("shows a retryable error state on failure", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("Couldn't load notifications")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationsPage.test.tsx`
Expected: FAIL — `./NotificationsPage` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// apps/web/src/features/notifications/NotificationsPage.tsx
import { Button, Container, Group, Stack, Text, Title } from "@mantine/core";
import { EmptyState, ErrorState, LoadingState } from "../../lib/states";
import { useNotifications } from "./hooks";
import { useMarkAllRead } from "./mutations";
import { NotificationItem } from "./NotificationItem";

// The full /notifications history (the popover's "See all"). Server-capped at 50 — a footnote keeps the
// cap honest (no silent truncation). Self-scoped; calm states only (no no-access path).
export function NotificationsPage() {
  const list = useNotifications("all");
  const markAll = useMarkAllRead();
  const rows = list.data ?? [];

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>Notifications</Title>
          <Button
            variant="light"
            size="compact-sm"
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending || rows.length === 0}
          >
            Mark all read
          </Button>
        </Group>
        {list.isLoading ? (
          <LoadingState label="Loading notifications" />
        ) : list.isError ? (
          <ErrorState title="Couldn't load notifications" onRetry={() => void list.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState message="You're all caught up." />
        ) : (
          <Stack gap="sm">
            {rows.map((n) => (
              <NotificationItem key={n.id} notification={n} />
            ))}
            {rows.length >= 50 && (
              <Text size="xs" c="dimmed">
                Showing the 50 most recent notifications.
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationsPage.test.tsx`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/notifications/NotificationsPage.tsx apps/web/src/features/notifications/NotificationsPage.test.tsx
git commit -m "feat(s-notify-fe): /notifications page"
```

---

### Task 6: `NotificationSettingsPage.tsx` — the master email toggle

**Files:**
- Create: `apps/web/src/features/notifications/NotificationSettingsPage.tsx`
- Test: `apps/web/src/features/notifications/NotificationSettingsPage.test.tsx`

**Interfaces:**
- Consumes: `useNotificationPreferences` (`./hooks`), `useSetEmailEnabled` (`./mutations`), `lib/states` (`LoadingState`/`ErrorState`/`MutationErrorState`).
- Produces: `NotificationSettingsPage()` (no props).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/notifications/NotificationSettingsPage.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationSettingsPage } from "./NotificationSettingsPage";

describe("NotificationSettingsPage", () => {
  it("reflects the current email_enabled value", async () => {
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    const sw = await screen.findByRole("switch", { name: "Email notifications" });
    expect(sw).not.toBeChecked();
  });

  it("PUTs the new value when toggled and confirms the save", async () => {
    let body: unknown = null;
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body);
      }),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await userEvent.click(await screen.findByRole("switch", { name: "Email notifications" }));
    await waitFor(() => expect(body).toEqual({ email_enabled: true }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("surfaces a save error", async () => {
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
      http.put("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ code: "boom", title: "Save failed" }, { status: 500 }),
      ),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await userEvent.click(await screen.findByRole("switch", { name: "Email notifications" }));
    expect(await screen.findByText("Couldn't save your preference")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationSettingsPage.test.tsx`
Expected: FAIL — `./NotificationSettingsPage` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// apps/web/src/features/notifications/NotificationSettingsPage.tsx
import { Button, Container, Group, Stack, Switch, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { ErrorState, LoadingState, MutationErrorState } from "../../lib/states";
import { useNotificationPreferences } from "./hooks";
import { useSetEmailEnabled } from "./mutations";

// The minimal per-user master email toggle (S-notify-fe). This is the route the email "Manage
// notifications" link (subjects.py::prefs_link → /settings/notifications) targets. The per-event digest
// matrix + quiet hours are a later release. Self-scoped; no permission gate.
export function NotificationSettingsPage() {
  const prefs = useNotificationPreferences();
  const setEmail = useSetEmailEnabled();

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>Notification settings</Title>
          <Button component={Link} to="/" variant="subtle">
            Back to app
          </Button>
        </Group>
        {prefs.isLoading ? (
          <LoadingState label="Loading preferences" />
        ) : prefs.isError ? (
          <ErrorState title="Couldn't load preferences" onRetry={() => void prefs.refetch()} />
        ) : (
          <Stack gap="sm">
            <Switch
              label="Email notifications"
              description="Receive an email when work is assigned to you. Emails carry only a summary and a link — never controlled content — and require your administrator to enable email delivery for the organisation."
              checked={prefs.data?.email_enabled ?? true}
              onChange={(e) => setEmail.mutate(e.currentTarget.checked)}
              disabled={setEmail.isPending}
            />
            {setEmail.isError && (
              <MutationErrorState title="Couldn't save your preference" error={setEmail.error} />
            )}
            {setEmail.isSuccess && (
              <Text size="sm" c="dimmed">
                Saved.
              </Text>
            )}
            <Text size="xs" c="dimmed">
              More granular per-event preferences and digests are coming in a later release.
            </Text>
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/notifications/NotificationSettingsPage.test.tsx`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/notifications/NotificationSettingsPage.tsx apps/web/src/features/notifications/NotificationSettingsPage.test.tsx
git commit -m "feat(s-notify-fe): notification settings (master email toggle)"
```

---

### Task 7: Shell wiring — TopBar + routes

**Files:**
- Modify: `apps/web/src/app/shell/TopBar.tsx`
- Modify: `apps/web/src/app/shell/TopBar.test.tsx` (rewrite the ack-bell suite)
- Modify: `apps/web/src/App.tsx` (+2 routes + 2 imports)

**Interfaces:**
- Consumes: `NotificationBell` (`features/notifications/NotificationBell`), `NotificationsPage`, `NotificationSettingsPage`.

- [ ] **Step 1: Rewrite `TopBar.test.tsx`**

Replace the whole file:

```tsx
// apps/web/src/app/shell/TopBar.test.tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { TopBar } from "./TopBar";

function renderBar() {
  return renderWithProviders(
    <TopBar navOpened={false} onToggleNav={() => {}} onOpenSearch={() => {}} />,
    { route: "/" },
  );
}

describe("TopBar", () => {
  test("keeps the Tasks work entry with a distinct label", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderBar();
    const tasks = await screen.findByRole("link", { name: "Tasks" });
    expect(tasks).toHaveAttribute("href", "/tasks");
  });

  test("renders the merged notification bell with an unread badge", async () => {
    server.use(
      http.get("/api/v1/notifications", () =>
        HttpResponse.json([
          {
            id: "n1",
            event_key: "task.assigned",
            subject_type: "DOCUMENT",
            subject_id: "d1",
            title: "Review requested",
            body: "",
            deep_link: "http://localhost/documents/d1",
            created_at: "2026-06-22T09:00:00Z",
            read_at: null,
          },
        ]),
      ),
    );
    renderBar();
    expect(await screen.findByText("1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications, 1 unread" })).toBeInTheDocument();
  });

  test("the account menu offers notification settings", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderBar();
    await userEvent.click(await screen.findByRole("button", { name: "Account" }));
    expect(screen.getByRole("menuitem", { name: "Notification settings" })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npx vitest run src/app/shell/TopBar.test.tsx`
Expected: FAIL — TopBar still renders the ack bell (no "Notifications" button / no "Notification settings" menuitem).

- [ ] **Step 3: Rewrite `TopBar.tsx`**

Replace the whole file:

```tsx
// apps/web/src/app/shell/TopBar.tsx
import { ActionIcon, Burger, Button, Group, Menu, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { IconSearch, IconTasks, IconUser } from "../../lib/icons";
import { useAuth } from "../../lib/auth";
import { NotificationBell } from "../../features/notifications/NotificationBell";

// S-notify-fe: the ack-count Indicator is retired — the bell is now the merged NOTIFICATION bell
// (awareness), and the Tasks icon stays the explicit WORK entry. DOC_ACK assignments flow as
// notifications, so the bell's unread badge encompasses new-ack awareness; the durable open-ack work
// count lives at /tasks. (useAckCount is unchanged and still powers Home's DoCard.)
//
// S-web-6: the search box is a real button (not a read-only text input) and renders on every breakpoint;
// it is icon-only below `sm` to keep the no-wrap header from overflowing on ~320px phones.
export function TopBar({
  navOpened,
  onToggleNav,
  onOpenSearch,
}: {
  navOpened: boolean;
  onToggleNav: () => void;
  onOpenSearch: () => void;
}) {
  const { logout } = useAuth();
  return (
    <Group h="100%" px="md" justify="space-between" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Burger
          opened={navOpened}
          onClick={onToggleNav}
          hiddenFrom="md"
          size="sm"
          aria-label="Toggle navigation"
        />
        <Text fw={700}>EasySynQ</Text>
      </Group>
      <Button
        variant="default"
        color="gray"
        fw={400}
        onClick={onOpenSearch}
        aria-label="Search (⌘K)"
      >
        <IconSearch size={16} />
        <Text component="span" c="dimmed" ml={6} visibleFrom="sm">
          Search (⌘K)
        </Text>
      </Button>
      <Group gap="xs" wrap="nowrap">
        <ActionIcon component={Link} to="/tasks" variant="subtle" aria-label="Tasks">
          <IconTasks />
        </ActionIcon>
        <NotificationBell />
        <Menu position="bottom-end">
          <Menu.Target>
            <ActionIcon variant="subtle" aria-label="Account">
              <IconUser />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item component={Link} to="/settings/notifications">
              Notification settings
            </Menu.Item>
            <Menu.Item onClick={logout}>Sign out</Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
    </Group>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npx vitest run src/app/shell/TopBar.test.tsx`
Expected: PASS (all 3).

- [ ] **Step 5: Add the routes in `App.tsx`**

Add the two imports near the other feature imports:

```tsx
import { NotificationsPage } from "./features/notifications/NotificationsPage";
import { NotificationSettingsPage } from "./features/notifications/NotificationSettingsPage";
```

Inside the root `<Route path="/" element={... <AppShell /> ...}>` block, add (e.g. right after the `tasks/:id` route):

```tsx
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="settings/notifications" element={<NotificationSettingsPage />} />
```

- [ ] **Step 6: Verify the build + the touched suites**

Run: `cd apps/web && npx tsc --noEmit && npx vitest run src/app/shell/TopBar.test.tsx src/features/notifications/`
Expected: tsc clean; all notification + TopBar tests PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/shell/TopBar.tsx apps/web/src/app/shell/TopBar.test.tsx apps/web/src/App.tsx
git commit -m "feat(s-notify-fe): wire the bell into TopBar + add /notifications + /settings/notifications routes"
```

---

### Task 8: Full gate + adversarial review

**Files:** none (verification only).

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
(Equivalent to `/check-web`.) Expected: eslint clean, tsc clean (strict `noUncheckedIndexedAccess`), build OK, the whole vitest suite green (no cross-file drift; any other test that mounts the TopBar/AppShell now hits the base `/api/v1/notifications` handler returning the fixtures — confirm none break).

- [ ] **Step 2: Adversarial review**

Run the `web-test-trap-reviewer` and `diff-critic` agents on the branch diff, plus a small 3-lens adversarial Workflow (self-scope/no-extra-gating + never-confident-zero; a11y/colour-safe-unread/XSS-safe-body/deep-link-safety; test-fidelity/determinism). Fold only confirmed findings.

- [ ] **Step 3: Commit any fixes, then hand to the live-smoke + PR flow.**

---

## Self-review notes (author)

- **Spec coverage:** §1 bell→Task 4+7; §2 popover/page/deep-link→Tasks 1,3,4,5; §3 settings→Task 6; §4 polling→Task 2 (`refetchInterval`); §5 files→Tasks 2–7; §6 a11y→Tasks 3,4; §7 tests→every task + Task 8. All covered.
- **Type consistency:** `Notification`/`NotificationPreferences` defined once in `lib/types.ts` (Task 2) and consumed everywhere; hook/mutation names identical across the data-layer test, components, and shell.
- **No placeholders:** every step carries complete code/commands.
