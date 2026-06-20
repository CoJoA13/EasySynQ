import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { riskListFixture } from "../../test/msw/handlers";

import { EditRiskModal } from "./EditRiskModal";

const HIGH = riskListFixture.data[1]!; // treated (treatment set), effectiveness null, L3×S4

it("Save is disabled until a field changes (dirty gating)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<EditRiskModal opened onClose={() => {}} risk={HIGH} />);
  const save = await screen.findByRole("button", { name: "Save changes" });
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText("Effectiveness"), "Confirmed");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it("sends ONLY changed fields (a partial PATCH) and null-clears an emptied field", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.patch("/api/v1/risks/:id", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(HIGH);
    }),
  );
  const user = userEvent.setup();
  const onClose = () => {};
  renderWithProviders(<EditRiskModal opened onClose={onClose} risk={HIGH} />);
  // change effectiveness (was null) and CLEAR the treatment (was set → expect treatment:null)
  await user.type(await screen.findByLabelText("Effectiveness"), "Verified at the next audit");
  await user.clear(screen.getByLabelText("Treatment"));
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => expect(body).not.toBeNull());
  // exactly the two changed fields — no untouched type/description/likelihood/severity/process_id
  expect(body).toEqual({ effectiveness: "Verified at the next audit", treatment: null });
});
