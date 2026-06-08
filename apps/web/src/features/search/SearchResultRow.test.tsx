import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { SearchHit } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { SearchResultRow } from "./SearchResultRow";

const hit: SearchHit = {
  type: "document",
  id: "11111111-1111-1111-1111-111111111111",
  identifier: "SOP-PUR-014",
  title: "Supplier Selection",
  current_state: "Effective",
  clause_refs: ["8.4"],
  snippet: "…<b>Supplier</b> Selection",
  rank: 0.6,
};

test("renders identifier, a title link to the document, and a clause chip link", () => {
  renderWithProviders(<SearchResultRow hit={hit} />);
  expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
  const title = screen.getByRole("link", { name: "Supplier Selection" });
  expect(title).toHaveAttribute("href", "/documents/11111111-1111-1111-1111-111111111111");
  const clause = screen.getByRole("link", { name: /Clause 8.4/ });
  expect(clause).toHaveAttribute("href", "/library?clause=8.4");
});

test("renders the state badge and the highlighted snippet", () => {
  const { container } = renderWithProviders(<SearchResultRow hit={hit} />);
  expect(screen.getByLabelText("State: Effective")).toBeInTheDocument();
  expect(container.querySelector("mark")?.textContent).toBe("Supplier");
});
