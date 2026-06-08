import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { detailCapabilities, docFixture } from "../../test/msw/handlers";
import { DocumentDetailPage } from "./DocumentDetailPage";

const ID = "11111111-1111-1111-1111-111111111111";

function renderPage(route = `/documents/${ID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="documents/:id" element={<DocumentDetailPage />} />
    </Routes>,
    { route },
  );
}

// Re-serve the detail doc with an overridden capabilities block (for the author-gating tests).
function serveDocWithCaps(caps: Partial<typeof detailCapabilities>) {
  server.use(
    http.get("/api/v1/documents/:id", ({ params }) => {
      const doc = docFixture.find((d) => d.id === params.id);
      return doc
        ? HttpResponse.json({ ...doc, capabilities: { ...detailCapabilities, ...caps } })
        : HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 });
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

test("DocumentDetailPage renders the header, tiles, rendition, history, where-used and metadata", async () => {
  renderPage();
  expect(
    await screen.findByRole("heading", { name: "Supplier Selection & Evaluation" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Governing revision")).toBeInTheDocument();
  expect(screen.getByText("Mapped clauses")).toBeInTheDocument();
  expect(screen.getByText("Versions")).toBeInTheDocument();
  // governing revision resolves from the version list (current_effective_version_id → Rev B)
  await waitFor(() => expect(screen.getAllByText("Rev B").length).toBeGreaterThanOrEqual(1));
  expect(screen.getByText("Controlled rendition")).toBeInTheDocument();
  expect(screen.getByText("Where-used")).toBeInTheDocument();
  expect(screen.getByText("Version history")).toBeInTheDocument();
  expect(screen.getByText("Control metadata")).toBeInTheDocument();
});

test("DocumentDetailPage renders the Approvals stepper card", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  expect(screen.getByText("Approvals")).toBeInTheDocument();
  expect(await screen.findByText("Quality approval")).toBeInTheDocument();
});

test("DocumentDetailPage shows a loading skeleton before the document resolves", () => {
  renderPage();
  expect(screen.getByLabelText("Loading document")).toBeInTheDocument();
});

test("DocumentDetailPage shows a not-found state for a missing document (404)", async () => {
  renderPage("/documents/99999999-9999-9999-9999-999999999999");
  expect(await screen.findByText("This document does not exist.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to the Library/ })).toBeInTheDocument();
});

test("DocumentDetailPage shows a no-access state on a 403", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to this document.")).toBeInTheDocument();
});

test("DocumentDetailPage hides author actions without the edit capability (DP-6)", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  expect(screen.queryByRole("button", { name: /Start revision/ })).not.toBeInTheDocument();
});

test("DocumentDetailPage shows Start revision when the edit capability is present", async () => {
  serveDocWithCaps({ edit: true });
  renderPage();
  expect(await screen.findByRole("button", { name: /Start revision/ })).toBeInTheDocument();
});

test("DocumentDetailPage has no a11y violations (read-only)", async () => {
  const { container } = renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("DocumentDetailPage has no a11y violations (with author actions)", async () => {
  serveDocWithCaps({ edit: true });
  const { container } = renderPage();
  await screen.findByRole("button", { name: /Start revision/ });
  expect(await axe(container)).toHaveNoViolations();
});
