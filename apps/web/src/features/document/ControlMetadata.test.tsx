import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { ControlMetadata } from "./ControlMetadata";
import type { DocumentSummary } from "../../lib/types";

const doc: DocumentSummary = {
  id: "11111111-1111-1111-1111-111111111111",
  identifier: "SOP-PUR-014",
  kind: "DOCUMENT",
  title: "Supplier Selection & Evaluation",
  document_type_id: "t1",
  area_code: "PUR",
  folder_path: "/SOPs/Purchasing",
  current_state: "Effective",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "u1",
  framework_id: "f1",
  current_effective_version_id: "v1",
  effective_from: "2026-03-14T00:00:00+00:00",
  created_at: null,
  review_period_months: null,
  next_review_due: null,
  last_reviewed_at: null,
  review_state: null,
  clause_refs: ["8.4", "8.5"],
};

test("ControlMetadata renders the control fields", () => {
  renderWithProviders(<ControlMetadata doc={doc} typeName="Procedure" ownerName="Diego" />);
  expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
  expect(screen.getByText("Procedure")).toBeInTheDocument();
  expect(screen.getByText("Diego")).toBeInTheDocument();
  expect(screen.getByText("8.4, 8.5")).toBeInTheDocument();
  expect(screen.getByText("/SOPs/Purchasing")).toBeInTheDocument();
  expect(screen.getByText("2026-03-14")).toBeInTheDocument();
});

test("ControlMetadata degrades a missing type/owner/clauses to em-dash", () => {
  renderWithProviders(<ControlMetadata doc={{ ...doc, clause_refs: [] }} />);
  // type + owner unset and clauses empty → three "—" cells
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
});

test("ControlMetadata has no a11y violations", async () => {
  const { container } = renderWithProviders(
    <ControlMetadata doc={doc} typeName="Procedure" ownerName="Diego" />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
