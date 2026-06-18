import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createElement, type ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useAckCount } from "./useAckCount";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(
    MantineProvider,
    { theme },
    createElement(
      QueryClientProvider,
      { client },
      createElement(AuthContext.Provider, { value: TEST_AUTH }, children),
    ),
  );
}

describe("useAckCount", () => {
  test("reports the open DOC_ACK count (not errored)", async () => {
    server.use(http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "a" }, { id: "b" }])));
    const { result } = renderHook(() => useAckCount(), { wrapper });
    await waitFor(() => expect(result.current.count).toBe(2));
    expect(result.current.isError).toBe(false);
  });

  test("a genuine zero is count 0 + not errored (silent bell)", async () => {
    server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
    const { result } = renderHook(() => useAckCount(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.count).toBe(0);
    expect(result.current.isError).toBe(false);
  });

  test("a failed load surfaces isError — the count 0 must NOT be read as 'no acks'", async () => {
    server.use(http.get("/api/v1/tasks", () => new HttpResponse(null, { status: 500 })));
    const { result } = renderHook(() => useAckCount(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    // count is 0 on failure, but isError is the discriminator the bell uses (never a confident 0)
    expect(result.current.count).toBe(0);
  });
});
