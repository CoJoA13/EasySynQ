import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { PeriodicReviewContext } from "./PeriodicReviewContext";

// ⚠ Deliberately a PRODUCTION-DEFAULT QueryClient (NO retry:false) — the global test wrapper's
// retry:false would mask a retry regression on the expected-403 path (the diff-critic catch):
// without the hook-level retry:false, the calm panel only appears after 3 backoff retries that
// re-hammer a deterministic deny.
function prodDefaultsWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>
          <MemoryRouter>{children}</MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

describe("PeriodicReviewContext under production query defaults", () => {
  test("an expected 403 is requested exactly once — no retry storm before the calm panel", async () => {
    let hits = 0;
    server.use(
      http.get("/api/v1/documents/:id", () => {
        hits += 1;
        return HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 });
      }),
    );
    render(<PeriodicReviewContext documentId="11111111-1111-1111-1111-111111111111" />, {
      wrapper: prodDefaultsWrapper,
    });
    expect(await screen.findByText("Document details not visible to you")).toBeInTheDocument();
    expect(hits).toBe(1);
  });
});
