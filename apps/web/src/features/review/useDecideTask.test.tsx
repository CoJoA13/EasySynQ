import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { useDecideTask } from "./hooks";

it("DCR decision invalidates the dcr caches, not capa", async () => {
  server.use(
    http.post("/api/v1/tasks/:id/decision", () =>
      HttpResponse.json({
        task_id: "t",
        instance_id: "i",
        stage_key: "s",
        outcome: "approve",
        decided_at: null,
        decided_by: "u",
        signature_event: null,
        comment: null,
      }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
  }
  const { result } = renderHook(() => useDecideTask(), { wrapper: Wrapper });
  await result.current.mutateAsync({
    taskId: "task-dcr-1",
    subjectType: "DCR",
    subjectId: "dcr-1",
    idempotencyKey: "idem-1",
    body: { outcome: "approve" },
  });
  await waitFor(() => {
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["dcr", "dcr-1"]));
    expect(keys).toContain(JSON.stringify(["dcrs"]));
    expect(keys).not.toContain(JSON.stringify(["capa", "dcr-1"]));
  });
});

it("LEADERSHIP_AUTHORIZATION decision invalidates the leadership-authorization + doc caches, not capa", async () => {
  server.use(
    http.post("/api/v1/tasks/:id/decision", () =>
      HttpResponse.json({
        task_id: "t",
        instance_id: "i",
        stage_key: "s",
        outcome: "verify",
        decided_at: null,
        decided_by: "u",
        signature_event: null,
        comment: null,
      }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
  }
  const { result } = renderHook(() => useDecideTask(), { wrapper: Wrapper });
  await result.current.mutateAsync({
    taskId: "task-lead-1",
    subjectType: "LEADERSHIP_AUTHORIZATION",
    subjectId: "doc-1",
    idempotencyKey: "idem-2",
    body: { outcome: "verify" },
  });
  await waitFor(() => {
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["leadership-authorization", "doc-1"]));
    expect(keys).toContain(JSON.stringify(["document", "doc-1"]));
    expect(keys).toContain(JSON.stringify(["objective", "doc-1"]));
    expect(keys).toContain(JSON.stringify(["management-review", "doc-1"]));
    expect(keys).toContain(JSON.stringify(["my-tasks"]));
    expect(keys).not.toContain(JSON.stringify(["capa", "doc-1"]));
  });
});

it("DOCUMENT decision (re-approval) invalidates the version-scoped leadership-authorization status (CX-3)", async () => {
  server.use(
    http.post("/api/v1/tasks/:id/decision", () =>
      HttpResponse.json({
        task_id: "t",
        instance_id: "i",
        stage_key: "s",
        outcome: "approve",
        decided_at: null,
        decided_by: "u",
        signature_event: null,
        comment: null,
      }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
  }
  const { result } = renderHook(() => useDecideTask(), { wrapper: Wrapper });
  await result.current.mutateAsync({
    taskId: "task-doc-1",
    subjectType: "DOCUMENT",
    subjectId: "doc-9",
    idempotencyKey: "idem-doc",
    body: { outcome: "approve" },
  });
  await waitFor(() => {
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["leadership-authorization", "doc-9"]));
    expect(keys).toContain(JSON.stringify(["document", "doc-9"]));
  });
});
