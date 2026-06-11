import { axe } from "jest-axe";
import { describe, expect, test, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { docFixture } from "../../test/msw/handlers";
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

const richDoc = docFixture[0] as unknown as DocumentSummary;

describe("ControlMetadata review rows", () => {
  test("renders period, next review with badge, last reviewed", () => {
    renderWithProviders(<ControlMetadata doc={richDoc} />);
    expect(screen.getByText("Review period")).toBeInTheDocument();
    expect(screen.getByText("24 months")).toBeInTheDocument();
    expect(screen.getByText("Next review")).toBeInTheDocument();
    expect(screen.getByText("2027-03-14")).toBeInTheDocument();
    expect(screen.getByLabelText("Review state: Current")).toBeInTheDocument();
    expect(screen.getByText("Last reviewed")).toBeInTheDocument();
  });

  test("unscheduled doc renders em-dashes and no badge", () => {
    renderWithProviders(
      <ControlMetadata
        doc={{
          ...richDoc,
          review_period_months: null,
          next_review_due: null,
          last_reviewed_at: null,
          review_state: null,
        }}
      />,
    );
    expect(screen.getByText("Review period")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Review state:/)).not.toBeInTheDocument();
  });

  test("Edit affordance absent without prop, present and fires with prop", async () => {
    const onEdit = vi.fn();
    renderWithProviders(<ControlMetadata doc={richDoc} onEditReviewPeriod={onEdit} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit review period" }));
    expect(onEdit).toHaveBeenCalledOnce();
  });

  test("Edit affordance is not rendered when onEditReviewPeriod is not passed", () => {
    renderWithProviders(<ControlMetadata doc={richDoc} />);
    expect(screen.queryByRole("button", { name: "Edit review period" })).not.toBeInTheDocument();
  });
});
