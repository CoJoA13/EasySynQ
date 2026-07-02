import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaBoardPage } from "./CapaBoardPage";
import { CapaLayout } from "./CapaLayout";
import { ComplaintsPage } from "./ComplaintsPage";
import { NcrsPage } from "./NcrsPage";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<CapaBoardPage />} />
        <Route path="complaints" element={<ComplaintsPage />} />
        <Route path="ncrs" element={<NcrsPage />} />
      </Route>
    </Routes>
  );
}

test("navigates board → complaints → ncrs through the tab bar", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  // the board face (its own title) renders at the index route
  expect(await screen.findByText("Nonconformity and CAPA")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "NCRs" }));
  expect(await screen.findByText("NCR-000052")).toBeInTheDocument();
});
