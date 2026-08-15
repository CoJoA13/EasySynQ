import { axe } from "jest-axe";
import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { expectResponsiveTable } from "../../test/responsiveTable";
import { docFixture, recordsFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { RecordsPage } from "./RecordsPage";

function LocationProbe() {
  const location = useLocation();
  return (
    <>
      <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>
      <output aria-label="Location state">{JSON.stringify(location.state)}</output>
    </>
  );
}

function BackButton() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/records?q=external-search")}>
        External search
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        Back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        Forward
      </button>
    </>
  );
}

function renderRecords(route = "/records", queryClient?: QueryClient) {
  return renderWithProviders(
    <Routes>
      <Route
        path="/records"
        element={
          <>
            <RecordsPage />
            <LocationProbe />
            <BackButton />
          </>
        }
      />
      <Route path="/records/:recordId" element={<LocationProbe />} />
    </Routes>,
    { route, ...(queryClient ? { queryClient } : {}) },
  );
}

test("renders the responsive Records register with one native detail link per row", async () => {
  const { container } = renderRecords();

  await screen.findByRole("link", { name: /open record REC-000041/i });
  const table = expectResponsiveTable(840);
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent),
  ).toEqual(["Identifier", "Title", "Type", "Captured by", "Captured", "State"]);
  const row = within(table)
    .getByRole("link", { name: /open record REC-000041/i })
    .closest("tr")!;
  expect(row).not.toHaveAttribute("role");
  expect(row).not.toHaveAttribute("tabindex");
  expect(within(row).getAllByRole("link")).toHaveLength(1);
  expect(await axe(container)).toHaveNoViolations();
});

test("replaces criteria while preserving unrelated URL state and pushes cursor pagination", async () => {
  const user = userEvent.setup();
  renderRecords("/records?view=compact&cursor=old");
  await screen.findByRole("heading", { name: "Records" });

  await user.type(screen.getByRole("searchbox", { name: "Search records" }), "REC-41");
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent(
      "/records?view=compact&q=REC-41",
    ),
  );
  await screen.findByRole("link", { name: /open record REC-000041/i });

  await user.click(screen.getByRole("button", { name: "Next records page" }));
  expect(screen.getByRole("button", { name: "Next records page" })).toHaveStyle({
    minHeight: "calc(2.75rem * var(--mantine-scale))",
  });
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "/records?view=compact&q=REC-41&cursor=next-records-page",
  );
});

test("does not replay a settled search after Clear all changes the URL", async () => {
  const user = userEvent.setup();
  renderRecords("/records?q=settled-search");
  await screen.findByRole("link", { name: /open record REC-000041/i });

  await user.click(screen.getByRole("button", { name: "Clear all" }));

  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("/records"),
  );
  await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));
  expect(screen.getByRole("searchbox", { name: "Search records" })).toHaveValue("");
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("q=settled-search");
});

test("cancels a pending search when Clear all makes URL state authoritative", async () => {
  const user = userEvent.setup();
  renderRecords("/records?record_type=EVIDENCE");
  await screen.findByRole("link", { name: /open record REC-000041/i });

  const search = screen.getByRole("searchbox", { name: "Search records" });
  await user.type(search, "pending-search");
  await user.click(screen.getByRole("button", { name: "Clear all" }));

  await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));
  expect(search).toHaveValue("");
  const location = new URL(
    screen.getByLabelText("Current location").textContent ?? "",
    "http://test",
  );
  expect(location.searchParams.has("record_type")).toBe(false);
  expect(location.searchParams.has("q")).toBe(false);
});

test("adopts Back and Forward search values even when history returns to its initial value", async () => {
  const user = userEvent.setup();
  renderRecords("/records?q=initial-search");
  const search = screen.getByRole("searchbox", { name: "Search records" });
  expect(search).toHaveValue("initial-search");

  await user.click(screen.getByRole("button", { name: "External search" }));
  await waitFor(() => expect(search).toHaveValue("external-search"));

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() => expect(search).toHaveValue("initial-search"));

  await user.click(screen.getByRole("button", { name: "Forward" }));
  await waitFor(() => expect(search).toHaveValue("external-search"));
});

test("opens the source picker with a blank server query", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderRecords();
  await screen.findByRole("link", { name: /open record REC-000041/i });

  await userEvent.setup().click(screen.getByRole("textbox", { name: "Source document" }));

  await waitFor(() =>
    expect(
      fetchSpy.mock.calls.some(([input]) => {
        const url = new URL(String(input), "http://test");
        return url.pathname === "/api/v1/documents" && !url.searchParams.has("q");
      }),
    ).toBe(true),
  );
  fetchSpy.mockRestore();
});

test("keeps a selected source label out of the server search query", async () => {
  const user = userEvent.setup();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderRecords();
  await screen.findByRole("link", { name: /open record REC-000041/i });

  const source = screen.getByRole("textbox", { name: "Source document" });
  await user.click(source);
  await user.type(source, "Supplier");
  await waitFor(() =>
    expect(
      fetchSpy.mock.calls.some(([input]) => {
        const url = new URL(String(input), "http://test");
        return url.pathname === "/api/v1/documents" && url.searchParams.get("q") === "Supplier";
      }),
    ).toBe(true),
  );
  await user.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));

  const queries = fetchSpy.mock.calls
    .map(([input]) => new URL(String(input), "http://test"))
    .filter((url) => url.pathname === "/api/v1/documents")
    .map((url) => url.searchParams.get("q"));
  expect(queries).toContain("Supplier");
  expect(queries).not.toContain("SOP-PUR-014 — Supplier Selection & Evaluation");
  fetchSpy.mockRestore();
});

test("retains an authorized selected source label after blank results omit it", async () => {
  const user = userEvent.setup();
  const documentQueries: Array<string | null> = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      const q = new URL(request.url).searchParams.get("q");
      documentQueries.push(q);
      const rows = q === "Supplier" ? [docFixture[0]] : [docFixture[1]];
      return HttpResponse.json({
        data: rows,
        page: { limit: 20, offset: 0, returned: rows.length, has_more: false },
      });
    }),
  );
  renderRecords();
  await screen.findByRole("link", { name: /open record REC-000041/i });

  const source = screen.getByRole("textbox", { name: "Source document" });
  await user.click(source);
  await screen.findByRole("option", { name: /SOP-PRD-007/ });
  await user.type(source, "Supplier");
  await user.click(
    await screen.findByRole("option", {
      name: "SOP-PUR-014 — Supplier Selection & Evaluation",
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent(
      "source_document_id=11111111-1111-1111-1111-111111111111",
    ),
  );
  await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));

  expect(source).toHaveValue("SOP-PUR-014 — Supplier Selection & Evaluation");
  expect(screen.queryByDisplayValue("Selected item unavailable")).not.toBeInTheDocument();
  expect(documentQueries).toContain("Supplier");
  expect(documentQueries).not.toContain("SOP-PUR-014 — Supplier Selection & Evaluation");
});

test("keeps a matching pre-mount source cache neutral without a successful current response", async () => {
  const user = userEvent.setup();
  const cachedDocument = docFixture[0]!;
  const cachedLabel = "SOP-PUR-014 — Supplier Selection & Evaluation";
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(["record-source-documents", "limit=20&offset=0"], {
    data: [cachedDocument],
    page: { limit: 20, offset: 0, returned: 1, has_more: false },
  });
  const currentMountRequests: string[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      currentMountRequests.push(request.url);
      return HttpResponse.json(
        { code: "permission_denied", title: "Source documents unavailable" },
        { status: 403 },
      );
    }),
  );

  renderRecords(`/records?source_document_id=${cachedDocument.id}`, queryClient);
  await screen.findByRole("link", { name: /open record REC-000041/i });

  const source = screen.getByRole("textbox", { name: "Source document" });
  await waitFor(() => expect(source).toHaveValue("Selected item unavailable"));
  expect(source).not.toHaveValue(cachedLabel);
  expect(screen.queryByDisplayValue(cachedLabel)).not.toBeInTheDocument();
  expect(currentMountRequests).toEqual([]);

  await user.click(source);
  await waitFor(() => expect(currentMountRequests).toHaveLength(1));
  await waitFor(() =>
    expect(screen.queryByRole("option", { name: cachedLabel })).not.toBeInTheDocument(),
  );
  expect(source).toHaveValue("Selected item unavailable");
});

test("keeps an unavailable selected UUID neutral and does not use a raw UUID lookup", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderRecords("/records?source_document_id=99999999-9999-9999-9999-999999999999");
  expect(await screen.findByText("Selected item unavailable")).toBeInTheDocument();
  expect(
    fetchSpy.mock.calls.some(
      ([path]) => path === "/api/v1/documents/99999999-9999-9999-9999-999999999999",
    ),
  ).toBe(false);
  fetchSpy.mockRestore();
});

test("keeps an unavailable selected capturer neutral without exposing its identifier", async () => {
  const missing = "99999999-9999-9999-9999-999999999999";
  renderRecords(`/records?captured_by=${missing}`);

  const capturedBy = await screen.findByRole("textbox", { name: "Captured by" });
  expect(capturedBy).toHaveValue("Selected item unavailable");
  expect(screen.queryByText(missing)).not.toBeInTheDocument();
});

async function choose(user: ReturnType<typeof userEvent.setup>, label: string, option: string) {
  await user.click(screen.getByRole("textbox", { name: label }));
  await user.click(await screen.findByRole("option", { name: option }));
}

test("serializes every record filter, clears only record criteria, and retains unrelated state", async () => {
  const user = userEvent.setup();
  renderRecords("/records?view=compact&cursor=old");
  await screen.findByRole("link", { name: /open record REC-000041/i });

  await choose(user, "Record type", "EVIDENCE");
  await choose(user, "Disposition", "ACTIVE");
  await choose(user, "Legal hold", "Yes");
  await choose(user, "Captured by", "Mara Quality");
  await user.click(screen.getByRole("textbox", { name: "Source document" }));
  await user.type(screen.getByRole("textbox", { name: "Source document" }), "Supplier");
  await user.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));

  await waitFor(() =>
    expect(
      Object.fromEntries(
        new URLSearchParams(screen.getByLabelText("Current location").textContent?.split("?")[1]),
      ),
    ).toEqual({
      view: "compact",
      record_type: "EVIDENCE",
      source_document_id: "11111111-1111-1111-1111-111111111111",
      captured_by: "bbbb1111-1111-1111-1111-111111111111",
      disposition_state: "ACTIVE",
      legal_hold: "true",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Clear all" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?view=compact");
});

test("Back restores the exact filtered cursor URL and identifier link retains it as detail origin", async () => {
  const user = userEvent.setup();
  renderRecords("/records?q=REC-41");
  await screen.findByRole("link", { name: /open record REC-000041/i });
  await user.click(screen.getByRole("button", { name: "Next records page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "/records?q=REC-41&cursor=next-records-page",
  );
  await user.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?q=REC-41");
  const link = screen.getByRole("link", { name: /open record REC-000041/i });
  await user.click(link);
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "/records/re000001-0001-0001-0001-000000000001",
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Location state")).toHaveTextContent("/records?q=REC-41"),
  );
});

test("shows the unfiltered empty message for a valid cursor-only page", async () => {
  server.use(
    http.get("/api/v1/records", () =>
      HttpResponse.json({
        data: [],
        page: { limit: 50, returned: 0, next_cursor: null },
      }),
    ),
  );

  renderRecords("/records?cursor=valid-next-page");

  expect(await screen.findByText("No records yet")).toBeInTheDocument();
  expect(screen.queryByText("No records match your filters")).not.toBeInTheDocument();
});

test("keeps the toolbar visible for loading, empty, invalid-cursor, and retryable-error states", async () => {
  let releaseLoading: ((value: Response) => void) | undefined;
  server.use(
    http.get(
      "/api/v1/records",
      () =>
        new Promise<Response>((resolve) => {
          releaseLoading = resolve;
        }),
    ),
  );
  const loading = renderRecords();
  expect(await screen.findByRole("status", { name: "Loading records" })).toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Search records" })).toBeInTheDocument();
  await act(async () =>
    releaseLoading?.(
      HttpResponse.json({ data: [], page: { limit: 50, returned: 0, next_cursor: null } }),
    ),
  );
  expect(await screen.findByText("No records yet")).toBeInTheDocument();
  loading.unmount();

  server.use(
    http.get("/api/v1/records", () =>
      HttpResponse.json({ data: [], page: { limit: 50, returned: 0, next_cursor: null } }),
    ),
  );
  const filtered = renderRecords("/records?record_type=EVIDENCE");
  expect(await screen.findByText("No records match your filters")).toBeInTheDocument();
  filtered.unmount();

  server.use(
    http.get("/api/v1/records", () =>
      HttpResponse.json(
        { code: "validation_error", title: "Invalid records cursor" },
        { status: 422 },
      ),
    ),
  );
  const cursor = renderRecords("/records?cursor=bad");
  const invalid = await screen.findByText("This records page is no longer available");
  expect(invalid).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Return to first page" })).toHaveStyle({
    minHeight: "calc(2.75rem * var(--mantine-scale))",
  });
  await userEvent.setup().click(screen.getByRole("button", { name: "Return to first page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records");
  cursor.unmount();

  let calls = 0;
  server.use(
    http.get("/api/v1/records", () => {
      calls += 1;
      return calls === 1
        ? HttpResponse.json({ title: "Temporary" }, { status: 503 })
        : HttpResponse.json(recordsFixture);
    }),
  );
  renderRecords();
  await userEvent.setup().click(await screen.findByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("link", { name: /open record REC-000041/i })).toBeInTheDocument();
  expect(calls).toBe(2);
});

test("keeps an ordinary validation 422 retryable even when a cursor is present", async () => {
  server.use(
    http.get("/api/v1/records", () =>
      HttpResponse.json(
        {
          code: "validation_error",
          title: "Request validation failed",
        },
        { status: 422 },
      ),
    ),
  );
  renderRecords("/records?cursor=bad&record_type=NOT_A_RECORD");

  expect(await screen.findByText("Couldn't load records")).toBeInTheDocument();
  expect(screen.queryByText("This records page is no longer available")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toHaveStyle({
    minHeight: "calc(2.75rem * var(--mantine-scale))",
  });
});

test("constrains a maximum-length active-filter label while preserving its accessible name", async () => {
  const query = "q".repeat(200);
  renderRecords(`/records?q=${query}`);

  const remove = await screen.findByRole("button", { name: `Remove filter Search: ${query}` });
  const visibleLabel = within(remove).getByText(`Search: ${query}`);
  expect(visibleLabel.tagName).toBe("SPAN");
  expect(visibleLabel).toHaveStyle({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
  expect(remove).toHaveStyle({ maxWidth: "100%" });
});
