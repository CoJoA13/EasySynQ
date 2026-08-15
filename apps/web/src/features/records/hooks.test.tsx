import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { docFixture, recordsFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { useRecordSourceDocuments, useRecords } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useRecords aborts the pending request when URL criteria change", async () => {
  let markOlderRequestStarted!: () => void;
  const olderRequestStarted = new Promise<void>((resolve) => {
    markOlderRequestStarted = resolve;
  });
  let releaseOlderResponse!: () => void;
  const olderResponseReleased = new Promise<void>((resolve) => {
    releaseOlderResponse = resolve;
  });
  let olderRequestAborted = false;

  server.use(
    http.get("/api/v1/records", async ({ request }) => {
      const requestUrl = new URL(request.url);
      if (requestUrl.searchParams.get("q") === "older") {
        request.signal.addEventListener(
          "abort",
          () => {
            olderRequestAborted = true;
          },
          { once: true },
        );
        markOlderRequestStarted();
        await olderResponseReleased;
        return HttpResponse.json({ ...recordsFixture, data: recordsFixture.data.slice(0, 1) });
      }
      return HttpResponse.json({ ...recordsFixture, data: recordsFixture.data.slice(1) });
    }),
  );

  const { result, rerender } = renderHook(({ q }: { q: string }) => useRecords({ limit: 50, q }), {
    wrapper,
    initialProps: { q: "older" },
  });

  await olderRequestStarted;
  rerender({ q: "newer" });

  try {
    await waitFor(() => expect(olderRequestAborted).toBe(true));
    await waitFor(() => expect(result.current.data?.data[0]?.identifier).toBe("REC-000042"));
  } finally {
    releaseOlderResponse();
  }
});

test("useRecordSourceDocuments uses the row-filtered Documents endpoint only while open", async () => {
  const requests: string[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      requests.push(new URL(request.url).search);
      return HttpResponse.json({
        data: docFixture,
        page: { limit: 20, offset: 0, returned: 2, has_more: false },
      });
    }),
  );

  const { result, rerender } = renderHook(
    ({ q, enabled }: { q: string; enabled: boolean }) => useRecordSourceDocuments(q, enabled),
    { wrapper, initialProps: { q: "SOP", enabled: false } },
  );

  expect(result.current.fetchStatus).toBe("idle");
  rerender({ q: "SOP", enabled: true });

  await waitFor(() => expect(result.current.data?.data).toEqual(docFixture));
  expect(requests).toEqual(["?limit=20&offset=0&q=SOP"]);
});
