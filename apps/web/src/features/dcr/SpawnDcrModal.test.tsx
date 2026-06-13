import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
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
  await userEvent.click(screen.getByRole("button", { name: "Raise change request" }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01-0001-0001-0001-000000000099");
});
