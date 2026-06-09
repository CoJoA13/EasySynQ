import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaLayout } from "./CapaLayout";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<div>BOARD FACE</div>} />
        <Route path="complaints" element={<div>COMPLAINTS FACE</div>} />
        <Route path="ncrs" element={<div>NCRS FACE</div>} />
      </Route>
    </Routes>
  );
}

test("renders the board face + three tabs at /capa", async () => {
  renderWithProviders(tree(), { route: "/capa" });
  expect(await screen.findByText("BOARD FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Board" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Complaints" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toBeInTheDocument();
});

test("the active tab follows the deep-linked route", async () => {
  renderWithProviders(tree(), { route: "/capa/ncrs" });
  expect(await screen.findByText("NCRS FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toHaveAttribute("aria-selected", "true");
});

test("clicking a tab navigates to that face", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  await screen.findByText("BOARD FACE");
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("COMPLAINTS FACE")).toBeInTheDocument();
});
