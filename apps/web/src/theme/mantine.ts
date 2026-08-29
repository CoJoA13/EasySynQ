import {
  Badge,
  createTheme,
  defaultVariantColorsResolver,
  Drawer,
  type MantineColorsTuple,
  Modal,
  ScrollArea,
  type VariantColorsResolver,
} from "@mantine/core";
import type { Tone } from "../lib/status";

// The Mantine theme reads the SAME CSS variables as Tailwind (src/theme/tokens.css) — one token
// source, never two palettes. The sans stack leads with Archivo, self-hosted from public/fonts/
// and declared in src/theme/fonts.css — still air-gap-safe, because it is served same-origin and
// never fetched from a CDN (the Caddy CSP is `font-src 'self'`, so a CDN request would be blocked
// anyway). The mono stack remains the system stack.

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

// The brand ramp, anchored on the mark's teal. Shade 6 IS the mark hex (#0EA394); shade 7 is the
// darkened light-scheme accent, and shade 7 is the primary in BOTH schemes (see primaryShade).
const brand: MantineColorsTuple = [
  "#e6f4f2",
  "#c9e8e4",
  "#9fd8d1",
  "#71c5bc",
  "#4bb4a9",
  "#2ba79b",
  "#0ea394",
  "#0a7a6f",
  "#08635a",
  "#064c45",
];

export const theme = createTheme({
  fontFamily: "var(--es-font-sans)",
  fontFamilyMonospace: "var(--es-font-mono)",
  colors: { brand },
  primaryColor: "brand",
  // ONE shade for both schemes, deliberately. Mantine's filled-variant resolver decides the label
  // via parseThemeColor, which resolves a shade-less theme colour with `colorScheme || "light"` and
  // is never handed the real scheme — so the label is ALWAYS computed from the LIGHT shade. With a
  // split like { light: 7, dark: 6 } the dark fill (#0ea394) is judged by the light shade
  // (#0a7a6f) and keeps a white label: 3.14:1, measured in the browser. A single shade makes that
  // mismatch impossible rather than merely unlikely.
  primaryShade: 7,

  // autoContrast picks the filled LABEL (black or white) from the fill's luminance instead of
  // always using white. It is load-bearing for two reasons.
  //
  // 1. primaryShade is THEME-GLOBAL: Mantine re-derives `-filled` for EVERY entry of theme.colors
  //    from it, not just `brand`. Moving off Mantine's defaults (light 6 / dark 8) with a forced
  //    white label would have pushed color="red" in dark from 4.51:1 to 3.84:1 — below AA.
  // 2. Several Mantine palettes ALREADY failed with a forced white label at their default shade
  //    (teal 3.95:1, orange 3.58:1, green 3.45:1, yellow 2.48:1). This app has ten filled buttons
  //    on palette colours (7 teal, 2 red, 1 orange), so that was already shipping.
  //
  // luminanceThreshold is Mantine's own isLightColor default (0.179) rather than the theme default
  // of 0.3. 0.179 is the luminance at which black and white contrast equally, so choosing by it is
  // optimal: every palette at shade 7 lands between 4.62:1 and 15.52:1.
  autoContrast: true,
  luminanceThreshold: 0.179,

  defaultRadius: "md",
  variantColorResolver: statusVariantColorResolver,

  // Every scale below is SOURCED FROM tokens.css rather than left at Mantine's defaults — that is
  // the whole point of S-ui-1: before this the token ramps existed with zero readers, so the app
  // rendered as stock Mantine. fontSizes.<k> and lineHeights.<k> are drawn from the SAME token row
  // so `size="sm"` yields a coherent size+leading pair rather than a mismatched combination.
  //
  // The mapping is SEMANTIC, not positional. Mantine's t-shirt keys size components; the --es-fs-*
  // ramp describes document type. `sm` is Mantine's default for the great majority of components
  // (402 call sites here) and therefore carries BODY, not the smaller `small` row. Mapping by
  // position instead would have shrunk the dominant text from 14px to 13px — and, because Mantine
  // derives input description/error text as `font-size-sm - 2px`, would have dropped every
  // validation message to 11px. Shrinking text in the slice that exists to remediate legibility
  // would be self-defeating, so no step in this mapping is smaller than what it replaces.
  fontSizes: {
    xs: "var(--es-fs-small)", // 13px (Mantine default 12px)
    sm: "var(--es-fs-body)", // 14px — unchanged; the dominant size
    md: "var(--es-fs-h3)", // 16px — unchanged; Mantine's base
    lg: "var(--es-fs-h2)", // 19px (18px)
    xl: "var(--es-fs-h1)", // 24px (20px) — `size="xl"` is almost all <Container>, a separate scale
  },
  lineHeights: {
    xs: "var(--es-lhr-small)",
    sm: "var(--es-lhr-body)",
    md: "var(--es-lhr-h3)",
    lg: "var(--es-lhr-h2)",
    xl: "var(--es-lhr-h1)",
  },
  headings: {
    fontFamily: "var(--es-font-sans)",
    sizes: {
      h1: {
        fontSize: "var(--es-fs-h1)",
        lineHeight: "var(--es-lh-h1)",
        fontWeight: "var(--es-fw-h1)",
      },
      h2: {
        fontSize: "var(--es-fs-h2)",
        lineHeight: "var(--es-lh-h2)",
        fontWeight: "var(--es-fw-h2)",
      },
      h3: {
        fontSize: "var(--es-fs-h3)",
        lineHeight: "var(--es-lh-h3)",
        fontWeight: "var(--es-fw-h3)",
      },
      // The token ramp stops at h3, and h4/h5 are both used (13 and 10 call sites). They must not
      // fall below the body copy they head: Mantine's own h4/h5 defaults (18px/16px) invert against
      // h3's 16px, while continuing the ramp down into small/caption would put a heading BELOW the
      // 14px `size="sm"` body text. So the deep headings hold size and separate by weight instead.
      // h6 is currently unused but is defined so it never falls back to an untokened value.
      h4: {
        fontSize: "var(--es-fs-h3)",
        lineHeight: "var(--es-lh-h3)",
        fontWeight: "var(--es-fw-semibold)",
      },
      h5: {
        fontSize: "var(--es-fs-body)",
        lineHeight: "var(--es-lh-body)",
        fontWeight: "var(--es-fw-semibold)",
      },
      h6: {
        fontSize: "var(--es-fs-small)",
        lineHeight: "var(--es-lh-small)",
        fontWeight: "var(--es-fw-bold)",
      },
    },
  },
  radius: {
    xs: "var(--es-radius-xs)",
    sm: "var(--es-radius-sm)",
    md: "var(--es-radius-md)",
    lg: "var(--es-radius-lg)",
    xl: "var(--es-radius-xl)",
  },
  spacing: {
    xs: "var(--es-space-3)",
    sm: "var(--es-space-4)",
    md: "var(--es-space-5)",
    lg: "var(--es-space-6)",
    xl: "var(--es-space-7)",
  },
  shadows: {
    xs: "var(--es-shadow-xs)",
    sm: "var(--es-shadow-sm)",
    md: "var(--es-shadow-md)",
    lg: "var(--es-shadow-lg)",
    xl: "var(--es-shadow-xl)",
  },
  // Modal/Drawer render their close controls from the dialog components themselves. Default the
  // accessible name here so every app dialog gets a named X without contaminating Mantine's shared
  // CloseButton primitive (also used by Select/FileInput clear controls, where "Close" is wrong).
  components: {
    Modal: Modal.extend({
      defaultProps: { closeButtonProps: { "aria-label": "Close" } },
    }),
    Drawer: Drawer.extend({
      defaultProps: { closeButtonProps: { "aria-label": "Close" } },
    }),
    // Mantine caps a Badge at `max-width: 100%` and ellipsises its label, so inside a squeezed
    // table cell a status reads "ACTION REQUIR…" or "DRA…" — a status a sighted reader cannot
    // read is not a status. (The accessible name is unaffected: StatusBadge sets its own
    // aria-label, so screen readers always had the full text. This is a visual defect only.)
    // Letting the badge size to its content widens the column, which widens the table, which the
    // register's Table.ScrollContainer already scrolls — the tables are horizontally scrollable by
    // design, so growth is absorbed rather than clipped.
    Badge: Badge.extend({
      styles: { root: { maxWidth: "none" }, label: { overflow: "visible" } },
    }),
    // Mantine's ScrollArea hides its scrollbar until hover (`type: "hover"`), and every register
    // table sits in a Table.ScrollContainer built on one. So a table wider than its viewport
    // scrolls with NO visual cue that it does — measured on /context at 1115px, the table is 880px
    // inside an 807px port and the "Last reviewed" column sits 73px past the clip edge with
    // `scrollbar-width: none`. The owner read that as a misspelled header ("Last reviewe"), which
    // is exactly the failure: content is unreachable and nothing says so. `auto` shows the bar
    // whenever the content actually overflows, and nothing when it does not.
    ScrollArea: ScrollArea.extend({ defaultProps: { type: "auto" } }),
  },
});
