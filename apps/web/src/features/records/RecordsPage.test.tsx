import { axe } from "jest-axe";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { expectResponsiveTable } from "../../test/responsiveTable";
import { RecordsPage } from "./RecordsPage";

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>;
}

function renderRecords(route = "/records") {
  return renderWithProviders(<><RecordsPage /><LocationProbe /></>, { route });
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
