import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { PNG_1x1 } from "../../test/msw/handlers";
import { VisualDiffViewer } from "./VisualDiffViewer";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";
const VD = "/api/v1/documents/:id/versions/:vid/visual-diff";
const PAGE = "/api/v1/documents/:id/versions/:vid/visual-diff/page/:page";

const pending = { status: "Pending", page_count: null, reason: null, pages: null };
const png = () => new HttpResponse(PNG_1x1, { headers: { "Content-Type": "image/png" } });

afterEach(() => vi.restoreAllMocks());

test("VisualDiffViewer (Ready) renders the changed-page rail + the page image via an AUTHED fetch", async () => {
  let authHeader: string | null = null;
  server.use(http.get(PAGE, ({ request }) => ((authHeader = request.headers.get("authorization")), png())));

  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);

  // the pane image — default page is the first CHANGED page (index 1 → "Page 2"), default layer Diff
  const img = await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
  expect(img.tagName.toLowerCase()).toBe("img");
  expect((img as HTMLImageElement).src).toMatch(/^blob:/);
  // the bearer rode the page fetch (the endpoint is authed, not presigned)
  expect(authHeader).toBe("Bearer test-token");
  // the rail marks changed pages NON-color (a textual "changed" in the accessible name)
  expect(screen.getByRole("button", { name: "Page 2, changed" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Page 1" })).toBeInTheDocument(); // page 0 unchanged
});

test("VisualDiffViewer layer toggle re-fetches the page with ?layer=to", async () => {
  const layers: string[] = [];
  server.use(
    http.get(PAGE, ({ request }) => (layers.push(new URL(request.url).searchParams.get("layer") ?? ""), png())),
  );
  const user = userEvent.setup();
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await screen.findByAltText(/Diff layer/);
  await user.click(screen.getByText("After"));
  await waitFor(() => expect(layers).toContain("to"));
});

test("VisualDiffViewer shows a calm note when a layer has no image for the page (404)", async () => {
  server.use(
    http.get(PAGE, () => HttpResponse.json({ code: "not_found", title: "No image" }, { status: 404 })),
  );
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() =>
    expect(screen.getByText("No image on this side for this page.")).toBeInTheDocument(),
  );
});

test("VisualDiffViewer n/p steps through the changed pages", async () => {
  const user = userEvent.setup();
  server.use(http.get(PAGE, () => png()));
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
  const region = screen.getByRole("group", { name: "Visual page diff" });
  region.focus();
  await user.keyboard("n"); // page 1 → next changed page (index 2 → "Page 3")
  await screen.findByAltText("Page 3 of 3 — Diff layer (changed)");
  await user.keyboard("p"); // back to the prior changed page (index 1 → "Page 2")
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
});

test("VisualDiffViewer (Pending) shows the phased long-op affordance, not a frozen UI", async () => {
  server.use(
    http.post(VD, () => HttpResponse.json(pending)),
    http.get(VD, () => HttpResponse.json(pending)),
  );
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() => expect(screen.getByText("Rendering page images…")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Re-request render" })).toBeInTheDocument();
});

test("VisualDiffViewer (Failed) is a calm terminal with a source-download fallback (no dead Retry)", async () => {
  const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
  server.use(
    http.post(VD, () =>
      HttpResponse.json({ status: "Failed", page_count: null, reason: "render crashed", pages: null }),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() => expect(screen.getByText(/render crashed/)).toBeInTheDocument());
  // a terminal Failed row can't be re-driven (the backend only re-enqueues Pending) — no dead Retry
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Download After source/ }));
  await waitFor(() => expect(openSpy).toHaveBeenCalled());
});

test("VisualDiffViewer resets the selected page when the compared pair changes", async () => {
  const TO2 = "dddd2222-2222-2222-2222-222222222222";
  const user = userEvent.setup();
  server.use(http.get(PAGE, () => png()));
  const { rerender } = renderWithProviders(
    <VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />,
  );
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)"); // default = first changed page
  await user.click(screen.getByRole("button", { name: "Page 3, changed" }));
  await screen.findByAltText("Page 3 of 3 — Diff layer (changed)"); // user selected the last page
  // change the pair — the selection must reset to the NEW diff's first changed page, not stay on 3
  rerender(<VisualDiffViewer documentId={DOC} fromVid={TO} toVid={TO2} />);
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
});

test("VisualDiffViewer (Unavailable) offers the source-download fallback, not an error", async () => {
  const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
  server.use(
    http.post(VD, () =>
      HttpResponse.json({
        status: "Unavailable",
        page_count: null,
        reason: "a version is not renderable to PDF",
        pages: null,
      }),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() => expect(screen.getByText("Visual diff unavailable")).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /Download Before source/ }));
  await waitFor(() => expect(openSpy).toHaveBeenCalled());
});

test("VisualDiffViewer shows quiet no-access on a 403 (document.read_draft)", async () => {
  server.use(
    http.post(VD, () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })),
  );
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() =>
    expect(screen.getByText("You don't have access to the visual diff.")).toBeInTheDocument(),
  );
});

test("VisualDiffViewer revokes the prior page objectURL when the layer changes (no leak)", async () => {
  const revoke = vi.spyOn(URL, "revokeObjectURL");
  server.use(http.get(PAGE, () => png()));
  const user = userEvent.setup();
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await screen.findByAltText(/Diff layer/);
  await user.click(screen.getByText("Before"));
  await screen.findByAltText(/Before layer/);
  await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:mock"));
});

test("VisualDiffViewer shows a page-load error on a non-404 failure", async () => {
  server.use(
    http.get(PAGE, () => HttpResponse.json({ code: "server_error", title: "boom" }, { status: 500 })),
  );
  renderWithProviders(<VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() =>
    expect(screen.getByText("Could not load this page image.")).toBeInTheDocument(),
  );
});

test("VisualDiffViewer (Ready) has no a11y violations", async () => {
  server.use(http.get(PAGE, () => png()));
  const { container } = renderWithProviders(
    <VisualDiffViewer documentId={DOC} fromVid={FROM} toVid={TO} />,
  );
  await screen.findByAltText(/Diff layer/);
  expect(await axe(container)).toHaveNoViolations();
});
