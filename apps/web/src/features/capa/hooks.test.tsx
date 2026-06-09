import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { useCapa, useCapas } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useCapas returns the {data} rows", async () => {
  const { result } = renderHook(() => useCapas(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.length).toBeGreaterThan(0);
  expect(result.current.forbidden).toBe(false);
});

test("useCapas surfaces a 403 as forbidden (not an error throw)", async () => {
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useCapas(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});

test("useCapa returns the detail with stages", async () => {
  const { result } = renderHook(() => useCapa("ca000001-0001-0001-0001-000000000001"), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.stages!.length).toBeGreaterThan(0);
});

test("useCapa is disabled when id is null", () => {
  const { result } = renderHook(() => useCapa(null), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});
