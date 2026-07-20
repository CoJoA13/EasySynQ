import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ReportsRegisterPage } from "./ReportsRegisterPage";
import type { DocumentControlRegister } from "../../lib/types";

const REG: DocumentControlRegister = {
  provenance: {
    report_name: "Controlled Document Register",
    generated_by: "Mara",
    generated_at: "2026-07-19T12:00:00+00:00",
    as_of: "2026-07-19T12:00:00+00:00",
    scope: "org:DEFAULT",
    app_version: "0.1.0",
    filters: {},
    row_count: 2,
    content_hash: "sha256:abc123",
  },
  rows: [
    {
      id: "1",
      identifier: "SOP-QA-001",
      title: "Document Control",
      document_type_id: null,
      document_type: "SOP",
      current_state: "Effective",
      owner_user_id: "u1",
      owner_display: "Priya",
      effective_revision_label: "Rev A",
      effective_from: "2026-06-01T00:00:00+00:00",
      blob_sha256: "deadbeefcafef00d",
      clause_refs: [{ clause: "7.5.3", starred: true }],
      process_links: [],
      approved_by: "Ken",
      approved_on: "2026-06-01T00:00:00+00:00",
      next_review_due: "2027-06-01",
      review_state: "OK",
    },
    {
      id: "2",
      identifier: "WI-QA-002",
      title: "Aardvark Work Instruction",
      document_type_id: null,
      document_type: "WI",
      current_state: "Draft",
      owner_user_id: "u2",
      owner_display: "Diego",
      effective_revision_label: null,
      effective_from: null,
      blob_sha256: null,
      clause_refs: [],
      process_links: [],
      approved_by: null,
      approved_on: null,
      next_review_due: null,
      review_state: null,
    },
  ],
} satisfies DocumentControlRegister;

describe("ReportsRegisterPage", () => {
  it("renders the provenance banner + a register row", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();
    // The page title AND the provenance banner's report_name both render this string.
    expect(screen.getAllByText("Controlled Document Register").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/sha256:abc123/)).toBeInTheDocument();
    expect(screen.getByText("Rev A")).toBeInTheDocument();
    expect(screen.getByText(/7\.5\.3/)).toBeInTheDocument();
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
  });

  it("shows a calm no-access panel on 403", async () => {
    server.use(
      http.get("/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });

  it("shows a calm error (not an infinite loader) on a non-403 failure", async () => {
    server.use(
      http.get("/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText(/Couldn't load the register/)).toBeInTheDocument();
  });

  it("debounced search filters rows by identifier / title / type", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search"), "aardvark");
    await waitFor(() => expect(screen.queryByText("SOP-QA-001")).not.toBeInTheDocument());
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
  });

  it("sorts by the Identifier column", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    // Default sort is identifier asc: SOP-QA-001 before WI-QA-002.
    let cells = screen.getAllByText(/^(SOP-QA-001|WI-QA-002)$/);
    expect(cells.map((c) => c.textContent)).toEqual(["SOP-QA-001", "WI-QA-002"]);
    await user.click(screen.getByRole("button", { name: "Sort by Identifier" }));
    cells = screen.getAllByText(/^(SOP-QA-001|WI-QA-002)$/);
    expect(cells.map((c) => c.textContent)).toEqual(["WI-QA-002", "SOP-QA-001"]);
  });

  it("has no axe violations", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    const { container } = renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(await axe(container)).toHaveNoViolations();
  });
});
