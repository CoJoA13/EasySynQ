import {
  DEFAULT_THEME,
  defaultVariantColorsResolver,
  type VariantColorsResolverInput,
} from "@mantine/core";
import { expect, test } from "vitest";
import { statusVariantColorResolver, theme } from "./mantine";

const input = (variant: string, color: string): VariantColorsResolverInput => ({
  theme: DEFAULT_THEME,
  variant,
  color,
});

test("theme reads the font tokens and the brand-teal primary", () => {
  expect(theme.fontFamily).toBe("var(--es-font-sans)");
  expect(theme.fontFamilyMonospace).toBe("var(--es-font-mono)");
  expect(theme.primaryColor).toBe("brand");
});

test("the brand ramp carries the mark hex at shade 6 and the light accent at shade 7", () => {
  // The palette must stay tied to public/easysynq-mark.svg — that reconciliation IS the slice.
  expect(theme.colors?.brand?.[6]).toBe("#0ea394"); // the mark's teal, verbatim
  expect(theme.colors?.brand?.[7]).toBe("#0a7a6f"); // darkened for text/fill on light
});

test("one primary shade serves both schemes, so the label can never be judged wrongly", () => {
  // Mantine's filled-variant resolver computes the label from parseThemeColor, which resolves a
  // shade-less theme colour with `colorScheme || "light"` and is never given the real scheme. A
  // per-scheme split therefore judges the DARK fill by the LIGHT shade. Pinning a single shade
  // removes the failure mode; this test exists so a future "use the brand hex in dark" edit has to
  // confront that, rather than silently shipping a 3.14:1 button label.
  expect(theme.primaryShade).toBe(7);
  expect(theme.colors?.brand?.[7]).toBe("#0a7a6f");
  expect(theme.colors?.brand?.[6]).toBe("#0ea394"); // the mark hex, kept in the ramp
});

test("autoContrast keeps EVERY filled label at AA across the whole palette", () => {
  // primaryShade is theme-GLOBAL: Mantine re-derives `-filled` for every entry of theme.colors.
  // Without autoContrast the forced white label put color="red" at 3.84:1, and Mantine's own
  // teal/orange/green/yellow were already failing at their default shade.
  expect(theme.autoContrast).toBe(true);
  expect(theme.luminanceThreshold).toBe(0.179);

  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (hex: string) => {
    const n = parseInt(hex.slice(1), 16);
    return (
      0.2126 * channel((n >> 16) & 0xff) +
      0.7152 * channel((n >> 8) & 0xff) +
      0.0722 * channel(n & 0xff)
    );
  };
  const ratio = (a: number, b: number) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);

  const shade = theme.primaryShade;
  expect(typeof shade, "a per-scheme pair reintroduces the light/dark mismatch above").toBe(
    "number",
  );
  const palettes = { ...DEFAULT_THEME.colors, brand: theme.colors!.brand! };

  for (const [name, tuple] of Object.entries(palettes)) {
    const fill = luminance(tuple[shade as number]!);
    // Mantine's rule: luminance > threshold -> black label, else white.
    const label = fill > theme.luminanceThreshold! ? 0 : 1;
    expect(
      ratio(fill, label),
      `${name}[${shade}] = ${tuple[shade as number]} with the auto-chosen ${label ? "white" : "black"} label`,
    ).toBeGreaterThanOrEqual(4.5);
  }
});

test("every Mantine scale is sourced from an --es-* token rather than a Mantine default", () => {
  // Before S-ui-1 the token ramps had zero readers and the app rendered as stock Mantine.
  for (const scale of [
    theme.fontSizes,
    theme.lineHeights,
    theme.radius,
    theme.spacing,
    theme.shadows,
  ]) {
    expect(Object.keys(scale ?? {})).toEqual(["xs", "sm", "md", "lg", "xl"]);
    for (const value of Object.values(scale ?? {})) {
      expect(value).toMatch(/^var\(--es-[a-z0-9-]+\)$/);
    }
  }
  for (const heading of Object.values(theme.headings?.sizes ?? {})) {
    expect(heading.fontSize).toMatch(/^var\(--es-fs-[a-z0-9]+\)$/);
    expect(heading.lineHeight).toMatch(/^var\(--es-lh-[a-z0-9]+\)$/);
    expect(heading.fontWeight).toMatch(/^var\(--es-fw-[a-z0-9]+\)$/);
  }
});

test("status variant resolves to the AA-tuned --es-*-soft / --es-*-text token pair", () => {
  const warn = statusVariantColorResolver(input("status", "warning"));
  expect(warn.background).toBe("var(--es-warning-soft)");
  expect(warn.color).toBe("var(--es-warning-text)");
});

test("neutral status is synthesized from the surface-2 / text-2 tokens", () => {
  const n = statusVariantColorResolver(input("status", "neutral"));
  expect(n.background).toBe("var(--es-surface-2)");
  expect(n.color).toBe("var(--es-text-2)");
});

test("emphasisSuccess status shares the success token pair (the ★ glyph is the distinction)", () => {
  const e = statusVariantColorResolver(input("status", "emphasisSuccess"));
  expect(e.background).toBe("var(--es-success-soft)");
  expect(e.color).toBe("var(--es-success-text)");
});

test("every non-status Badge variant falls through to Mantine's default resolver (no app-wide breakage)", () => {
  for (const variant of [
    "light",
    "filled",
    "outline",
    "dot",
    "gradient",
    "transparent",
    "white",
    "default",
  ]) {
    const i = input(variant, "indigo");
    expect(statusVariantColorResolver(i)).toEqual(defaultVariantColorsResolver(i));
  }
});
