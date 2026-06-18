import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { renderWithProviders } from "../test/render";
import { GlyphLegend } from "./GlyphLegend";
import { TONE_MEANING, TONES } from "./status";

test("the legend trigger is named and opens the canonical glyph meanings", async () => {
  const user = userEvent.setup();
  renderWithProviders(<GlyphLegend />);
  await user.click(screen.getByRole("button", { name: "Status legend" }));
  // every canonical tone's MEANING is surfaced (sourced from lib/status — one vocabulary, no drift)
  await waitFor(() => expect(screen.getByText(TONE_MEANING.success)).toBeInTheDocument());
  for (const tone of TONES) {
    expect(screen.getByText(TONE_MEANING[tone])).toBeInTheDocument();
  }
});

test("the closed legend has no axe violations", async () => {
  const { container } = renderWithProviders(<GlyphLegend />);
  expect(await axe(container)).toHaveNoViolations();
});
