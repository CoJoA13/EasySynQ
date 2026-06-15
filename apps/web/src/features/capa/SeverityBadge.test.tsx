import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import type { NcSeverity } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { SeverityBadge } from "./SeverityBadge";

test("SeverityBadge renders a text label + accessible name (status is never colour-only)", () => {
  render(
    <MantineProvider theme={theme}>
      <SeverityBadge severity="Critical" />
    </MantineProvider>,
  );
  // The label + the aria-label carry the meaning — not colour alone (DP-7).
  expect(screen.getByText("Critical")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Critical")).toBeInTheDocument();
});

test("maps each severity to its canonical tone glyph (locks the intended semantics)", () => {
  // Critical = a hard fail (danger ✕); Major = needs-attention amber (warning ◔ — never red);
  // Minor = inert (neutral ○). The glyph is the non-colour channel; the label disambiguates.
  const cases: [NcSeverity, string][] = [
    ["Critical", TONE_GLYPH.danger],
    ["Major", TONE_GLYPH.warning],
    ["Minor", TONE_GLYPH.neutral],
  ];
  for (const [severity, glyph] of cases) {
    const { unmount } = render(
      <MantineProvider theme={theme}>
        <SeverityBadge severity={severity} />
      </MantineProvider>,
    );
    expect(screen.getByText(severity)).toBeInTheDocument();
    expect(screen.getByText(glyph)).toBeInTheDocument();
    unmount();
  }
});
