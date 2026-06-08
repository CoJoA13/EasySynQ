import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { theme } from "../../theme/mantine";
import { visualDiffFixture } from "../../test/msw/handlers";
import { useVisualDiff } from "./useVisualDiff";
import { ApiError } from "../../lib/api";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";
const VD = "/api/v1/documents/:id/versions/:vid/visual-diff";

const pending = { status: "Pending", page_count: null, reason: null, pages: null };

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

test("useVisualDiff does NOT request when disabled or the pair is incomplete/identical", async () => {
  let posts = 0;
  server.use(http.post(VD, () => ((posts += 1), HttpResponse.json(visualDiffFixture))));

  // enabled=false
  renderHook(() => useVisualDiff(DOC, TO, FROM, false), { wrapper });
  // incomplete pair
  renderHook(() => useVisualDiff(DOC, TO, null, true), { wrapper });
  // identical pair
  renderHook(() => useVisualDiff(DOC, TO, TO, true), { wrapper });

  await new Promise((r) => setTimeout(r, 30));
  expect(posts).toBe(0);
});

test("useVisualDiff POSTs to request, then GET-polls a Pending row to Ready", async () => {
  server.use(
    http.post(VD, () => HttpResponse.json(pending)), // request → still rendering
    http.get(VD, () => HttpResponse.json(visualDiffFixture)), // poll → Ready
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Ready"));
  expect(result.current.status?.page_count).toBe(3);
});

test("useVisualDiff surfaces a terminal POST result without a redundant GET (Failed)", async () => {
  let gets = 0;
  server.use(
    http.post(VD, () =>
      HttpResponse.json({ status: "Failed", page_count: null, reason: "a version row is missing", pages: null }),
    ),
    http.get(VD, () => ((gets += 1), HttpResponse.json(visualDiffFixture))),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Failed"));
  expect(result.current.status?.reason).toBe("a version row is missing");
  expect(gets).toBe(0); // a terminal POST must not trigger a poll
});

test("useVisualDiff treats Unavailable (non-renderable version) as a terminal status, not an error", async () => {
  server.use(
    http.post(VD, () =>
      HttpResponse.json({
        status: "Unavailable",
        page_count: null,
        reason: "a version is not renderable to PDF (no page images available)",
        pages: null,
      }),
    ),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Unavailable"));
  expect(result.current.isError).toBe(false);
});

test("useVisualDiff surfaces a 403 as an ApiError (document.read_draft)", async () => {
  server.use(
    http.post(VD, () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.error).toBeInstanceOf(ApiError);
  expect((result.current.error as ApiError).status).toBe(403);
});

test("useVisualDiff retry() re-POSTs a failed render", async () => {
  let posts = 0;
  server.use(
    http.post(VD, () => {
      posts += 1;
      return HttpResponse.json(
        posts === 1
          ? { status: "Failed", page_count: null, reason: "transient", pages: null }
          : visualDiffFixture,
      );
    }),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Failed"));

  act(() => result.current.retry());
  await waitFor(() => expect(result.current.status?.status).toBe("Ready"));
  expect(posts).toBe(2);
});
