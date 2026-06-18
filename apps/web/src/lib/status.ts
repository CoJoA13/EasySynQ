// The single canonical status vocabulary for the whole SPA (the one source of truth that replaces the
// ~12 drifting per-feature badge maps). A status is a (tone, glyph, label):
//   • the TONE resolves to an AA-tuned --es-*-soft (fill) / --es-*-text (foreground) token pair via the
//     variant="status" resolver in theme/mantine.ts — so light and dark come free (tokens.css re-keys
//     every --es-* under the dark colour scheme);
//   • the GLYPH is a non-colour channel (DP-5 / doc-11 DP-7 — status is NEVER colour alone, so it
//     survives colour-blindness and a greyscale audit-export);
//   • the LABEL (supplied by the caller's domain map) carries the precise meaning.
// `emphasisSuccess` shares the success colour pair — the ★ glyph is the only distinction (a stronger
// "milestone / released" read over a plain ✓), so it adds a glyph channel, not a new colour.
export type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "emphasisSuccess";

export const TONES: readonly Tone[] = [
  "success",
  "warning",
  "danger",
  "info",
  "neutral",
  "emphasisSuccess",
];

// The one canonical glyph per tone. ▲ is deliberately retired (it previously overloaded amber + danger
// + DIVERGENT across features — the core glyph drift this slice removes).
export const TONE_GLYPH: Record<Tone, string> = {
  success: "✓",
  warning: "◔",
  danger: "✕",
  info: "●",
  neutral: "○",
  emphasisSuccess: "★",
};

// The ONE human meaning per tone — the single source for the in-product glyph legend (GlyphLegend) so
// the shape channel is legible to the employee and the external auditor alike (the "legibility is the
// feature" principle). Kept here, beside the glyph map, so the legend can never drift from the canon —
// every feature's per-domain state→tone map narrows onto one of these six meanings (the S-obj-rag
// lesson: one vocabulary, no second drifting set).
export const TONE_MEANING: Record<Tone, string> = {
  success: "OK / on track",
  warning: "Needs attention",
  danger: "Action required / failed",
  info: "In progress / informational",
  neutral: "Neutral / no data",
  emphasisSuccess: "Released / milestone",
};
