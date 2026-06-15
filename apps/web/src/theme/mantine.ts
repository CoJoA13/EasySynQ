import {
  createTheme,
  defaultVariantColorsResolver,
  type VariantColorsResolver,
} from "@mantine/core";
import type { Tone } from "../lib/status";

// The Mantine theme reads the SAME CSS variables as Tailwind (src/theme/tokens.css) — one token
// source, never two palettes. Fonts use the mockup's system stack (no web fonts; air-gap-safe).

// One status colour source of truth. A Badge with variant="status" + color=<tone> resolves to that
// tone's AA-tuned --es-*-soft (fill) / --es-*-text (foreground) token pair — so light AND dark come
// free (tokens.css re-keys every --es-* under the dark colour scheme). `emphasisSuccess` shares the
// success pair (the ★ glyph is the distinction); `neutral` has no --es status pair, so it is
// synthesized from the recessed-surface + secondary-text tokens (≈6:1 AA in both schemes).
const TONE_TOKENS: Record<Tone, { bg: string; fg: string }> = {
  success: { bg: "var(--es-success-soft)", fg: "var(--es-success-text)" },
  warning: { bg: "var(--es-warning-soft)", fg: "var(--es-warning-text)" },
  danger: { bg: "var(--es-danger-soft)", fg: "var(--es-danger-text)" },
  info: { bg: "var(--es-info-soft)", fg: "var(--es-info-text)" },
  neutral: { bg: "var(--es-surface-2)", fg: "var(--es-text-2)" },
  emphasisSuccess: { bg: "var(--es-success-soft)", fg: "var(--es-success-text)" },
};

// EVERY non-status variant/colour falls through to Mantine's default resolver untouched — otherwise
// this would break every Alert/Button colour app-wide (pinned by the fall-through test).
export const statusVariantColorResolver: VariantColorsResolver = (input) => {
  if (input.variant === "status") {
    const pair = TONE_TOKENS[input.color as Tone] ?? TONE_TOKENS.neutral;
    return { background: pair.bg, hover: pair.bg, color: pair.fg, border: "1px solid transparent" };
  }
  return defaultVariantColorsResolver(input);
};

export const theme = createTheme({
  fontFamily: "var(--es-font-sans)",
  fontFamilyMonospace: "var(--es-font-mono)",
  primaryColor: "indigo", // closest built-in to the mockup accent #4f5bd5; exact accent via tokens
  defaultRadius: "md",
  variantColorResolver: statusVariantColorResolver,
});
