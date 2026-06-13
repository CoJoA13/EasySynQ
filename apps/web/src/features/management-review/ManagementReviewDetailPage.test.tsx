import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ManagementReviewDetailPage } from "./ManagementReviewDetailPage";

const ID = "mr-0001-0001-0001-000000000001";

// Grant mgmtReview.record_outputs so the Draft Compile/Submit affordances render (the default
// /me/permissions handler returns an empty grant set — the bare reader).
function grantRecordOutputs() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "mgmtReview.record_outputs", effect: "ALLOW", source: "SYSTEM" }],
      }),
    ),
  );
}

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/management-reviews/:id" element={<ManagementReviewDetailPage />} />
    </Routes>,
    { route: `/management-reviews/${id}` },
  );
}

it("renders the header, the inputs and outputs sections, and the Draft lifecycle actions", async () => {
  grantRecordOutputs();
  renderAt(ID);

  // The card renders before the data line resolves (the S-home-1 trap) — waitFor the first assertion.
  await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
  expect(
    screen.getByRole("heading", { name: "2026 Annual Management Review" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();

  // The inputs section (OBJECTIVES_STATUS + AUDIT_RESULTS available; PRIOR_ACTIONS gap).
  expect(screen.getByRole("heading", { name: "Review inputs (9.3.2)" })).toBeInTheDocument();
  // The split <Text span> nodes render in one <p> — match on the trailing text node.
  expect(screen.getByText(/objectives on target/i)).toBeInTheDocument();
  expect(screen.getByText(/Quality objectives status/i)).toBeInTheDocument();
  expect(screen.getByText(/Audit results/i)).toBeInTheDocument();

  // The outputs section (a DECISION + an ACTION output).
  expect(screen.getByRole("heading", { name: "Review outputs (9.3.3)" })).toBeInTheDocument();
  expect(screen.getByText("Approve the objectives for 2026")).toBeInTheDocument();
  expect(screen.getByText("Refresh the supplier evaluation register")).toBeInTheDocument();

  // The Draft + record_outputs lifecycle affordances.
  expect(await screen.findByRole("button", { name: "Compile inputs" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument();
  // No release/close on a Draft.
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Close review" })).not.toBeInTheDocument();
});

it("has no accessibility violations", async () => {
  grantRecordOutputs();
  const { container } = renderAt(ID);
  await screen.findByRole("button", { name: "Compile inputs" });
  expect(await axe(container)).toHaveNoViolations();
});

it("hides every lifecycle action for a bare reader with no approval cycle", async () => {
  // Default /me/permissions = empty grants; approval is null (pre-submit) by default.
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Compile inputs" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
  expect(screen.queryByText("Lifecycle")).toBeNull();
});

it("shows a calm not-found alert on a 404", async () => {
  server.use(
    http.get("/api/v1/management-reviews/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByText(/couldn't load this review/i)).toBeInTheDocument(),
  );
});

it("maps the review_close_blocked code to calm copy when an action's task isn't done", async () => {
  grantRecordOutputs();
  // Drive the review to the close-able state (released → ActionsTracked) and 409 the close.
  server.use(
    http.get("/api/v1/management-reviews/:id", () =>
      HttpResponse.json({ ...mgmtReviewClosable() }),
    ),
    http.post("/api/v1/management-reviews/:id/close", () =>
      HttpResponse.json({ code: "review_close_blocked", title: "blocked" }, { status: 409 }),
    ),
  );
  renderAt(ID);
  const closeBtn = await screen.findByRole("button", { name: "Close review" });
  await userEvent.click(closeBtn);
  await waitFor(() =>
    expect(
      screen.getByText("Close is blocked — an action output's task isn't complete yet."),
    ).toBeInTheDocument(),
  );
});

// A close-able detail: Effective + ActionsTracked (the post-release, pre-close rest state).
function mgmtReviewClosable() {
  return {
    id: ID,
    identifier: "MR-001",
    title: "2026 Annual Management Review",
    current_state: "Effective" as const,
    period_label: "2026 Annual",
    review_date: "2026-06-12",
    attendees: [{ name: "Mara", role: "QM" }],
    close_state: "ActionsTracked" as const,
    closed_at: null,
    created_at: "2026-06-01T09:00:00+00:00",
    inputs: [],
    outputs: [],
  };
}

function mgmtReviewApproved(release: boolean) {
  return {
    id: ID,
    identifier: "MR-002",
    title: "Approved review",
    current_state: "Approved" as const,
    period_label: "2026 Annual",
    review_date: "2026-06-12",
    attendees: null,
    close_state: null,
    closed_at: null,
    created_at: "2026-06-01T09:00:00+00:00",
    inputs: [],
    outputs: [],
    capabilities: { release },
  };
}

it("shows Release when capabilities.release is true and state is Approved", async () => {
  grantRecordOutputs();
  server.use(http.get("/api/v1/management-reviews/:id", () => HttpResponse.json(mgmtReviewApproved(true))));
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument(),
  );
});

it("hides Release when capabilities.release is false (SoD-2), even at Approved", async () => {
  grantRecordOutputs();
  server.use(http.get("/api/v1/management-reviews/:id", () => HttpResponse.json(mgmtReviewApproved(false))));
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("Approved review")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
});
