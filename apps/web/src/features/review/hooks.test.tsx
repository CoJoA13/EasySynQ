import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useDecideTask, useTasks } from "./hooks";

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

test("useTasks lists the caller's pending tasks", async () => {
  const { result } = renderHook(() => useTasks({ state: "PENDING" }), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.[0]?.type).toBe("APPROVE");
});

test("useDecideTask posts a decision with an Idempotency-Key header", async () => {
  let sentKey: string | null = null;
  server.use(
    http.post("/api/v1/tasks/:id/decision", ({ request }) => {
      sentKey = request.headers.get("Idempotency-Key");
      return HttpResponse.json({
        task_id: "task1111-1111-1111-1111-111111111111",
        instance_id: "wf111111-1111-1111-1111-111111111111",
        stage_key: "quality_approval",
        outcome: "approve",
        decided_at: "2026-06-08T10:00:00+00:00",
        decided_by: "bbbb1111-1111-1111-1111-111111111111",
        signature_event: null,
        comment: null,
      });
    }),
  );
  const { result } = renderHook(() => useDecideTask(), { wrapper });
  await result.current.mutateAsync({
    taskId: "task1111-1111-1111-1111-111111111111",
    documentId: "11111111-1111-1111-1111-111111111111",
    idempotencyKey: "key-123",
    body: { outcome: "approve" },
  });
  expect(sentKey).toBe("key-123");
});
