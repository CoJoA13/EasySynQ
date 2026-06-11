import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import type { ObjectivePlan } from "../../lib/types";
import { PlansSection } from "./PlansSection";

const PLANS: ObjectivePlan[] = [
  { id: "p1", objective_id: "o1", action: "Add a second carrier", resource: "Logistics budget",
    responsible_user_id: "bbbb1111-1111-1111-1111-111111111111", due_date: "2026-09-30" },
];

it("lists each plan's action and due date", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  expect(screen.getByText("Add a second carrier")).toBeInTheDocument();
  expect(screen.getByText(/2026-09-30/)).toBeInTheDocument();
});

it("shows an empty hint when there are no plans", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={[]} />);
  expect(screen.getByText(/no plans yet/i)).toBeInTheDocument();
});

it("does not render add/remove affordances without objective.manage", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  expect(screen.queryByRole("button", { name: /add plan/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /remove plan/i })).not.toBeInTheDocument();
});
