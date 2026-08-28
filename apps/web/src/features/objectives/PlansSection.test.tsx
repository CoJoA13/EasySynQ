import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { ObjectivePlan } from "../../lib/types";
import { MutationFeedbackOutlet } from "../../lib/mutationFeedback";
import { PlansSection } from "./PlansSection";

const PLANS: ObjectivePlan[] = [
  {
    id: "p1",
    objective_id: "o1",
    action: "Add a second carrier",
    resource: "Logistics budget",
    responsible_user_id: "bbbb1111-1111-1111-1111-111111111111",
    due_date: "2026-09-30",
  },
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
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /add plan/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /remove plan/i })).toBeInTheDocument();
  });

  it("removes a plan via DELETE after the operator confirms", async () => {
    grantManage();
    let deleted = false;
    server.use(
      http.delete("/api/v1/objectives/:id/plans/:planId", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
    // [U17] The row button names the PLAN — a bare "Remove plan" repeated per row is ambiguous
    // for getByLabelText and for assistive tech.
    await waitFor(() => screen.getByRole("button", { name: "Remove plan: Add a second carrier" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove plan: Add a second carrier" }));

    // [U17] The delete is permanent and now needs an explicit confirmation.
    const dialog = await screen.findByRole("dialog", { name: /remove this plan/i });
    expect(deleted).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove plan" }));
    await waitFor(() => expect(deleted).toBe(true));
  });

  it("[U17] cancelling the confirmation leaves the plan alone", async () => {
    grantManage();
    let deleted = false;
    server.use(
      http.delete("/api/v1/objectives/:id/plans/:planId", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove plan: Add a second carrier" }),
    );
    const dialog = await screen.findByRole("dialog", { name: /remove this plan/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /remove this plan/i })).not.toBeInTheDocument(),
    );
    expect(deleted).toBe(false);
  });

  it("[U17] surfaces a failed removal instead of swallowing it", async () => {
    grantManage();
    server.use(
      http.delete("/api/v1/objectives/:id/plans/:planId", () =>
        HttpResponse.json({ code: "permission_denied" }, { status: 403 }),
      ),
    );
    // The failure renders through the shared mutation-feedback channel, whose OUTLET lives in
    // AppShell — mount it alongside so this isolated render sees what the app would show.
    renderWithProviders(
      <>
        <PlansSection objectiveId="o1" plans={PLANS} />
        <MutationFeedbackOutlet />
      </>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove plan: Add a second carrier" }),
    );
    const dialog = await screen.findByRole("dialog", { name: /remove this plan/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove plan" }));
    // The mutation had NO onError at all: the row simply stayed put with nothing said.
    expect(
      await screen.findByText(/this plan was not removed: add a second carrier/i),
    ).toBeInTheDocument();
  });
});
