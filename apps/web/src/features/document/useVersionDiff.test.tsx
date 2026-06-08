import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { theme } from "../../theme/mantine";
import { useVersionDiff } from "./useVersionDiff";
import { ApiError } from "../../lib/api";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

test("useVersionDiff fetches the redline for a distinct version pair", async () => {
  const { result } = renderHook(() => useVersionDiff(DOC, TO, FROM), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.to.revision_label).toBe("Rev B");
  expect(result.current.data?.text_diff.status).toBe("ok");
});

test("useVersionDiff is disabled when the pair is incomplete or identical", () => {
  const { result: missing } = renderHook(() => useVersionDiff(DOC, TO, null), { wrapper });
  expect(missing.current.fetchStatus).toBe("idle");
  const { result: same } = renderHook(() => useVersionDiff(DOC, TO, TO), { wrapper });
  expect(same.current.fetchStatus).toBe("idle");
});

test("useVersionDiff surfaces a 403 as an ApiError (document.read_draft)", async () => {
  server.use(
    http.get("/api/v1/documents/:id/versions/:vid/diff", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useVersionDiff(DOC, TO, FROM), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.error).toBeInstanceOf(ApiError);
  expect((result.current.error as ApiError).status).toBe(403);
});
