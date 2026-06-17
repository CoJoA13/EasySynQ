import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import type { LeadershipAuthorizationStatus, MePermissions } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import {
  LEADERSHIP_DOC_ID,
  leadershipAuthorizedStatus,
  leadershipInProgressStatus,
  leadershipNeedsAttentionStatus,
  leadershipNotApplicableStatus,
  leadershipRequiredStatus,
} from "../../test/msw/handlers";
import { LeadershipReleaseGate } from "./LeadershipReleaseGate";

const APPROVE_PERMS = {
  scope: { level: "SYSTEM", selector: null },
  permissions: [{ key: "document.approve", effect: "ALLOW", source: "system_override" }],
} satisfies MePermissions;

function grantApprove() {
  server.use(http.get("/api/v1/me/permissions", () => HttpResponse.json(APPROVE_PERMS)));
}

function statusReturns(body: LeadershipAuthorizationStatus) {
  server.use(
    http.get("/api/v1/documents/:id/leadership-authorization", () => HttpResponse.json(body)),
  );
}

const REQUEST_BTN = /request top-management authorization/i;

describe("LeadershipReleaseGate", () => {
  test("self-suppresses for a non-leadership document (no panel rendered)", async () => {
    // The default handler returns is_leadership_artifact=false → the gate must render nothing, so it is
    // safe to embed on EVERY document/objective/MR detail. Wait for the status to be FETCHED (so this
    // proves suppression-after-resolve, not just the loading null), then assert no Alert.
    let fetched = false;
    server.use(
      http.get("/api/v1/documents/:id/leadership-authorization", () => {
        fetched = true;
        return HttpResponse.json(leadershipNotApplicableStatus);
      }),
    );
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    await waitFor(() => expect(fetched).toBe(true));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("required + unauthorized + no cycle → the Request panel (gated on document.approve)", async () => {
    statusReturns(leadershipRequiredStatus);
    grantApprove();
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/authorization required/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: REQUEST_BTN })).toBeInTheDocument();
  });

  test("hides the Request button when the caller lacks document.approve", async () => {
    // default /me/permissions = [] → no document.approve
    statusReturns(leadershipRequiredStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/authorization required/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REQUEST_BTN })).toBeNull();
    expect(screen.getByText(/an approver must start it/i)).toBeInTheDocument();
  });

  test("a cycle in progress → the awaiting panel, no Request button", async () => {
    statusReturns(leadershipInProgressStatus);
    grantApprove();
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/awaiting top-management authorization/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REQUEST_BTN })).toBeNull();
  });

  test("NEEDS_ATTENTION → a fail-closed warning", async () => {
    statusReturns(leadershipNeedsAttentionStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/no top-management member is assigned/i)).toBeInTheDocument();
  });

  test("authorized → the release-may-proceed confirmation", async () => {
    statusReturns(leadershipAuthorizedStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/release may proceed/i)).toBeInTheDocument();
  });

  test("a non-Approved leadership doc renders nothing yet (the request would 409 not-approved)", async () => {
    let fetched = false;
    server.use(
      http.get("/api/v1/documents/:id/leadership-authorization", () => {
        fetched = true;
        return HttpResponse.json(leadershipRequiredStatus);
      }),
    );
    grantApprove();
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="InReview" />,
    );
    await waitFor(() => expect(fetched).toBe(true));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a request 409 surfaces calmly (already authorized)", async () => {
    statusReturns(leadershipRequiredStatus);
    grantApprove();
    server.use(
      http.post("/api/v1/documents/:id/request-leadership-authorization", () =>
        HttpResponse.json(
          { code: "already_authorized", title: "This version is already authorized." },
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    await u.click(await screen.findByRole("button", { name: REQUEST_BTN }));
    expect(await screen.findByText(/already authorized/i)).toBeInTheDocument();
  });
});
