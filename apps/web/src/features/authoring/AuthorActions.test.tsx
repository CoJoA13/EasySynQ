import { screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { DocumentCapabilities, DocumentSummary } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuthorActions } from "./AuthorActions";

const baseDoc: DocumentSummary = {
  id: "33333333-3333-3333-3333-333333333333",
  identifier: "SOP-GEN-001",
  kind: "DOCUMENT",
  title: "Draft Doc",
  document_type_id: "aaaa1111-1111-1111-1111-111111111111",
  area_code: "GEN",
  folder_path: null,
  current_state: "Draft",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  framework_id: "cccc1111-1111-1111-1111-111111111111",
  current_effective_version_id: null,
  effective_from: null,
  created_at: "2026-06-07T10:00:00+00:00",
};

const allCaps: DocumentCapabilities = {
  checkout: true,
  edit: true,
  manage_metadata: true,
  submit: true,
  release: false,
  obsolete: false,
  read_draft: true,
};

it("renders nothing until capabilities are present (the seed row has none)", () => {
  renderWithProviders(<AuthorActions doc={{ ...baseDoc, capabilities: undefined }} />);
  expect(screen.queryByText("Author actions")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /check out to edit/i })).not.toBeInTheDocument();
});

it("shows the Draft author actions and is accessible; submit is gated on ≥1 clause", async () => {
  const { container } = renderWithProviders(
    <AuthorActions doc={{ ...baseDoc, capabilities: allCaps }} />,
  );
  expect(await screen.findByText("Author actions")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /check out to edit/i })).toBeInTheDocument();
  expect(screen.getByText(/add a clause/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /submit for review/i })).toBeDisabled(); // 0 clauses
  expect(await axe(container)).toHaveNoViolations();
});

it("enables submit once a clause is mapped", async () => {
  server.use(
    http.get("/api/v1/documents/:id/clause-mappings", () =>
      HttpResponse.json([
        {
          id: "cm1",
          document_id: "d",
          clause_id: "c84",
          clause_number: "8.4",
          clause_title: "x",
          is_requirement_level: false,
          framework_id: "f1",
          created_at: "2026-06-07T10:06:00+00:00",
        },
      ]),
    ),
  );
  renderWithProviders(<AuthorActions doc={{ ...baseDoc, capabilities: allCaps }} />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /submit for review/i })).toBeEnabled(),
  );
});

it("hides the clause mapper when manage_metadata is denied (no dead control)", () => {
  renderWithProviders(
    <AuthorActions doc={{ ...baseDoc, capabilities: { ...allCaps, manage_metadata: false } }} />,
  );
  expect(screen.getByRole("button", { name: /check out to edit/i })).toBeInTheDocument();
  expect(screen.queryByText(/add a clause/i)).not.toBeInTheDocument();
});

it("shows an awaiting-review notice for an InReview document", () => {
  renderWithProviders(
    <AuthorActions doc={{ ...baseDoc, current_state: "InReview", capabilities: allCaps }} />,
  );
  expect(screen.getByText(/awaiting review/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /submit for review/i })).not.toBeInTheDocument();
});

it("offers start-revision for an Effective document", () => {
  renderWithProviders(
    <AuthorActions
      doc={{
        ...baseDoc,
        current_state: "Effective",
        current_effective_version_id: "v1",
        capabilities: allCaps,
      }}
    />,
  );
  expect(screen.getByRole("button", { name: /start revision/i })).toBeInTheDocument();
});
