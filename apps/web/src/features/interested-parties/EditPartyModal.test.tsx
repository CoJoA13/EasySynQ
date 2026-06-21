import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { interestedPartyListFixture } from "../../test/msw/handlers";

import { EditPartyModal } from "./EditPartyModal";

const ACME = interestedPartyListFixture.data[0]!; // customer · high · active · last_reviewed 2026-06-01

it("Save is disabled until a field changes (dirty gating)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<EditPartyModal opened onClose={() => {}} party={ACME} />);
  const save = await screen.findByRole("button", { name: "Save changes" });
  expect(save).toBeDisabled();
  // flip the status to Closed → dirty
  await user.click(screen.getByText("Closed"));
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it("sends ONLY changed fields (a partial PATCH) and null-clears an emptied date", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.patch("/api/v1/interested-parties/:id", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(ACME);
    }),
  );
  const user = userEvent.setup();
  renderWithProviders(<EditPartyModal opened onClose={() => {}} party={ACME} />);
  // close the party AND clear the last-reviewed date (was set → expect last_reviewed_at:null)
  await user.click(await screen.findByText("Closed"));
  await user.clear(screen.getByLabelText("Last reviewed"));
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => expect(body).not.toBeNull());
  // exactly the two changed fields — no untouched party_type/party_name/needs/influence
  expect(body).toEqual({ status: "closed", last_reviewed_at: null });
});
