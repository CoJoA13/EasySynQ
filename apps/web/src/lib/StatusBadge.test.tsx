import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithProviders } from "../test/render";
import { StatusBadge } from "./StatusBadge";
import { TONE_GLYPH, TONES } from "./status";

it("renders label + aria-label + the canonical aria-hidden glyph for every tone", () => {
  for (const tone of TONES) {
    const { unmount } = renderWithProviders(<StatusBadge tone={tone} label={tone} kind="State" />);
    // The label + accessible name carry the meaning (DP-7 — never colour alone)...
    expect(screen.getByLabelText(`State: ${tone}`)).toHaveTextContent(tone);
    // ...and the glyph is a second, non-colour channel, hidden from the accessible name.
    const g = screen.getByText(TONE_GLYPH[tone]);
    expect(g).toBeInTheDocument();
    expect(g).toHaveAttribute("aria-hidden", "true");
    unmount();
  }
});

it("uses the canonical glyph by default and honours an explicit override", () => {
  const { unmount } = renderWithProviders(<StatusBadge tone="warning" label="Amber" />);
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  unmount();
  renderWithProviders(<StatusBadge tone="warning" label="Amber" glyph="⚑" />);
  expect(screen.getByText("⚑")).toBeInTheDocument();
});
