import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { Breadcrumb } from "./Breadcrumb";

const ID = "11111111-1111-1111-1111-111111111111";

function renderCrumb(client: QueryClient, route: string) {
  function Tree({ children }: { children: ReactNode }) {
    return (
      <MantineProvider theme={theme}>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(<Breadcrumb />, { wrapper: Tree });
}

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
