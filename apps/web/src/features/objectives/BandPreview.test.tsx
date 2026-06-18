import { expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { TONE_GLYPH } from "../../lib/status";
import { BandPreview } from "./BandPreview";

it("draws a three-zone preview with the canonical glyph per zone + the threshold/target labels", () => {
  renderWithProviders(<BandPreview target="95" threshold="90" direction="HIGHER_IS_BETTER" />);
  expect(screen.getByText(/90 at-risk/i)).toBeInTheDocument();
  expect(screen.getByText(/95 target/i)).toBeInTheDocument();
  // a labelled meter for screen readers — its accessible name carries the MEANING per zone, never the
  // raw colour key, so the AT channel gets the same non-colour meaning as the visual glyphs (Codex P2).
  const band = screen.getByRole("img", { name: /status band/i });
  expect(band).toBeInTheDocument();
  const bandName = band.getAttribute("aria-label") ?? "";
  expect(bandName).toMatch(/Action required/);
  expect(bandName).toMatch(/On track/);
  expect(bandName).not.toMatch(/\b(red|amber|green)\b/i);
  // the DP-5 non-colour channel: each zone carries its canonical glyph (red ✕ / amber ◔ / green ✓),
  // so the worse→better band reads in greyscale, not by colour alone. Scoped to the band — the "95
  // target ✓" caption below it also renders a ✓.
  expect(within(band).getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  expect(within(band).getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(within(band).getByText(TONE_GLYPH.success)).toBeInTheDocument();
});

it("shows the soft warning when the threshold is on the wrong side", () => {
  renderWithProviders(<BandPreview target="95" threshold="96" direction="HIGHER_IS_BETTER" />);
  expect(screen.getByText(/below the target/i)).toBeInTheDocument();
});

it("renders nothing structural when the target is not yet a number", () => {
  renderWithProviders(<BandPreview target="" threshold="" direction="HIGHER_IS_BETTER" />);
  expect(document.querySelector("[role='img']")).not.toBeInTheDocument();
  expect(document.querySelector("svg")).not.toBeInTheDocument();
});
