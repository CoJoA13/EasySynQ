import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { FacetBar } from "./FacetBar";

test("renders lifecycle filters and active chips with canonical human-readable labels", () => {
  renderWithProviders(
    <FacetBar value={{ state: "UnderRevision" }} onChange={vi.fn()} onClear={vi.fn()} />,
  );

  expect(screen.getByDisplayValue("Under revision")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Remove filter State: Under revision" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("UnderRevision")).not.toBeInTheDocument();
});
