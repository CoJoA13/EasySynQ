import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
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
  // Each status carries its meaning on three redundant channels: a text label, a non-colour glyph, and
  // the accessible name — so the badge survives colour-blindness and a greyscale audit-export (DP-5).
  expect(screen.getByLabelText("Coverage: Covered")).toHaveTextContent("Covered");
  expect(screen.getByText(TONE_GLYPH.success)).toBeInTheDocument();

  expect(screen.getByLabelText("Coverage: Partial")).toHaveTextContent("Partial");
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();

  expect(screen.getByLabelText("Coverage: Gap")).toHaveTextContent("Gap");
  expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
});
