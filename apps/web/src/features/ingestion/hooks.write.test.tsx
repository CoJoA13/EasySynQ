import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { useBulkDecision, useCommitRun, useCreateImportRun, useMerge } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}
const RID = ingestionRunFixture.id;

test("useBulkDecision sends the body + an Idempotency-Key header", async () => {
  let seenKey: string | null = null;
  let seenBody: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/decisions", async ({ request }) => {
      seenKey = request.headers.get("Idempotency-Key");
      seenBody = await request.json();
      return HttpResponse.json({ applied: 2 });
    }),
  );
  const { result } = renderHook(() => useBulkDecision(RID), { wrapper });
  result.current.mutate({
    body: { action: "accept", selector: { band: "HIGH" } },
    idempotencyKey: "key-1",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(seenKey).toBe("key-1");
  expect(seenBody).toEqual({ action: "accept", selector: { band: "HIGH" } });
});

test("useMerge posts file_ids + the effective member", async () => {
  let body: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  const { result } = renderHook(() => useMerge(RID), { wrapper });
  result.current.mutate({
    body: { file_ids: ["a", "b"], effective_file_id: "a", reconstruct_revision_chain: true },
    idempotencyKey: "m-1",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(body).toEqual({ file_ids: ["a", "b"], effective_file_id: "a", reconstruct_revision_chain: true });
});

test("useCreateImportRun returns the created run", async () => {
  const { result } = renderHook(() => useCreateImportRun(), { wrapper });
  result.current.mutate({ source_root: "/srv/import/x", ocr_enabled: true });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Created");
});

test("useCommitRun posts to the commit verb", async () => {
  const { result } = renderHook(() => useCommitRun(RID), { wrapper });
  result.current.mutate();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Committing");
});


afterEach(() => {
  vi.useRealTimers();
});

test("whole-run checklist/run invalidations coalesce; row-scoped ones stay immediate (C9)", async () => {
  // The checklist + run refetches fold/read the WHOLE run server-side — two rapid decisions must
  // trigger ONE trailing refetch of those families, while the (page-scoped) files listing
  // invalidates per decision.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(client, "invalidateQueries");
  function coalesceWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
  }
  server.use(
    http.post("/api/v1/admin/imports/:id/decisions", () => HttpResponse.json({ applied: 1 })),
  );
  const { result } = renderHook(() => useBulkDecision(RID), { wrapper: coalesceWrapper });
  const calls = (family: string) =>
    spy.mock.calls.filter((c) => (c[0]?.queryKey as unknown[] | undefined)?.[0] === family).length;

  result.current.mutate({ body: { action: "defer", file_ids: ["f-1"] }, idempotencyKey: "c9-1" });
  await waitFor(() => expect(calls("import-files")).toBe(1));
  result.current.mutate({ body: { action: "defer", file_ids: ["f-2"] }, idempotencyKey: "c9-2" });
  await waitFor(() => expect(calls("import-files")).toBe(2));

  // Whole-run families have not refetched yet — the trailing window is still open.
  expect(calls("import-checklist")).toBe(0);
  expect(calls("import-run")).toBe(0);

  await vi.advanceTimersByTimeAsync(1100);
  expect(calls("import-checklist")).toBe(1); // coalesced: one refetch for two decisions
  expect(calls("import-run")).toBe(1);
});
