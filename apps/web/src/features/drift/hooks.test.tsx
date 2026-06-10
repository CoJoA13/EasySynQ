import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { driftStatusFixture } from "../../test/msw/handlers";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { SUPERSEDED_PAGE_SIZE, useDriftStatus, useSupersededCopies } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

describe("drift hooks", () => {
  test("useDriftStatus returns the status snapshot", async () => {
    const { result } = renderHook(() => useDriftStatus(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(driftStatusFixture);
    expect(result.current.forbidden).toBe(false);
  });

  test("useDriftStatus flags a 403 as forbidden", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    const { result } = renderHook(() => useDriftStatus(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.forbidden).toBe(true);
  });

  test("useSupersededCopies sends limit + offset to the server", async () => {
    let seen: string | null = null;
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ total: { versions: 0, copies: 0 }, items: [] });
      }),
    );
    const { result } = renderHook(() => useSupersededCopies(SUPERSEDED_PAGE_SIZE), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(seen).toBe(`?limit=${SUPERSEDED_PAGE_SIZE}&offset=${SUPERSEDED_PAGE_SIZE}`);
  });
});
