import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useAcknowledgeTask, useBulkAcknowledge } from "./ackHooks";

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

describe("review ack hooks", () => {
  test("useAcknowledgeTask POSTs outcome=acknowledge with the CALLER's Idempotency-Key", async () => {
    let outcome: string | null = null;
    let sentKey: string | null = null;
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        outcome = ((await request.json()) as { outcome: string }).outcome;
        sentKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ document_id: "d", document_version_id: null, acknowledgement_id: "a", replayed: false });
      }),
    );
    const { result } = renderHook(() => useAcknowledgeTask(), { wrapper });
    // The caller owns the key (stable across retries) — the hook must send it verbatim, not mint its own.
    await result.current.mutateAsync({ taskId: "tkak1111-1111-1111-1111-111111111111", documentId: "11111111-1111-1111-1111-111111111111", idempotencyKey: "stable-key-abc" });
    expect(outcome).toBe("acknowledge");
    expect(sentKey).toBe("stable-key-abc");
  });

  test("useBulkAcknowledge reports per-task success/failure (allSettled)", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", ({ params }) => {
        if (params.id === "bad") return HttpResponse.json({ code: "ack_superseded", title: "superseded" }, { status: 409 });
        return HttpResponse.json({ document_id: "d", acknowledgement_id: "a", replayed: false });
      }),
    );
    const { result } = renderHook(() => useBulkAcknowledge(), { wrapper });
    const out = await result.current.run(["ok1", "ok2", "bad"]);
    expect(out.ok).toEqual(["ok1", "ok2"]);
    expect(out.failed).toEqual([{ taskId: "bad", code: "ack_superseded" }]);
  });
});
