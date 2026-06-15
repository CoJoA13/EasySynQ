import { expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { Route, Routes } from "react-router-dom";
import type { Objective } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import {
  objectiveDetailFixture,
  objectiveUnderRevisionDetailFixture,
} from "../../test/msw/handlers";
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
  // the period appears in both the measurements table and the trend-chart x-axis → scope to the table.
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  expect(within(screen.getByRole("table")).getByText("2026-04-01")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a not-found alert on a 404", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Objective not found" }, { status: 404 }),
    ),
  );
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByText(/couldn't load this objective/i)).toBeInTheDocument(),
  );
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
  // #3: submitting now confirms first.
  await userEvent.click(await screen.findByRole("button", { name: "Submit" }));
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

it("#3: Release confirms first — confirming POSTs the objective release (not submit)", async () => {
  let released = false;
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Approved",
        capabilities: { submit: false, release: true, edit: false, start_revision: false },
        pending_commitment: null,
      } satisfies Objective),
    ),
    http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(null)),
    http.post("/api/v1/objectives/:id/release", () => {
      released = true;
      return HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Effective",
      } satisfies Objective);
    }),
  );
  renderAt(ID);
  await userEvent.click(await screen.findByRole("button", { name: "Release" }));
  expect(released).toBe(false); // the bare click only opens the confirm
  await userEvent.click(await screen.findByRole("button", { name: "Release objective" }));
  await waitFor(() => expect(released).toBe(true));
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
  // #3: submitting now confirms first; the error surfaces inside the dialog.
  await userEvent.click(await screen.findByRole("button", { name: "Submit" }));
  await waitFor(() => expect(screen.getByText("Commitment incomplete")).toBeInTheDocument());
  // The page stays usable — the affordance is still there for a retry.
  expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument();
});

// ---- S-obj-4 revision affordances ----

it("shows Start revision on an Effective objective with the capability", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Effective",
        effective_from: "2026-06-01T09:00:00+00:00",
      } satisfies Objective),
    ),
  );
  renderAt(ID);
  await screen.findByRole("button", { name: "Start revision" });
  expect(screen.queryByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
});

it("UnderRevision: the calm revision panel replaces the stepper; Submit is offered", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json(objectiveUnderRevisionDetailFixture),
    ),
    // Leave the approval handler at its default — it returns a COMPLETED-looking instance.
    // The calm panel must appear INSTEAD of the stepper.
  );
  renderAt(ID);
  await screen.findByText(/revision in progress/i);
  // The Alert body — there may also be a ProposedRevisionCard with similar text, so use getAllByText.
  expect(screen.getAllByText(/keeps governing/i).length).toBeGreaterThan(0);
  expect(screen.queryByText("Released to effective")).toBeNull();
  expect(screen.getByRole("button", { name: "Submit for review" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Start revision" })).not.toBeInTheDocument();
});

it("renders the proposed-revision card with was→now rows when pending_commitment diverges", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json(objectiveUnderRevisionDetailFixture),
    ),
  );
  renderAt(ID);
  // objectiveUnderRevisionDetailFixture: governing target "95 %", pending "97 %"
  await screen.findByText(/proposed revision/i);
  expect(screen.getByText("95 % → 97 %")).toBeInTheDocument();
});

it("hides Start revision without the capability", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Effective",
        effective_from: "2026-06-01T09:00:00+00:00",
        capabilities: { submit: false, release: false, edit: false, start_revision: false },
        pending_commitment: null,
      } satisfies Objective),
    ),
  );
  renderAt(ID);
  await screen.findByRole("heading", { name: "On-time delivery rate" });
  expect(screen.queryByRole("button", { name: "Start revision" })).not.toBeInTheDocument();
});

// ---- S-obj-4 EditCommitmentModal affordance ----

it("offers Edit commitment on UnderRevision and opens the modal seeded with the pending target", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json(objectiveUnderRevisionDetailFixture),
    ),
  );
  renderAt(ID);
  // Wait for the page to render the Edit button
  const editBtn = await screen.findByRole("button", { name: "Edit commitment" });
  fireEvent.click(editBtn);

  // The modal opens
  const dialog = await screen.findByRole("dialog");
  // Seeds from pending_commitment (target "97"), NOT the governing "95"
  expect(within(dialog).getByLabelText(/^target/i)).toHaveValue("97");
});

it("hides Edit commitment on an Effective objective (state gate)", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({
        ...objectiveDetailFixture,
        current_state: "Effective",
        effective_from: "2026-06-01T09:00:00+00:00",
        // submit/edit/start_revision are the SAME objective.manage answer server-side — an
        // API-faithful fixture can never split them (the #1 false-PASS class).
        capabilities: { submit: true, release: false, edit: true, start_revision: true },
        pending_commitment: null,
      } satisfies Objective),
    ),
  );
  renderAt(ID);
  await screen.findByRole("heading", { name: "On-time delivery rate" });
  expect(screen.queryByRole("button", { name: "Edit commitment" })).not.toBeInTheDocument();
});
