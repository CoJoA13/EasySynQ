import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { AuditsLayout } from "./AuditsLayout";

function harness(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="audits" element={<AuditsLayout />}>
        <Route index element={<div>AUDITS-FACE</div>} />
        <Route path="programme" element={<div>PROGRAMME-FACE</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

test("renders the two tabs with the index face active at /audits", async () => {
  harness("/audits");
  expect(await screen.findByText("AUDITS-FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Audits" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Programme" })).toHaveAttribute("aria-selected", "false");
});

test("deep-link /audits/programme selects the Programme tab", async () => {
  harness("/audits/programme");
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Programme" })).toHaveAttribute("aria-selected", "true");
});

test("clicking a tab navigates the outlet", async () => {
  const u = userEvent.setup();
  harness("/audits");
  await u.click(screen.getByRole("tab", { name: "Programme" }));
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
});

test("the static programme route outranks /audits/:id (the route-shadow guard)", async () => {
  renderWithProviders(
    <Routes>
      <Route path="audits" element={<AuditsLayout />}>
        <Route index element={<div>AUDITS-FACE</div>} />
        <Route path="programme" element={<div>PROGRAMME-FACE</div>} />
      </Route>
      <Route path="audits/:id" element={<div>DETAIL-FACE</div>} />
    </Routes>,
    { route: "/audits/programme" },
  );
  expect(await screen.findByText("PROGRAMME-FACE")).toBeInTheDocument();
  expect(screen.queryByText("DETAIL-FACE")).toBeNull();
});
