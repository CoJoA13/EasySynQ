import { render } from "@testing-library/react";
import { expect, it } from "vitest";
import {
  IconBell,
  IconDocument,
  IconRecord,
  IconSearch,
  IconShield,
  IconTasks,
  IconUser,
} from "./icons";

const ICONS = [IconSearch, IconTasks, IconBell, IconUser, IconDocument, IconRecord, IconShield];

it("every icon renders an aria-hidden, currentColor-stroked SVG", () => {
  for (const Icon of ICONS) {
    const { container, unmount } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // The host control carries the accessible name; the glyph itself must be hidden from AT.
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    unmount();
  }
});

it("respects an explicit size", () => {
  const { container } = render(<IconSearch size={28} />);
  const svg = container.querySelector("svg");
  expect(svg).toHaveAttribute("width", "28");
  expect(svg).toHaveAttribute("height", "28");
});
