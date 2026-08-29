import { render } from "@testing-library/react";
import type { ComponentType } from "react";
import { expect, it } from "vitest";
import * as icons from "./icons";

// Enumerated from the MODULE, not a hand-kept list. The previous version named seven icons
// explicitly, so every icon added after it was written silently escaped the contract — S-ui-2 added
// eighteen. Reading the exports means a new icon is covered the moment it exists.
const ENTRIES = Object.entries(icons).filter(([name]) => name.startsWith("Icon")) as [
  string,
  ComponentType<{ size?: number }>,
][];

it("exports a reasonable set (guards against the glob silently matching nothing)", () => {
  expect(ENTRIES.length).toBeGreaterThanOrEqual(28);
});

it.each(ENTRIES)(
  "%s renders an aria-hidden, currentColor-stroked SVG on the 24px grid",
  (_n, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // The host control carries the accessible name; the glyph itself must be hidden from AT.
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    // One grid for the whole set — a stray viewBox makes an icon optically larger than its neighbours
    // at the same `size`, which is exactly what a hand-drawn set drifts into.
    expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
    expect(svg).toHaveAttribute("fill", "none");
    // A typo'd icon that renders an empty <svg> would pass every assertion above.
    expect(svg!.children.length).toBeGreaterThan(0);
  },
);

it.each(ENTRIES)("%s draws only inside the 24px box", (_n, Icon) => {
  // Cheap geometry guard: every numeric coordinate in the shape attributes must sit within the
  // viewBox (a small bleed is allowed for stroke width). Catches a fat-fingered `y="41"`, which
  // renders as a clipped or invisible glyph that no snapshot-free test would otherwise notice.
  const { container } = render(<Icon />);
  const svg = container.querySelector("svg")!;
  for (const el of Array.from(svg.querySelectorAll("*"))) {
    for (const attr of [
      "x",
      "y",
      "x1",
      "y1",
      "x2",
      "y2",
      "cx",
      "cy",
      "width",
      "height",
      "points",
      "d",
    ]) {
      const raw = el.getAttribute(attr);
      if (!raw) continue;
      for (const n of raw.match(/-?\d+(\.\d+)?/g) ?? []) {
        expect(Math.abs(Number(n)), `${_n}: ${attr}="${raw}"`).toBeLessThanOrEqual(24);
      }
    }
  }
});

it.each(ENTRIES)("%s draws no zero-length line", (_n, Icon) => {
  // A <line> whose endpoints coincide renders a dot ONLY while strokeLinecap is "round"; with the
  // default "butt" it draws nothing at all. Two icons used that trick for their exclamation dot,
  // which made a visible mark depend on an unrelated attribute. jsdom has no getBBox, so this is
  // the static form of the check that caught it in a real browser.
  const { container } = render(<Icon />);
  for (const line of Array.from(container.querySelectorAll("line"))) {
    const [x1, y1, x2, y2] = ["x1", "y1", "x2", "y2"].map((a) => Number(line.getAttribute(a)));
    expect(x1 === x2 && y1 === y2, `${_n}: zero-length line at ${x1},${y1}`).toBe(false);
  }
});

it("respects an explicit size", () => {
  const { container } = render(<icons.IconSearch size={28} />);
  const svg = container.querySelector("svg");
  expect(svg).toHaveAttribute("width", "28");
  expect(svg).toHaveAttribute("height", "28");
});
