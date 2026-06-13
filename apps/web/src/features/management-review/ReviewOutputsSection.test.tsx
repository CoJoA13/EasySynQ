import { expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReviewOutput } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

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
};
const ACTION: ReviewOutput = {
  id: "ro-2",
  management_review_id: REVIEW_ID,
  output_type: "ACTION",
  description: "Refresh the supplier evaluation register",
  owner_user_id: "bbbb2222-2222-2222-2222-222222222222",
  due_date: "2026-09-01",
  spawned_task_id: null,
};
const IMPROVEMENT: ReviewOutput = {
  id: "ro-3",
  management_review_id: REVIEW_ID,
  output_type: "IMPROVEMENT",
  description: "Pilot a digital nonconformity intake form",
  owner_user_id: null,
  due_date: null,
  spawned_task_id: null,
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
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} />);
  // owner resolves to the directory display_name; due renders verbatim.
  await waitFor(() => expect(screen.getByText(/Diego Owner/)).toBeInTheDocument());
  expect(screen.getByText(/due 2026-09-01/)).toBeInTheDocument();
});

it("omits empty groups (no heading when no outputs of that type)", () => {
  renderWithProviders(<ReviewOutputsSection reviewId={REVIEW_ID} outputs={[DECISION]} editable={false} />);
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
