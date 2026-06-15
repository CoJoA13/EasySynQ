import { expect, it } from "vitest";
import { TONE_GLYPH, TONES } from "./status";

it("every canonical tone has a distinct, non-empty glyph (DP-5 non-colour channel)", () => {
  for (const t of TONES) {
    expect(TONE_GLYPH[t].length).toBeGreaterThan(0);
  }
  // Distinct glyphs are what let a colour-blind / greyscale reader tell the tones apart.
  expect(new Set(TONES.map((t) => TONE_GLYPH[t])).size).toBe(TONES.length);
});
