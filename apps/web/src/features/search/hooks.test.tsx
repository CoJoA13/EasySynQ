import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { useSearch, useSuggest } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useSearch returns the {results, hidden_by_scope} envelope", async () => {
  const { result } = renderHook(() => useSearch("supplier"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.results).toHaveLength(1);
  expect(result.current.data?.results[0]?.identifier).toBe("SOP-PUR-014");
  expect(result.current.data?.hidden_by_scope).toBe(2);
});

test("useSearch is disabled for an empty/whitespace query", () => {
  const { result } = renderHook(() => useSearch("   "), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});

test("useSuggest returns the suggestion list when q is non-empty", async () => {
  const { result } = renderHook(() => useSuggest("sop"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.suggestions).toHaveLength(2);
});

test("useSuggest is disabled for an empty query", () => {
  const { result } = renderHook(() => useSuggest(""), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});
