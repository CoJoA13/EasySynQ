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
