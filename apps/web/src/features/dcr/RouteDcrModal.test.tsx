import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RouteDcrModal } from "./RouteDcrModal";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";

it("confirms routing and closes on success", async () => {
  let posted = false;
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/route`, () => {
      posted = true;
      return HttpResponse.json({ id: DCR_ID, state: "InApproval" });
    }),
  );
  let closed = false;
  renderWithProviders(
    <RouteDcrModal dcrId={DCR_ID} significance="MINOR" onClose={() => (closed = true)} />,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Route for approval" }));
  await waitFor(() => expect(closed).toBe(true));
  expect(posted).toBe(true);
});

it("surfaces a 409 dcr_no_approvers calmly", async () => {
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/route`, () =>
      HttpResponse.json(
        { code: "dcr_no_approvers", title: "No approver is assigned to the routed role." },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<RouteDcrModal dcrId={DCR_ID} significance="MINOR" onClose={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Route for approval" }));
  expect(await screen.findByText(/No approver is assigned/)).toBeInTheDocument();
});
