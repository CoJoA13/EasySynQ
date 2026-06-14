import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrImpactList } from "../../lib/types";
import { useAnnotateImpact } from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>
          <MemoryRouter>{children}</MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

it("useAnnotateImpact PUTs the annotations to /dcrs/{id}/impact", async () => {
  let method = "";
  let body: unknown = null;
  const refreshed = {
    data: [
      {
        id: "i1",
        dimension: "affected_processes",
        auto_populated: null,
        requester_annotation: "Diego to re-validate",
        created_at: "2026-06-10T10:00:00+00:00",
        updated_at: "2026-06-11T10:00:00+00:00",
      },
    ],
  } satisfies DcrImpactList;
  server.use(
    http.put("/api/v1/dcrs/:id/impact", async ({ request }) => {
      method = request.method;
      body = await request.json();
      return HttpResponse.json(refreshed);
    }),
  );
  const { result } = renderHook(() => useAnnotateImpact("dcr1"), { wrapper });
  result.current.mutate({ affected_processes: "Diego to re-validate" });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(method).toBe("PUT");
  expect(body).toEqual({ annotations: { affected_processes: "Diego to re-validate" } });
});
