import { screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { QuadrantCard, TileNoAccess } from "./QuadrantCard";
import { StatLine } from "./StatLine";

it("renders the PDCA chip, the RAG badge and a single Open link", () => {
  renderWithProviders(
    <QuadrantCard
      phase="PLAN"
      clauseLabel="Cl 4–7"
      rag="amber"
      openTo="/objectives"
      openLabel="Open objectives"
    >
      <StatLine value="6 / 8" label="objectives on target" tone="green" />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /plan quadrant/i });
  expect(within(card).getByText(/PLAN · Cl 4–7/)).toBeInTheDocument();
  expect(within(card).getByLabelText(/status: needs attention/i)).toBeInTheDocument();
  const open = within(card).getByRole("link", { name: /open objectives/i });
  expect(open).toHaveAttribute("href", "/objectives");
});

it("omits the RAG badge when rag is null (loading / no-access)", () => {
  renderWithProviders(
    <QuadrantCard phase="ACT" clauseLabel="Cl 10" rag={null} openTo="/capa" openLabel="Open CAPA">
      <TileNoAccess />
    </QuadrantCard>,
  );
  expect(screen.queryByLabelText(/status:/i)).not.toBeInTheDocument();
  expect(screen.getByText(/no access to this section/i)).toBeInTheDocument();
});
