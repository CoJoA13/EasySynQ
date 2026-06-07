import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, it } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { usePermissions } from "./usePermissions";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("can(key) is true only for an ALLOW grant", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [
          { key: "document.create", effect: "ALLOW", source: "role:Author" },
          { key: "document.release", effect: "DENY", source: "user_override" },
        ],
      }),
    ),
  );
  const { result } = renderHook(() => usePermissions(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.can("document.create")).toBe(true);
  expect(result.current.can("document.release")).toBe(false); // DENY → not allowed
  expect(result.current.can("document.edit")).toBe(false); // absent → not allowed
});

it("passes scope params through to the query", async () => {
  let seenUrl = "";
  server.use(
    http.get("/api/v1/me/permissions", ({ request }) => {
      seenUrl = request.url;
      return HttpResponse.json({ scope: { level: "DOC_CLASS", selector: null }, permissions: [] });
    }),
  );
  const { result } = renderHook(() => usePermissions({ level: "DOC_CLASS", id: "L2_PROCEDURE" }), {
    wrapper,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(seenUrl).toContain("scope_level=DOC_CLASS");
  expect(seenUrl).toContain("scope_id=L2_PROCEDURE");
});
