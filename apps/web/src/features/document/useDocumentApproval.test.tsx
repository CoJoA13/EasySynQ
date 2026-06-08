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
import { useDocumentApproval } from "./useDocumentApproval";

const DOC = "11111111-1111-1111-1111-111111111111";

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

test("useDocumentApproval returns the instance with tasks", async () => {
  const { result } = renderHook(() => useDocumentApproval(DOC), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.current_state).toBe("IN_APPROVAL");
  expect(result.current.data?.tasks?.[0]?.type).toBe("APPROVE");
});

test("useDocumentApproval returns null when there is no cycle", async () => {
  server.use(http.get("/api/v1/documents/:id/approval", () => HttpResponse.json(null)));
  const { result } = renderHook(() => useDocumentApproval(DOC), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data).toBeNull();
});

test("useDocumentApproval is disabled with no document id", () => {
  const { result } = renderHook(() => useDocumentApproval(null), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});
