import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { contextListFixture } from "../../test/msw/handlers";

import { EditIssueModal } from "./EditIssueModal";

const STRENGTH = contextListFixture.data[0]!; // internal · strength · active · last_reviewed 2026-06-01

it("Save is disabled until a field changes (dirty gating)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<EditIssueModal opened onClose={() => {}} issue={STRENGTH} />);
  const save = await screen.findByRole("button", { name: "Save changes" });
  expect(save).toBeDisabled();
  // flip the status to Closed → dirty
  await user.click(screen.getByText("Closed"));
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it("sends ONLY changed fields (a partial PATCH) and null-clears an emptied date", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.patch("/api/v1/context/:id", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(STRENGTH);
    }),
  );
  const user = userEvent.setup();
  renderWithProviders(<EditIssueModal opened onClose={() => {}} issue={STRENGTH} />);
  // close the issue AND clear the last-reviewed date (was set → expect last_reviewed_at:null)
  await user.click(await screen.findByText("Closed"));
  await user.clear(screen.getByLabelText("Last reviewed"));
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => expect(body).not.toBeNull());
  // exactly the two changed fields — no untouched classification/description/category
  expect(body).toEqual({ status: "closed", last_reviewed_at: null });
});
