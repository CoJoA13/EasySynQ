import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { distributionFixture, ackMatrixFixture } from "../../test/msw/handlers";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useDistribution, useAcknowledgements } from "./ackHooks";

const DOC = "11111111-1111-1111-1111-111111111111";

// A PRODUCTION-defaults QueryClient (NO retry:false override) — proves the hook's own retry:false
// stops the deny from being re-hammered (the S-web-8 lesson; the test wrapper would otherwise mask it).
function prodWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

describe("ack hooks", () => {
  test("useDistribution returns the payload", async () => {
    const { result } = renderHook(() => useDistribution(DOC), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(distributionFixture);
  });

  test("useAcknowledgements returns the matrix", async () => {
    const { result } = renderHook(() => useAcknowledgements(DOC, true), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(ackMatrixFixture);
  });

  test("useAcknowledgements flags a 403 as forbidden WITHOUT retry-hammering (production defaults)", async () => {
    let calls = 0;
    server.use(
      http.get("/api/v1/documents/:id/acknowledgements", () => {
        calls += 1;
        return HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 });
      }),
    );
    const { result } = renderHook(() => useAcknowledgements(DOC, true), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.forbidden).toBe(true);
    expect(calls).toBe(1); // retry:false → exactly one call, no 3× backoff hammer
  });

  test("useAcknowledgements does not fetch when enabled=false (flag off)", async () => {
    const { result } = renderHook(() => useAcknowledgements(DOC, false), { wrapper: prodWrapper });
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.fetchStatus).toBe("idle");
  });
});
