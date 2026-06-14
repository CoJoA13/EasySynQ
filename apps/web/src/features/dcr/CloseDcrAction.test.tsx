import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { CloseDcrAction } from "./CloseDcrAction";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";

it("closes on success", async () => {
  let posted = false;
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/close`, () => {
      posted = true;
      return HttpResponse.json({ id: DCR_ID, state: "Closed" });
    }),
  );
  renderWithProviders(<CloseDcrAction dcrId={DCR_ID} />);
  await userEvent.click(await screen.findByRole("button", { name: "Close change request" }));
  await waitFor(() => expect(posted).toBe(true));
});

it("surfaces the 409 dcr_effectivity_pending message verbatim", async () => {
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/close`, () =>
      HttpResponse.json(
        // api.ts builds ApiError.message from problem.detail ?? title (NOT `message`) — use `detail`.
        {
          code: "dcr_effectivity_pending",
          detail: "The resulting version is not yet Effective (the scheduled cutover is pending).",
        },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<CloseDcrAction dcrId={DCR_ID} />);
  await userEvent.click(await screen.findByRole("button", { name: "Close change request" }));
  expect(await screen.findByText(/not yet Effective/)).toBeInTheDocument();
});
