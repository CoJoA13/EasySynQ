import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { Route, Routes } from "react-router-dom";
import type { Objective } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { objectiveDetailFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { ObjectiveDetailPage } from "./ObjectiveDetailPage";

const ID = "ob000001-0001-0001-0001-000000000001";

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/objectives/:id" element={<ObjectiveDetailPage />} />
    </Routes>,
    { route: `/objectives/${id}` },
  );
}

it("renders the header, commitment, plans and measurements", async () => {
  const { container } = renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByRole("heading", { name: "On-time delivery rate" })).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();
  expect(screen.getByText("Add a second carrier to the south region")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("2026-04-01")).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a not-found alert on a 404", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Objective not found" }, { status: 404 }),
    ),
  );
  renderAt(ID);
  await waitFor(() => expect(screen.getByText(/couldn't load this objective/i)).toBeInTheDocument());
});

// ---- S-obj-3 lifecycle affordances ----

it("shows Submit for review on a Draft and hides it once the re-fetch lands InReview", async () => {
  // No approval cycle yet — the Lifecycle card carries only the Submit affordance.
  server.use(http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(null)));
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument(),
  );
  // The initial render already consumed the Draft detail; queue the post-invalidation re-fetch
  // BEFORE clicking so it lands InReview (capabilities.submit stays true — the STATE leg hides it).
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "InReview",
      } satisfies Objective),
    ),
  );
  await userEvent.click(screen.getByRole("button", { name: "Submit for review" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument(),
  );
});

it("renders the approval stepper when a cycle exists", async () => {
  renderAt(ID);
  await waitFor(() => expect(screen.getByLabelText("Approval progress")).toBeInTheDocument());
  expect(screen.getByText("Quality approval")).toBeInTheDocument();
  // The candidate pool resolves via the user directory (nameOf — the ApprovalsTab idiom).
  await waitFor(() => expect(screen.getByText("Awaiting Mara Quality")).toBeInTheDocument());
});

it("shows Release (and not Submit) on an Approved objective with release capability", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Approved",
        // submit/edit/start_revision are the SAME objective.manage answer server-side — an
        // API-faithful fixture can never split them (the #1 false-PASS class).
        capabilities: { submit: false, release: true, edit: false, start_revision: false },
        pending_commitment: null,
      } satisfies Objective),
    ),
  );
  renderAt(ID);
  await waitFor(() => expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
});

it("shows no Lifecycle card for a bare reader with no cycle", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        capabilities: { submit: false, release: false, edit: false, start_revision: false },
        pending_commitment: null,
      } satisfies Objective),
    ),
    http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(null)),
  );
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  expect(screen.queryByText("Lifecycle")).toBeNull();
});

it("surfaces a calm error when submit fails", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(null)),
    http.post("/api/v1/objectives/:id/submit-review", () =>
      HttpResponse.json({ code: "conflict", title: "Commitment incomplete" }, { status: 409 }),
    ),
  );
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Submit for review" }));
  await waitFor(() => expect(screen.getByText("Commitment incomplete")).toBeInTheDocument());
  // The page stays usable — the affordance is still there for a retry.
  expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument();
});
