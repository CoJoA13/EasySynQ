import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrStageTimeline } from "./DcrStageTimeline";
import type { DcrStageEvent, DirectoryUser } from "../../lib/types";

const directory: DirectoryUser[] = [{ id: "u-1", display_name: "Priya Author" }];

it("renders genesis as the to-state only, and a transition as from→to with the resolved actor", () => {
  const events: DcrStageEvent[] = [
    { id: "e1", from_state: null, to_state: "Open", actor_id: "u-1", comment: "Raised", payload: null, occurred_at: "2026-06-10T09:00:00+00:00" },
    { id: "e2", from_state: "Open", to_state: "Assessed", actor_id: null, comment: null, payload: null, occurred_at: "2026-06-11T09:00:00+00:00" },
  ];
  const { getByText } = renderWithProviders(<DcrStageTimeline events={events} directory={directory} />);
  expect(getByText("Open")).toBeInTheDocument();
  expect(getByText("Open → Assessed")).toBeInTheDocument();
  expect(getByText(/Priya Author/)).toBeInTheDocument();
  expect(getByText(/system/)).toBeInTheDocument();
  expect(getByText("Raised")).toBeInTheDocument();
});

it("shows an empty state when there are no events", () => {
  const { getByText } = renderWithProviders(<DcrStageTimeline events={[]} directory={directory} />);
  expect(getByText("No history yet.")).toBeInTheDocument();
});
