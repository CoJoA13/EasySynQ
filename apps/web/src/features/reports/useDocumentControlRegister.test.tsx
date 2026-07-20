import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { DocumentControlRegister } from "../../lib/types";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { useDocumentControlRegister } from "./useDocumentControlRegister";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

const SAMPLE = {
  provenance: {
    report_name: "Controlled Document Register",
    generated_by: "Mara",
    generated_at: "2026-07-19T12:00:00+00:00",
    as_of: "2026-07-19T12:00:00+00:00",
    scope: "org:DEFAULT",
    app_version: "0.1.0",
    filters: {},
    row_count: 1,
    content_hash: "sha256:abc",
  },
  rows: [
    {
      id: "1",
      identifier: "SOP-QA-001",
      title: "Doc Control",
      document_type_id: null,
      document_type: "SOP",
      current_state: "Effective",
      owner_user_id: "u1",
      owner_display: "Priya",
      effective_revision_label: "Rev A",
      effective_from: "2026-06-01T00:00:00+00:00",
      blob_sha256: "deadbeef",
      clause_refs: [{ clause: "7.5.3", starred: true }],
      process_links: [],
      approved_by: "Ken",
      approved_on: "2026-06-01T00:00:00+00:00",
      next_review_due: null,
      review_state: null,
    },
  ],
} satisfies DocumentControlRegister;

test("returns the register on success", async () => {
  server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(SAMPLE)));
  const { result } = renderHook(() => useDocumentControlRegister(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.rows[0]?.identifier).toBe("SOP-QA-001");
  expect(result.current.forbidden).toBe(false);
});

test("flags forbidden on a 403 (caller lacks report.read)", async () => {
  server.use(
    http.get("/api/v1/reports/document-control", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useDocumentControlRegister(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
