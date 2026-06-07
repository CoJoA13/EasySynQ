import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { buildDocumentsQuery, useDocuments } from "./useDocuments";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useDocuments returns the {data, page} envelope", async () => {
  const { result } = renderHook(() => useDocuments({}, { limit: 50, offset: 0 }), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.data).toHaveLength(2);
  expect(result.current.data?.data[0]?.identifier).toBe("SOP-PUR-014");
  expect(result.current.data?.page.has_more).toBe(false);
});

test("buildDocumentsQuery emits the bracketed filter grammar and percent-encodes timestamps", () => {
  const qs = buildDocumentsQuery(
    {
      current_state: "Effective",
      document_type: "t1",
      owner_user_id: "u1",
      clause: "8.4",
      effective_from_gte: "2026-01-01T00:00:00+00:00",
    },
    { limit: 25, offset: 25 },
  );
  const p = new URLSearchParams(qs);
  expect(p.get("limit")).toBe("25");
  expect(p.get("offset")).toBe("25");
  expect(p.get("filter[current_state][eq]")).toBe("Effective");
  expect(p.get("filter[document_type][eq]")).toBe("t1");
  expect(p.get("filter[owner_user_id][eq]")).toBe("u1");
  expect(p.get("filter[clause_refs][has]")).toBe("8.4");
  expect(p.get("filter[effective_from][gte]")).toBe("2026-01-01T00:00:00+00:00");
  // The "+" is percent-encoded (%2B), so the server won't decode it as a space.
  expect(qs).toContain("%2B");
});

test("buildDocumentsQuery omits absent facets", () => {
  const qs = buildDocumentsQuery({}, { limit: 50, offset: 0 });
  expect(qs).toBe("limit=50&offset=0");
});
