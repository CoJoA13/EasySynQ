import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import type { Objective } from "../../lib/types";
import { objectiveDetailFixture, objectiveUnderRevisionDetailFixture } from "../../test/msw/handlers";
import { ProposedRevisionCard } from "./ProposedRevisionCard";

// A minimal Objective base for direct component render tests (no MSW needed — pure component).
const baseObjective: Objective = { ...objectiveDetailFixture };

it("renders nothing when pending_commitment is null", () => {
  renderWithProviders(
    <ProposedRevisionCard objective={{ ...baseObjective, pending_commitment: null }} />,
  );
  // The component returns null — no "Proposed revision" heading appears.
  expect(screen.queryByText(/proposed revision/i)).not.toBeInTheDocument();
});

it("renders nothing when pending_commitment is absent (undefined)", () => {
  // pending_commitment is optional on the type — undefined means no revision in flight.
  const withoutPending = { ...baseObjective } as Objective;
  delete withoutPending.pending_commitment;
  renderWithProviders(<ProposedRevisionCard objective={withoutPending} />);
  expect(screen.queryByText(/proposed revision/i)).not.toBeInTheDocument();
});

it("renders the proposed revision heading and a was→now row for a diverging target", () => {
  // objectiveUnderRevisionDetailFixture: governing target "95", pending target "97", unit "%"
  renderWithProviders(
    <ProposedRevisionCard objective={objectiveUnderRevisionDetailFixture} />,
  );
  expect(screen.getByText(/proposed revision/i)).toBeInTheDocument();
  expect(screen.getByText("95 % → 97 %")).toBeInTheDocument();
});

it("filters identical fields — same due_date on both shows no Due date row", () => {
  // The fixture has the same due_date on both governing and pending.
  renderWithProviders(
    <ProposedRevisionCard objective={objectiveUnderRevisionDetailFixture} />,
  );
  // due_date is "2026-12-31" on both sides → no "Due date" row
  expect(screen.queryByText("Due date")).not.toBeInTheDocument();
});
