import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { StateBadge } from "./StateBadge";

test("StateBadge renders a text label (status is never color-only)", () => {
  render(
    <MantineProvider>
      <StateBadge state="Effective" />
    </MantineProvider>,
  );
  // The label text carries the meaning + an accessible name — not color alone (DP-7).
  expect(screen.getByText("Effective")).toBeInTheDocument();
  expect(screen.getByLabelText("State: Effective")).toBeInTheDocument();
});

test("StateBadge covers all 7 lifecycle states", () => {
  const states = [
    "Draft",
    "InReview",
    "Approved",
    "Effective",
    "UnderRevision",
    "Superseded",
    "Obsolete",
  ] as const;
  for (const s of states) {
    const { unmount } = render(
      <MantineProvider>
        <StateBadge state={s} />
      </MantineProvider>,
    );
    unmount();
  }
});

test("maps each lifecycle state to its canonical tone glyph (locks the intended semantics)", () => {
  const cases = [
    ["Draft", TONE_GLYPH.neutral],
    ["InReview", TONE_GLYPH.warning],
    ["Approved", TONE_GLYPH.info],
    ["Effective", TONE_GLYPH.emphasisSuccess],
    ["UnderRevision", TONE_GLYPH.warning],
    ["Superseded", TONE_GLYPH.neutral],
    ["Obsolete", TONE_GLYPH.neutral],
  ] as const;
  for (const [state, glyph] of cases) {
    const { unmount } = render(
      <MantineProvider>
        <StateBadge state={state} />
      </MantineProvider>,
    );
    // The glyph is the non-colour channel; the label still disambiguates same-glyph states.
    expect(screen.getByText(glyph)).toBeInTheDocument();
    unmount();
  }
});
