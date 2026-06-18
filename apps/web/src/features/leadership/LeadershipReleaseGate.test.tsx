import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import type { LeadershipAuthorizationStatus } from "../../lib/types";
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

// CX-1: the Request button now gates on the server-computed `can_request` field carried on the
// leadership-authorization status (the ABAC-aware "holds document.approve at this doc's scope"
// answer) — NOT a SYSTEM-scoped /me/permissions probe. So these tests drive button visibility
// purely through the status fixtures' `can_request`, with no /me/permissions mock.
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

  test("required + unauthorized + no cycle + can_request → the Request panel", async () => {
    // can_request:true (the server-computed, scope-aware capability) → the button shows.
    statusReturns(leadershipRequiredStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/authorization required/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: REQUEST_BTN })).toBeInTheDocument();
  });

  test("hides the Request button when can_request is false (caller lacks approve at scope)", async () => {
    // CX-1: the gate reads the server's per-doc capability, not a SYSTEM-scoped /me/permissions probe.
    statusReturns({ ...leadershipRequiredStatus, can_request: false });
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/authorization required/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REQUEST_BTN })).toBeNull();
    expect(screen.getByText(/an approver must start it/i)).toBeInTheDocument();
  });

  test("a cycle in progress → the awaiting panel, no Request button", async () => {
    // can_request is true in this fixture → proves the in-progress STATE suppresses the button.
    statusReturns(leadershipInProgressStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/awaiting top-management authorization/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REQUEST_BTN })).toBeNull();
  });

  test("NEEDS_ATTENTION → a warning AND a re-request affordance (CX-2)", async () => {
    statusReturns(leadershipNeedsAttentionStatus);
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="Approved" />,
    );
    expect(await screen.findByText(/no top-management member was assigned/i)).toBeInTheDocument();
    // CX-2: NEEDS_ATTENTION is terminal/re-requestable — the approver can retry once an admin assigns
    // a member, so the Request button must remain (not just a dead-end warning).
    expect(screen.getByRole("button", { name: REQUEST_BTN })).toBeInTheDocument();
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
    renderWithProviders(
      <LeadershipReleaseGate documentId={LEADERSHIP_DOC_ID} currentState="InReview" />,
    );
    await waitFor(() => expect(fetched).toBe(true));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a request 409 surfaces calmly AND refetches the status (CR-1 / CX-5)", async () => {
    let statusFetches = 0;
    server.use(
      http.get("/api/v1/documents/:id/leadership-authorization", () => {
        statusFetches += 1;
        return HttpResponse.json(leadershipRequiredStatus);
      }),
    );
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
    // CR-1/CX-5: the request's onSettled invalidation refetches the status even on a 409, so a
    // concurrent approver's progress can't leave the panel stale.
    await waitFor(() => expect(statusFetches).toBeGreaterThan(1));
  });
});
