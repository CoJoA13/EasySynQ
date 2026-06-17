import { expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { useLocation } from "react-router-dom";
import type { Initiative, ReviewOutput } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const REVIEW_ID = "mr-0001-0001-0001-000000000001";

// directoryFixture (handlers.ts) carries Mara (bbbb1111…) + Diego (bbbb2222…). Pin the ACTION owner
// to a real directory id so nameOf resolves to the display name (not the "a user" fallback).
const DECISION: ReviewOutput = {
  id: "ro-1",
  management_review_id: REVIEW_ID,
  output_type: "DECISION",
  description: "Approve the objectives for 2026",
  owner_user_id: null,
  due_date: null,
  spawned_task_id: null,
  spawned_capa_id: null,
};
const ACTION: ReviewOutput = {
  id: "ro-2",
  management_review_id: REVIEW_ID,
  output_type: "ACTION",
  description: "Refresh the supplier evaluation register",
  owner_user_id: "bbbb2222-2222-2222-2222-222222222222",
  due_date: "2026-09-01",
  spawned_task_id: null,
  spawned_capa_id: null,
};
const IMPROVEMENT: ReviewOutput = {
  id: "ro-3",
  management_review_id: REVIEW_ID,
  output_type: "IMPROVEMENT",
  description: "Pilot a digital nonconformity intake form",
  owner_user_id: null,
  due_date: null,
  spawned_task_id: null,
  spawned_capa_id: null,
};

const ALL = [DECISION, ACTION, IMPROVEMENT];

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

it("renders outputs grouped under their DECISION / ACTION / IMPROVEMENT headings", async () => {
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={false} />);
  expect(screen.getByText(/Review outputs/i)).toBeInTheDocument();
  // The group labels (OUTPUT_LABEL from ./labels) — "Decision" / "Action" / "Improvement opportunity".
  expect(screen.getByText("Decision")).toBeInTheDocument();
  expect(screen.getByText("Action")).toBeInTheDocument();
  expect(screen.getByText("Improvement opportunity")).toBeInTheDocument();
  // Each output description renders.
  expect(screen.getByText("Approve the objectives for 2026")).toBeInTheDocument();
  expect(screen.getByText("Refresh the supplier evaluation register")).toBeInTheDocument();
  expect(screen.getByText("Pilot a digital nonconformity intake form")).toBeInTheDocument();
});

it("shows the ACTION owner name (via the directory) and the due date", async () => {
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} />,
  );
  // owner resolves to the directory display_name; due renders verbatim.
  await waitFor(() => expect(screen.getByText(/Diego Owner/)).toBeInTheDocument());
  expect(screen.getByText(/due 2026-09-01/)).toBeInTheDocument();
});

it("omits empty groups (no heading when no outputs of that type)", () => {
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[DECISION]} editable={false} />,
  );
  expect(screen.getByText("Decision")).toBeInTheDocument();
  expect(screen.queryByText("Action")).not.toBeInTheDocument();
  expect(screen.queryByText("Improvement opportunity")).not.toBeInTheDocument();
});

it("shows the empty-state line when there are no outputs", () => {
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={[]} editable={false} />);
  expect(screen.getByText(/No outputs recorded yet/i)).toBeInTheDocument();
});

it("hides 'Add output' when the caller lacks mgmtReview.record_outputs even if editable", async () => {
  // default me/permissions returns no keys
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={true} />);
  // Give the permission query a tick to resolve to the empty set.
  await waitFor(() => expect(screen.getByText("Decision")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /Add output/i })).not.toBeInTheDocument();
});

it("hides 'Add output' when not editable even with the permission", async () => {
  grant("mgmtReview.record_outputs");
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={false} />);
  await waitFor(() => expect(screen.getByText("Decision")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /Add output/i })).not.toBeInTheDocument();
});

it("shows 'Add output' + per-row Remove when editable and the permission is granted", async () => {
  grant("mgmtReview.record_outputs");
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={true} />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Add output/i })).toBeInTheDocument(),
  );
  // one Remove per output
  expect(screen.getAllByRole("button", { name: /Remove/i })).toHaveLength(ALL.length);
});

it("opens AddOutputModal and requires an owner before save is enabled for an ACTION", async () => {
  grant("mgmtReview.record_outputs");
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={true} />);
  const add = await screen.findByRole("button", { name: /Add output/i });
  await userEvent.click(add);

  const dialog = await screen.findByRole("dialog");
  // A DECISION (the default type) needs only a description to save.
  const description = within(dialog).getByLabelText(/Description/i);
  await userEvent.type(description, "Adopt the revised audit programme");
  // Save enabled for a DECISION with a description.
  expect(within(dialog).getByRole("button", { name: /^Add$|Save|Add output/i })).toBeEnabled();

  // Switch to ACTION → save disabled until an owner is chosen.
  await userEvent.click(within(dialog).getByRole("radio", { name: /Action/i }));
  const save = within(dialog).getByRole("button", { name: /^Add$|Save|Add output/i });
  expect(save).toBeDisabled();
});

it("shows Raise CAPA on an ACTION row when tracking + capa.create, and View CAPA when spawned", async () => {
  grant("capa.create");
  const spawned: ReviewOutput = { ...ACTION, id: "ro-9", spawned_capa_id: "capa-77" };
  renderWithProviders(
    <ReviewOutputsSection
      reviewId={REVIEW_ID}
      outputs={[ACTION, spawned]}
      editable={false}
      tracking
    />,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Raise CAPA" })).toBeInTheDocument(),
  );
  const view = screen.getByRole("link", { name: /View CAPA/ });
  expect(view).toHaveAttribute("href", "/capa?capa=capa-77");
});

it("hides Raise CAPA without capa.create", async () => {
  grant("mgmtReview.read");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} tracking />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Raise CAPA" })).not.toBeInTheDocument();
});

it("has no accessibility violations with the Raise affordance", async () => {
  grant("capa.create");
  const { container } = renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} tracking />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});

it("keeps the View CAPA link on a closed (non-tracking) review (Codex P2)", async () => {
  grant("capa.create");
  const spawned: ReviewOutput = { ...ACTION, id: "ro-9", spawned_capa_id: "capa-77" };
  renderWithProviders(
    <ReviewOutputsSection
      reviewId={REVIEW_ID}
      outputs={[spawned]}
      editable={false}
      tracking={false}
    />,
  );
  // even with tracking=false (a Closed review) the deep-link to the already-spawned CAPA survives
  const view = await screen.findByRole("link", { name: /View CAPA/ });
  expect(view).toHaveAttribute("href", "/capa?capa=capa-77");
  // ...but Raise is NOT offered outside the tracking window
  expect(screen.queryByRole("button", { name: "Raise CAPA" })).not.toBeInTheDocument();
});

// ---- S-improvement-3b raise-initiative affordance (canRaiseInitiative = tracking && improvement.manage) ----
it("shows Raise initiative on ACTION + IMPROVEMENT outputs when tracking + improvement.manage", async () => {
  grant("improvement.manage");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={ALL} editable={false} tracking />,
  );
  // ALL = DECISION + ACTION + IMPROVEMENT → exactly 2 Raise-initiative buttons (the DECISION row has
  // none; the DECISION-only test below confirms zero in isolation).
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Raise initiative" })).toHaveLength(2),
  );
});

it("hides Raise initiative for a DECISION output", async () => {
  grant("improvement.manage");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[DECISION]} editable={false} tracking />,
  );
  await waitFor(() => expect(screen.getByText("Decision")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Raise initiative" })).not.toBeInTheDocument();
});

it("hides Raise initiative when the review is not tracking", async () => {
  grant("improvement.manage");
  renderWithProviders(
    <ReviewOutputsSection
      reviewId={REVIEW_ID}
      outputs={[ACTION, IMPROVEMENT]}
      editable={false}
      tracking={false}
    />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Raise initiative" })).not.toBeInTheDocument();
});

it("hides Raise initiative without improvement.manage", async () => {
  grant("mgmtReview.read");
  renderWithProviders(
    <ReviewOutputsSection
      reviewId={REVIEW_ID}
      outputs={[ACTION, IMPROVEMENT]}
      editable={false}
      tracking
    />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Raise initiative" })).not.toBeInTheDocument();
});

it("raises an initiative from a tracked IMPROVEMENT output and deep-links to it", async () => {
  grant("improvement.manage");
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <ReviewOutputsSection
        reviewId={REVIEW_ID}
        outputs={[IMPROVEMENT]}
        editable={false}
        tracking
      />
      <LocationProbe />
    </>,
  );
  await u.click(await screen.findByRole("button", { name: "Raise initiative" }));
  // Only the modal renders a "Title" field; its submit is "Raise" (distinct from the trigger).
  await u.type(await screen.findByLabelText(/^Title/), "Pilot the digital intake form");
  await u.click(screen.getByRole("button", { name: "Raise" }));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/improvement?initiative=10000000-0000-0000-0000-0000000000f2",
    ),
  );
});

it("sends the SELECTED process_id when a process is chosen in the MR raise picker", async () => {
  grant("improvement.manage");
  // Capture the POST body to assert the optional process picker actually wires process_id through.
  // processesFixture (the default GET /processes handler) carries "Production" → this id.
  const PRODUCTION_ID = "pr000002-0002-0002-0002-000000000002";
  let seenBody: { process_id?: string | null } | null = null;
  const OUTPUT_ID = ACTION.id;
  server.use(
    http.post(
      `/api/v1/management-reviews/${REVIEW_ID}/outputs/${OUTPUT_ID}/raise-initiative`,
      async ({ request }) => {
        seenBody = (await request.json()) as { process_id?: string | null };
        return HttpResponse.json(
          {
            id: "10000000-0000-0000-0000-0000000000f2",
            identifier: "IMP-2026-0011",
            title: "Refresh the supplier evaluation register",
            description: null,
            target_outcome: null,
            source: "review",
            source_link_id: OUTPUT_ID,
            process_id: PRODUCTION_ID,
            owner_user_id: null,
            stage: "Open",
            opened_at: "2026-06-17T09:00:00Z",
            closed_at: null,
            created_by: "20000000-0000-0000-0000-0000000000aa",
            created_at: "2026-06-17T09:00:00Z",
            updated_at: null,
          } satisfies Initiative,
          { status: 201 },
        );
      },
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} tracking />
      <LocationProbe />
    </>,
  );
  await u.click(await screen.findByRole("button", { name: "Raise initiative" }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/^Title/), "Pilot the digital intake form");
  // Open the optional Process Mantine Select and pick a real fixture process (the AuditDetailPage
  // Select pattern: click the labelled control to open the combobox, then click the option).
  await u.click(within(dialog).getByLabelText("Process (optional)"));
  await u.click(await screen.findByRole("option", { name: "Production" }));
  await u.click(within(dialog).getByRole("button", { name: "Raise" }));
  await waitFor(() => expect(seenBody).not.toBeNull());
  expect(seenBody!.process_id).toBe(PRODUCTION_ID);
});
