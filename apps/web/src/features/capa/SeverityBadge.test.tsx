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

test("an optional count makes an AGGREGATE pill with a distinct accessible name", () => {
  // The summary histogram on /capa needs "Critical · 3" without minting a second severity pill.
  // The name must differ from the per-item pill's, because both render on the same board and a
  // duplicate accessible name breaks getByLabelText's single-match contract.
  render(
    <MantineProvider theme={theme}>
      <SeverityBadge severity="Major" count={3} />
    </MantineProvider>,
  );
  expect(screen.getByText("Major · 3")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Major · 3")).toBeInTheDocument();
  expect(screen.queryByLabelText("Severity: Major")).toBeNull();
});
