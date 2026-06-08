import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CoverageBadge } from "./CoverageBadge";

test("renders a non-color label + glyph + aria-label per status", () => {
  renderWithProviders(
    <>
      <CoverageBadge status="COVERED" />
      <CoverageBadge status="PARTIAL" />
      <CoverageBadge status="GAP" />
    </>,
  );
  expect(screen.getByLabelText("Coverage: Covered")).toHaveTextContent("Covered");
  expect(screen.getByLabelText("Coverage: Partial")).toHaveTextContent("Partial");
  expect(screen.getByLabelText("Coverage: Gap")).toHaveTextContent("Gap");
});
