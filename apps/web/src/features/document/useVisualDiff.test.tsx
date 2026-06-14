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

test("useVisualDiff surfaces a terminal Failed status via the poll's first GET", async () => {
  // The poll owns its own fetch: once the POST settles success, the first GET (against an empty
  // cache) populates the status. The row is terminally Failed, so the GET returns Failed too — and
  // refetchInterval halts at that terminal status (no further polling).
  const failed = {
    status: "Failed",
    page_count: null,
    reason: "a version row is missing",
    pages: null,
  };
  server.use(
    http.post(VD, () => HttpResponse.json(failed)),
    http.get(VD, () => HttpResponse.json(failed)),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Failed"));
  expect(result.current.status?.reason).toBe("a version row is missing");
});

test("useVisualDiff treats Unavailable (non-renderable version) as a terminal status, not an error", async () => {
  // The row IS Unavailable, so the POST and the poll's GET both return it (the poll owns the fetch).
  const unavailable = {
    status: "Unavailable",
    page_count: null,
    reason: "a version is not renderable to PDF (no page images available)",
    pages: null,
  };
  server.use(
    http.post(VD, () => HttpResponse.json(unavailable)),
    http.get(VD, () => HttpResponse.json(unavailable)),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Unavailable"));
  expect(result.current.isError).toBe(false);
});

test("useVisualDiff surfaces a 403 as an ApiError (document.read_draft)", async () => {
  server.use(
    http.post(VD, () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.error).toBeInstanceOf(ApiError);
  expect((result.current.error as ApiError).status).toBe(403);
});

test("useVisualDiff retry() re-requests a stalled (Pending) render", async () => {
  // The poll owns the status read. Before the re-request the row is Pending (the dev renderer was
  // off when it was created); retry() re-enqueues, and by the next poll the worker has finished, so
  // the GET flips to Ready. We key the GET on the POST count to model "Pending until re-requested".
  let posts = 0;
  server.use(
    http.post(VD, () => ((posts += 1), HttpResponse.json(pending))),
    http.get(VD, () => HttpResponse.json(posts >= 2 ? visualDiffFixture : pending)),
  );
  const { result } = renderHook(() => useVisualDiff(DOC, TO, FROM, true), { wrapper });
  await waitFor(() => expect(result.current.status?.status).toBe("Pending"));

  act(() => result.current.retry());
  await waitFor(() => expect(result.current.status?.status).toBe("Ready"));
  expect(posts).toBe(2);
});

test("useVisualDiff reads the keyed poll cache — a pair change never shows the prior pair's result", async () => {
  const TO2 = "dddd2222-2222-2222-2222-222222222222";
  // The poll is keyed by (documentId, toVid, fromVid): each pair reads its OWN cache via its own
  // GET. Pair #1 (TO) is Ready; pair #2 (TO2) is still Pending — switching pairs must show pair #2's
  // Pending, never fall back to pair #1's Ready. The GET is keyed by vid to mirror that per-pair row.
  const perPairGet = ({ params }: { params: Record<string, string | readonly string[]> }) =>
    HttpResponse.json(
      params.vid === TO
        ? visualDiffFixture
        : { status: "Pending", page_count: null, reason: null, pages: null },
    );
  server.use(http.post(VD, perPairGet), http.get(VD, perPairGet));
  const { result, rerender } = renderHook(({ to }) => useVisualDiff(DOC, to, FROM, true), {
    wrapper,
    initialProps: { to: TO },
  });
  await waitFor(() => expect(result.current.status?.status).toBe("Ready"));
  // switch to a different pair whose diff is still Pending — must NOT keep showing pair #1's Ready
  rerender({ to: TO2 });
  await waitFor(() => expect(result.current.status?.status).toBe("Pending"));
});
