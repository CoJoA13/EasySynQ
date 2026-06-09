// apps/web/src/features/capa/mutations.test.tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import {
  useCapaClose,
  useCapaContainment,
  useCreateComplaint,
  useCreateNcr,
  useLinkEvidence,
  useNcrDisposition,
  useRaiseCapa,
  useSpawnCapa,
} from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useRaiseCapa POSTs and resolves the created CAPA", async () => {
  const { result } = renderHook(() => useRaiseCapa(), { wrapper });
  const capa = await result.current.mutateAsync({ title: "New NC", severity: "Minor" });
  expect(capa.id).toBeDefined();
});

test("useCapaContainment POSTs the content_block for a CAPA", async () => {
  const { result } = renderHook(() => useCapaContainment("ca000001-0001-0001-0001-000000000001"), {
    wrapper,
  });
  await result.current.mutateAsync({ content_block: { correction: "froze POs" } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useCapaClose POSTs with no body", async () => {
  const { result } = renderHook(() => useCapaClose("ca000008-0008-0008-0008-000000000008"), { wrapper });
  await result.current.mutateAsync();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useLinkEvidence POSTs an evidence-link to a capa_stage", async () => {
  const { result } = renderHook(() => useLinkEvidence("ca000008-0008-0008-0008-000000000008"), { wrapper });
  await result.current.mutateAsync({
    recordId: "re000001-0001-0001-0001-000000000001",
    targetId: "cr000002-0002-0002-0002-000000000002",
    linkReason: "PM schedule",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useCreateComplaint POSTs /complaints", async () => {
  const { result } = renderHook(() => useCreateComplaint(), { wrapper });
  const c = await result.current.mutateAsync({ description: "missing CoA" });
  expect(c.id).toBeDefined();
});

test("useSpawnCapa POSTs to the complaint's spawn-capa path", async () => {
  const { result } = renderHook(() => useSpawnCapa(), { wrapper });
  const capa = await result.current.mutateAsync({
    complaintId: "cm000001-0001-0001-0001-000000000001",
    severity: "Critical",
  });
  expect(capa.id).toBeDefined();
});

test("useCreateNcr POSTs /ncrs", async () => {
  const { result } = renderHook(() => useCreateNcr(), { wrapper });
  const n = await result.current.mutateAsync({ source: "process", description: "out of spec", severity: "Major" });
  expect(n.id).toBeDefined();
});

test("useNcrDisposition PATCHes the NCR's disposition path", async () => {
  const { result } = renderHook(
    () => useNcrDisposition("nc000001-0001-0001-0001-000000000001"),
    { wrapper },
  );
  await result.current.mutateAsync({ disposition: "rework", notes: "re-inspected" });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});
