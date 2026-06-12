import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { expect, it } from "vitest";
import type { Task } from "../../lib/types";
import { useMyTasks } from "./hooks";

// A PRODUCTION-defaults QueryClient (retry enabled by default) proves the hook's own retry:false — the
// shared test client hardcodes retry:false, so it would mask a missing retry:false (the S-web-8 trap).
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

const taskFixture: Task[] = [
  { id: "t1", instance_id: "i1", stage_key: "review", type: "REVIEW", state: "PENDING",
    assignee_user_id: null, candidate_pool: null, action_expected: "Review", due_at: "2026-06-13T00:00:00+00:00" },
];

it("useMyTasks reads the self-scoped pending tasks", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json(taskFixture)));
  const { result } = renderHook(() => useMyTasks(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data).toHaveLength(1);
  expect(result.current.forbidden).toBe(false);
});

it("useMyTasks surfaces a forbidden flag on 403 without retrying", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })));
  const { result } = renderHook(() => useMyTasks(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
