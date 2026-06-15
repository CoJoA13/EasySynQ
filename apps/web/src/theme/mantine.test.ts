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

test("theme uses the mockup's system font tokens + indigo primary", () => {
  expect(theme.fontFamily).toBe("var(--es-font-sans)");
  expect(theme.fontFamilyMonospace).toBe("var(--es-font-mono)");
  expect(theme.primaryColor).toBe("indigo");
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
