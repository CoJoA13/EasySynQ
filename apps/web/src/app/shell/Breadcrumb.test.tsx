import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { Breadcrumb } from "./Breadcrumb";

const ID = "11111111-1111-1111-1111-111111111111";

function renderCrumb(client: QueryClient, route: string, notFound = false) {
  function Tree({ children }: { children: ReactNode }) {
    return (
      <MantineProvider theme={theme}>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(<Breadcrumb notFound={notFound} />, { wrapper: Tree });
}

test("not-found mode renders only the fixed safe breadcrumb", () => {
  const client = new QueryClient();
  renderCrumb(client, "/missing/private-segment", true);
  const breadcrumb = screen.getByLabelText("Breadcrumb");
  expect(within(breadcrumb).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
  expect(within(breadcrumb).getByText("Page not found")).toBeInTheDocument();
  expect(within(breadcrumb).queryByText(/private-segment/i)).not.toBeInTheDocument();
});

test("Breadcrumb shows the document identifier (not the UUID) when cached", () => {
  const client = new QueryClient();
  client.setQueryData(["document", ID], { identifier: "SOP-PUR-014" });
  renderCrumb(client, `/documents/${ID}`);
  expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
  expect(screen.queryByText(ID)).not.toBeInTheDocument();
});

test("Breadcrumb degrades to the generic 'Document' label when not cached", () => {
  const client = new QueryClient();
  renderCrumb(client, `/documents/${ID}`);
  // both the "documents" crumb and the leaf show "Document"; the raw UUID is never shown
  expect(screen.getAllByText("Document").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(ID)).not.toBeInTheDocument();
});

test("Breadcrumb updates to the identifier when the document loads after a cold visit", async () => {
  // The bug Codex caught: a non-subscribing getQueryData() read would never update. The subscribed
  // observer must re-render once the page populates ['document', id].
  const client = new QueryClient();
  renderCrumb(client, `/documents/${ID}`);
  expect(screen.getAllByText("Document").length).toBeGreaterThanOrEqual(1);
  act(() => {
    client.setQueryData(["document", ID], { identifier: "SOP-PUR-014" });
  });
  expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
  expect(screen.queryByText(ID)).not.toBeInTheDocument();
});

test("Breadcrumb shows the authorized record identifier without exposing its route UUID", async () => {
  const client = new QueryClient();
  renderCrumb(client, `/records/${ID}`);
  expect(screen.getAllByText("Record").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(ID)).not.toBeInTheDocument();
  act(() => {
    client.setQueryData(["record", ID], { id: ID, identifier: "REC-000041" });
  });
  expect(await screen.findByText("REC-000041")).toBeInTheDocument();
  expect(screen.queryByText(ID)).not.toBeInTheDocument();
});

test.each([
  [`/tasks/${ID}`, "Task"],
  [`/audits/${ID}`, "Audit"],
  [`/imports/${ID}`, "Import run"],
  [`/objectives/${ID}`, "Objective"],
  [`/management-reviews/${ID}`, "Management review"],
  [`/dcrs/${ID}/diff`, "Change request"],
])("Breadcrumb replaces the detail identifier in %s with %s", (route, label) => {
  const client = new QueryClient();
  renderCrumb(client, route);
  const breadcrumb = screen.getByLabelText("Breadcrumb");
  expect(within(breadcrumb).getAllByText(label).length).toBeGreaterThan(0);
  expect(within(breadcrumb).queryByText(ID)).not.toBeInTheDocument();
});

test.each([
  ["/reports/document-control", "Controlled document register", "document-control"],
  ["/management-reviews", "Management reviews", "management-reviews"],
  ["/interested-parties", "Interested parties", "interested-parties"],
  ["/records", "Records", "records"],
  ["/drift/superseded-copies", "Superseded copies", "superseded-copies"],
])("Breadcrumb humanizes the registered route %s", (route, label, rawSlug) => {
  const client = new QueryClient();
  renderCrumb(client, route);
  const breadcrumb = screen.getByLabelText("Breadcrumb");
  expect(within(breadcrumb).getByText(label)).toBeInTheDocument();
  expect(within(breadcrumb).queryByText(rawSlug)).not.toBeInTheDocument();
});

test.each([
  [`/documents/${ID}`, "Document"],
  ["/reports/document-control", "Reports"],
  ["/settings/notifications", "Settings"],
  [`/dcrs/${ID}/diff`, "Change request"],
  [`/records/${ID}`, "Record"],
])("Breadcrumb renders the non-route parent in %s as text, not a dead link", (route, label) => {
  const client = new QueryClient();
  renderCrumb(client, route);
  const breadcrumb = screen.getByLabelText("Breadcrumb");
  expect(within(breadcrumb).getAllByText(label).length).toBeGreaterThan(0);
  expect(within(breadcrumb).queryByRole("link", { name: label })).not.toBeInTheDocument();
});
