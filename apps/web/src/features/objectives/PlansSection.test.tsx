import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { ObjectivePlan } from "../../lib/types";
import { PlansSection } from "./PlansSection";

// suppress unused import warning — vi is used in the appended tests
void vi;

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

function grantManage() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "objective.manage", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
}

describe("with objective.manage", () => {
  it("shows Add and Remove when objective.manage is granted", async () => {
    grantManage();
    renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /add plan/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /remove plan/i })).toBeInTheDocument();
  });

  it("removes a plan via DELETE", async () => {
    grantManage();
    let deleted = false;
    server.use(
      http.delete("/api/v1/objectives/:id/plans/:planId", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
    await waitFor(() => screen.getByRole("button", { name: /remove plan/i }));
    fireEvent.click(screen.getByRole("button", { name: /remove plan/i }));
    await waitFor(() => expect(deleted).toBe(true));
  });
});
