import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { SpawnDcrModal } from "./SpawnDcrModal";
import { useRaiseDcrFromCapa } from "./mutations";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

// A tiny host that creates the mutation (hooks must be top-level) and mounts the modal.
function Host() {
  const m = useRaiseDcrFromCapa("capa0001-0001-0001-0001-000000000001");
  return (
    <>
      <SpawnDcrModal title="Raise from CAPA" mutation={m} onClose={() => {}} />
      <LocationProbe />
    </>
  );
}

it("spawns a CREATE DCR from a CAPA and deep-links to the new DCR", async () => {
  renderWithProviders(<Host />);
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "Spawned from a CAPA.");
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  // waitFor the navigate to land (findByTestId resolves on the probe existing, racing the navigate).
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/dcrs?dcr=dcrNEW01-0001-0001-0001-000000000099",
    ),
  );
});

it("shows a calm error when the spawn fails and does not navigate", async () => {
  server.use(
    http.post("/api/v1/capas/:id/raise-dcr", () =>
      HttpResponse.json(
        {
          code: "capa_terminal",
          title: "Conflict",
          detail: "A closed CAPA cannot spawn a change request.",
        },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<Host />);
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "x");
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  expect(
    await screen.findByText("A closed CAPA cannot spawn a change request."),
  ).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("/"); // never navigated to /dcrs
});
