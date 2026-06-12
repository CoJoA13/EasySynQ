import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { useObjective, useObjectiveScorecard } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useObjectiveScorecard returns the rollup + rows", async () => {
  const { result } = renderHook(() => useObjectiveScorecard(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.total).toBe(4);
  expect(result.current.data?.by_rag.green).toBe(1);
  expect(result.current.forbidden).toBe(false);
});

it("useObjectiveScorecard sets forbidden on a 403", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useObjectiveScorecard(), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.forbidden).toBe(true);
});

it("useObjective loads a single objective with plans", async () => {
  const { result } = renderHook(() => useObjective("ob000001-0001-0001-0001-000000000001"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.plans).toHaveLength(1);
});
