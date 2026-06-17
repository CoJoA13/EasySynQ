import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import {
  initiativeAuthorizationFixture,
  initiativeCompletedFixture,
} from "../../test/msw/handlers";
import { InitiativeAdvancePanel } from "./InitiativeAdvancePanel";

// initiativeCompletedFixture.process_id is null → the cockpit scopes to SYSTEM, so a SYSTEM
// improvement.manage grant satisfies can() (mirrors the ImprovementRegisterPage.test pattern).
function grantManage() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "improvement.manage", effect: "ALLOW", source: null }],
      }),
    ),
  );
}

function noAuthorizationCycle() {
  server.use(
    http.get("/api/v1/improvement-initiatives/:id/authorization", () => HttpResponse.json(null)),
  );
}

describe("InitiativeAdvancePanel — S-improvement-4 management authorization", () => {
  test("at Completed with no authorization yet, offers BOTH the unsigned close and the request; opening request shows the modal", async () => {
    grantManage();
    noAuthorizationCycle();
    renderWithProviders(<InitiativeAdvancePanel initiative={initiativeCompletedFixture} />);
    const requestBtn = await screen.findByRole("button", {
      name: "Request management authorization",
    });
    expect(screen.getByRole("button", { name: "Close initiative" })).toBeInTheDocument();
    await userEvent.click(requestBtn);
    // The modal's confirm button has a DISTINCT accessible name from the trigger (no dup aria-label).
    expect(
      await screen.findByRole("button", { name: "Request authorization" }),
    ).toBeInTheDocument();
  });

  test("while an authorization is pending, suppresses the unsigned close + shows awaiting sign-off", async () => {
    grantManage();
    // Opt into a PENDING cycle (current_state top_mgmt_authorization) — the default is no cycle.
    server.use(
      http.get("/api/v1/improvement-initiatives/:id/authorization", () =>
        HttpResponse.json(initiativeAuthorizationFixture),
      ),
    );
    renderWithProviders(<InitiativeAdvancePanel initiative={initiativeCompletedFixture} />);
    expect(await screen.findByText(/awaiting a Top-Management sign-off/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close initiative" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Request management authorization" })).toBeNull();
  });

  test("NEEDS_ATTENTION surfaces the no-approver message and still offers a re-request", async () => {
    grantManage();
    server.use(
      http.get("/api/v1/improvement-initiatives/:id/authorization", () =>
        HttpResponse.json({
          instance_id: "50000000-0000-0000-0000-0000000000ff",
          subject_id: initiativeCompletedFixture.id,
          current_state: "NEEDS_ATTENTION",
          started_at: "2026-06-17T09:00:00Z",
          tasks: [],
        }),
      ),
    );
    renderWithProviders(<InitiativeAdvancePanel initiative={initiativeCompletedFixture} />);
    expect(await screen.findByText(/No Top-Management approver is assigned/i)).toBeInTheDocument();
    // NEEDS_ATTENTION is terminal for the cycle → a fresh request is still offered.
    expect(
      screen.getByRole("button", { name: "Request management authorization" }),
    ).toBeInTheDocument();
  });
});
