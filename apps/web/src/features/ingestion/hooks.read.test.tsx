import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import {
  useChecklist,
  useDupeClusters,
  useImportFiles,
  useImportRun,
  useImportRuns,
  useVersionFamilies,
} from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}
const RID = ingestionRunFixture.id;

test("useImportRuns returns the run list", async () => {
  const { result } = renderHook(() => useImportRuns(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.[0]?.id).toBe(RID);
});

test("useImportRun returns one run", async () => {
  const { result } = renderHook(() => useImportRun(RID), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Proposed");
});

test("useImportFiles applies the queue→filter mapping (band=HIGH returns the 2 high rows)", async () => {
  const { result } = renderHook(() => useImportFiles(RID, { band: "HIGH" }, 0), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.files).toHaveLength(2);
});

test("useChecklist returns the gate shape", async () => {
  const { result } = renderHook(() => useChecklist(RID), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.ready).toBe(false);
  expect(result.current.data?.blocking).toHaveLength(1);
  expect(result.current.data?.review.commit_ready).toBe(1);
});

test("useDupeClusters + useVersionFamilies return their lists", async () => {
  const clusters = renderHook(() => useDupeClusters(RID), { wrapper });
  await waitFor(() => expect(clusters.result.current.isSuccess).toBe(true));
  expect(clusters.result.current.data?.clusters).toHaveLength(1);
  const families = renderHook(() => useVersionFamilies(RID), { wrapper });
  await waitFor(() => expect(families.result.current.isSuccess).toBe(true));
  expect(families.result.current.data?.families).toHaveLength(1);
});
