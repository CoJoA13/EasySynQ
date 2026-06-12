import { expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { useEffectivePolicy, useObjectiveApproval } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useObjectiveApproval returns the instance + APPROVE task", async () => {
  const { result } = renderHook(
    () => useObjectiveApproval("ob000001-0001-0001-0001-000000000001"),
    { wrapper },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.subject_type).toBe("DOCUMENT");
  expect(result.current.data?.tasks?.some((t) => t.type === "APPROVE")).toBe(true);
});

it("useObjectiveApproval sets forbidden on a 403", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/approval", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(
    () => useObjectiveApproval("ob000001-0001-0001-0001-000000000001"),
    { wrapper },
  );
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.forbidden).toBe(true);
});

it("useEffectivePolicy returns the policy", async () => {
  const { result } = renderHook(() => useEffectivePolicy(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.identifier).toBe("POL-001");
});

it("useEffectivePolicy surfaces null calmly when no policy is effective", async () => {
  server.use(http.get("/api/v1/objectives/policy", () => HttpResponse.json(null)));
  const { result } = renderHook(() => useEffectivePolicy(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data).toBeNull();
});
