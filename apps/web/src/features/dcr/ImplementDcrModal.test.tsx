import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ImplementDcrModal } from "./ImplementDcrModal";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";

it("REVISE: submits an empty body and closes", async () => {
  let body: unknown;
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ id: DCR_ID, state: "Implemented" });
    }),
  );
  let closed = false;
  renderWithProviders(
    <ImplementDcrModal dcrId={DCR_ID} changeType="REVISE" onClose={() => (closed = true)} />,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Implement" }));
  await waitFor(() => expect(closed).toBe(true));
  expect(body).toEqual({});
});

it("RETIRE: a 409 obsoletion_blocked reveals force-retire + a required justification, then re-submits", async () => {
  let attempts = 0;
  let lastBody: { force_retire?: boolean; override_justification?: string } = {};
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, async ({ request }) => {
      attempts += 1;
      lastBody = (await request.json()) as typeof lastBody;
      if (attempts === 1)
        return HttpResponse.json(
          // api.ts builds ApiError.message from problem.detail ?? title (NOT `message`), so the
          // surfaced gap text must be in `detail` (matches the real obsoletion_blocked body).
          {
            code: "obsoletion_blocked",
            detail: "Obsoletion would create a coverage gap: SOP-1 is the sole coverer of 7.5.",
          },
          { status: 409 },
        );
      return HttpResponse.json({ id: DCR_ID, state: "Implemented" });
    }),
  );
  renderWithProviders(<ImplementDcrModal dcrId={DCR_ID} changeType="RETIRE" onClose={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Retire document" }));
  // The server gap message + the escalation appear.
  expect(await screen.findByText(/coverage gap/)).toBeInTheDocument();
  const force = await screen.findByLabelText(/Force-retire anyway/);
  await userEvent.click(force);
  // Submit is blocked until a justification is typed.
  await userEvent.type(
    screen.getByLabelText(/Justification/),
    "Superseded by SOP-2 effective today",
  );
  await userEvent.click(screen.getByRole("button", { name: "Force-retire" }));
  await waitFor(() => expect(attempts).toBe(2));
  expect(lastBody).toEqual({
    force_retire: true,
    override_justification: "Superseded by SOP-2 effective today",
  });
});
