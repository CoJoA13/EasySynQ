import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { ReviewOutput } from "../../lib/types";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const REVIEW_ID = "mr000001-0001-0001-0001-000000000001";
// Pinned to the REAL ReviewOutput type (lib/types.ts:1226-1235 — 8 fields incl. management_review_id).
const action = {
  id: "out00001-0001-0001-0001-000000000001",
  management_review_id: REVIEW_ID,
  output_type: "ACTION",
  description: "Revise the calibration SOP.",
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  due_date: null,
  spawned_task_id: null,
  spawned_capa_id: null,
} satisfies ReviewOutput;

it("raises a DCR from a tracked ACTION output and deep-links to it", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <>
      <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[action]} editable={false} tracking />
      <LocationProbe />
    </>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Raise DCR" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "From this MR action.");
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01");
});

it("does not show Raise DCR when the review is not tracking", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[action]} editable={false} tracking={false} />,
  );
  await screen.findByText("Revise the calibration SOP.");
  expect(screen.queryByRole("button", { name: "Raise DCR" })).toBeNull();
});
