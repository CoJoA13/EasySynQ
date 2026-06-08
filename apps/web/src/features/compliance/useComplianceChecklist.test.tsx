import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { useComplianceChecklist } from "./useComplianceChecklist";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("returns the checklist rollup + rows on success", async () => {
  const { result } = renderHook(() => useComplianceChecklist(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.rollup.total).toBe(3);
  expect(result.current.data?.rows).toHaveLength(3);
  expect(result.current.forbidden).toBe(false);
});

test("flags forbidden on a 403 (caller lacks report.compliance_checklist.read)", async () => {
  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useComplianceChecklist(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
