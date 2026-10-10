import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, it } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { flushTestQueryNotifications } from "../../test/queryNotifications";
import { usePermissions } from "./usePermissions";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("can(key) is true only for an ALLOW grant", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [
          { key: "document.create", effect: "ALLOW", source: "role:Author" },
          { key: "document.release", effect: "DENY", source: "user_override" },
        ],
      }),
    ),
  );
  const { result } = renderHook(() => usePermissions(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.can("document.create")).toBe(true);
  expect(result.current.can("document.release")).toBe(false); // DENY → not allowed
  expect(result.current.can("document.edit")).toBe(false); // absent → not allowed
});

it("passes scope params through to the query", async () => {
  let seenUrl = "";
  server.use(
    http.get("/api/v1/me/permissions", ({ request }) => {
      seenUrl = request.url;
      return HttpResponse.json({ scope: { level: "DOC_CLASS", selector: null }, permissions: [] });
    }),
  );
  const { result } = renderHook(() => usePermissions({ level: "DOC_CLASS", id: "L2_PROCEDURE" }), {
    wrapper,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(seenUrl).toContain("scope_level=DOC_CLASS");
  expect(seenUrl).toContain("scope_id=L2_PROCEDURE");
});

it.each([
  { name: "explicit true", tier: true, expected: true },
  { name: "explicit false", tier: false, expected: false },
  { name: "missing tier", tier: undefined, expected: false },
])("isSystemTier requires a successful explicit true server answer: $name", async ({ tier, expected }) => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "permission.grant", effect: "ALLOW", source: "role:QMS Owner" }],
        ...(tier === undefined ? {} : { system_tier: tier }),
      }),
    ),
  );
  const { result } = renderHook(() => usePermissions(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.can("permission.grant")).toBe(true);
  expect(result.current).toHaveProperty("isSystemTier", expected);
});

it("isSystemTier is false while loading", async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requested = false;
  server.use(
    http.get("/api/v1/me/permissions", async () => {
      requested = true;
      await gate;
      return HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [],
        system_tier: true,
      });
    }),
  );
  const { result } = renderHook(() => usePermissions(), { wrapper });
  try {
    await waitFor(() => expect(requested).toBe(true));
    expect(result.current.isPending).toBe(true);
    expect(result.current).toHaveProperty("isSystemTier", false);
  } finally {
    await act(async () => release?.());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  }
  expect(result.current).toHaveProperty("isSystemTier", true);
});

it("isSystemTier becomes false after a failed refresh with cached true data", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function cachedWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
  }
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "user.create", effect: "ALLOW", source: "user_override" }],
        system_tier: true,
      }),
    ),
  );
  const { result } = renderHook(() => usePermissions(), { wrapper: cachedWrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({ code: "unavailable", title: "Unavailable" }, { status: 503 }),
    ),
  );

  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["me-permissions"] });
    await flushTestQueryNotifications();
  });
  await waitFor(() => expect(result.current.isError).toBe(true));

  expect(result.current.data).toMatchObject({ system_tier: true });
  expect(result.current).toHaveProperty("isSystemTier", false);
});
