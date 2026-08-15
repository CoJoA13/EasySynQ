import { axe } from "jest-axe";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { expectResponsiveTable } from "../../test/responsiveTable";
import { recordsFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { RecordsPage } from "./RecordsPage";

function LocationProbe() {
  const location = useLocation();
  return <>
    <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>
    <output aria-label="Location state">{JSON.stringify(location.state)}</output>
  </>;
}

function BackButton() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>Back</button>;
}

function renderRecords(route = "/records") {
  return renderWithProviders(<Routes>
    <Route path="/records" element={<><RecordsPage /><LocationProbe /><BackButton /></>} />
    <Route path="/records/:recordId" element={<LocationProbe />} />
  </Routes>, { route });
}

test("renders the responsive Records register with one native detail link per row", async () => {
  const { container } = renderRecords();

  await screen.findByRole("link", { name: /open record REC-000041/i });
  const table = expectResponsiveTable(840);
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "Identifier",
    "Title",
    "Type",
    "Captured by",
    "Captured",
    "State",
  ]);
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
  await waitFor(() => expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?view=compact&q=REC-41"));
  await screen.findByRole("link", { name: /open record REC-000041/i });

  await user.click(screen.getByRole("button", { name: "Next records page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "/records?view=compact&q=REC-41&cursor=next-records-page",
  );
});

test("keeps an unavailable selected UUID neutral and does not use a raw UUID lookup", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderRecords("/records?source_document_id=99999999-9999-9999-9999-999999999999");
  expect(await screen.findByText("Selected item unavailable")).toBeInTheDocument();
  expect(fetchSpy.mock.calls.some(([path]) => path === "/api/v1/documents/99999999-9999-9999-9999-999999999999")).toBe(false);
  fetchSpy.mockRestore();
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

  await waitFor(() => expect(Object.fromEntries(new URLSearchParams(screen.getByLabelText("Current location").textContent?.split("?")[1]))).toEqual({
    view: "compact", record_type: "EVIDENCE", source_document_id: "11111111-1111-1111-1111-111111111111", captured_by: "bbbb1111-1111-1111-1111-111111111111", disposition_state: "ACTIVE", legal_hold: "true",
  }));
  await user.click(screen.getByRole("button", { name: "Clear all" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?view=compact");
});

test("Back restores the exact filtered cursor URL and identifier link retains it as detail origin", async () => {
  const user = userEvent.setup();
  renderRecords("/records?q=REC-41");
  await screen.findByRole("link", { name: /open record REC-000041/i });
  await user.click(screen.getByRole("button", { name: "Next records page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?q=REC-41&cursor=next-records-page");
  await user.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records?q=REC-41");
  const link = screen.getByRole("link", { name: /open record REC-000041/i });
  await user.click(link);
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records/re000001-0001-0001-0001-000000000001");
  await waitFor(() => expect(screen.getByLabelText("Location state")).toHaveTextContent("/records?q=REC-41"));
});

test("shows the unfiltered empty message for a valid cursor-only page", async () => {
  server.use(http.get("/api/v1/records", () => HttpResponse.json({
    data: [],
    page: { limit: 50, returned: 0, next_cursor: null },
  })));

  renderRecords("/records?cursor=valid-next-page");

  expect(await screen.findByText("No records yet")).toBeInTheDocument();
  expect(screen.queryByText("No records match your filters")).not.toBeInTheDocument();
});

test("keeps the toolbar visible for loading, empty, invalid-cursor, and retryable-error states", async () => {
  let releaseLoading: ((value: Response) => void) | undefined;
  server.use(http.get("/api/v1/records", () => new Promise<Response>((resolve) => { releaseLoading = resolve; })));
  const loading = renderRecords();
  expect(await screen.findByRole("status", { name: "Loading records" })).toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Search records" })).toBeInTheDocument();
  await act(async () => releaseLoading?.(HttpResponse.json({ data: [], page: { limit: 50, returned: 0, next_cursor: null } })));
  expect(await screen.findByText("No records yet")).toBeInTheDocument();
  loading.unmount();

  server.use(http.get("/api/v1/records", () => HttpResponse.json({ data: [], page: { limit: 50, returned: 0, next_cursor: null } })));
  const filtered = renderRecords("/records?record_type=EVIDENCE");
  expect(await screen.findByText("No records match your filters")).toBeInTheDocument();
  filtered.unmount();

  server.use(http.get("/api/v1/records", () => HttpResponse.json({ code: "validation_error", title: "Bad cursor" }, { status: 422 })));
  const cursor = renderRecords("/records?cursor=bad");
  const invalid = await screen.findByText("This records page is no longer available");
  expect(invalid).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Return to first page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("/records");
  cursor.unmount();

  let calls = 0;
  server.use(http.get("/api/v1/records", () => {
    calls += 1;
    return calls === 1 ? HttpResponse.json({ title: "Temporary" }, { status: 503 }) : HttpResponse.json(recordsFixture);
  }));
  renderRecords();
  await userEvent.setup().click(await screen.findByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("link", { name: /open record REC-000041/i })).toBeInTheDocument();
  expect(calls).toBe(2);
});
