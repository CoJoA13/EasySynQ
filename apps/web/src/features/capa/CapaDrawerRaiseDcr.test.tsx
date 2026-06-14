import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { CapaDrawer } from "./CapaDrawer";

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

const CAPA_ID = "capa0001-0001-0001-0001-000000000001";

it("hides Raise change request without changeRequest.create", async () => {
  renderWithProviders(<CapaDrawer capaId={CAPA_ID} onClose={() => {}} />);
  await screen.findByText(/Close gate/i); // wait for the drawer body to load
  expect(screen.queryByRole("button", { name: "Raise change request" })).toBeNull();
});

it("raises a DCR from the CAPA and deep-links to it", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <>
      <CapaDrawer capaId={CAPA_ID} onClose={() => {}} />
      <LocationProbe />
    </>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Raise change request" }));
  const dialog = await screen.findByRole("dialog", { name: /Raise a change request from this CAPA/i });
  await userEvent.click(await within(dialog).findByRole("radio", { name: "Create" }));
  await userEvent.type(within(dialog).getByLabelText(/Reason for change/), "From this CAPA.");
  await userEvent.click(within(dialog).getByRole("button", { name: "Raise" }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01");
});
