import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { theme } from "../../theme/mantine";
import { AuditStateBadge, FindingTypeBadge } from "./badges";

function r(ui: React.ReactElement) {
  return render(<MantineProvider theme={theme}>{ui}</MantineProvider>);
}

test("AuditStateBadge renders label + non-colour glyph + accessible name (DP-7)", () => {
  r(<AuditStateBadge state="InProgress" />);
  // The label carries the meaning; the glyph is the non-colour channel (info → ●).
  expect(screen.getByText("In progress")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.info)).toBeInTheDocument();
  expect(screen.getByLabelText("Audit state: In progress")).toBeInTheDocument();
});

test("AuditStateBadge maps Closed to the success milestone glyph", () => {
  r(<AuditStateBadge state="Closed" />);
  expect(screen.getByText("Closed")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.success)).toBeInTheDocument();
  expect(screen.getByLabelText("Audit state: Closed")).toBeInTheDocument();
});

test("AuditStateBadge covers every audit state", () => {
  const states = [
    "Scheduled",
    "Planned",
    "InProgress",
    "FindingsDraft",
    "Reported",
    "Closing",
    "Closed",
  ] as const;
  for (const s of states) {
    const { unmount } = r(<AuditStateBadge state={s} />);
    unmount();
  }
});

test("FindingTypeBadge renders a Major NC with the warning glyph", () => {
  r(<FindingTypeBadge type="NC" severity="Major" />);
  expect(screen.getByText("Major NC")).toBeInTheDocument();
  // Major → warning (◔): faithful to the prior amber hue + consistent with CAPA's SeverityBadge.
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(screen.getByLabelText("Finding type: Major NC")).toBeInTheDocument();
});

test("FindingTypeBadge maps a Minor NC to the neutral glyph", () => {
  r(<FindingTypeBadge type="NC" severity="Minor" />);
  expect(screen.getByText("Minor NC")).toBeInTheDocument();
  // Minor → neutral (○): faithful to the prior gray hue + consistent with CAPA's SeverityBadge.
  expect(screen.getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
  expect(screen.getByLabelText("Finding type: Minor NC")).toBeInTheDocument();
});

test("FindingTypeBadge renders an NC with no recorded severity as danger", () => {
  r(<FindingTypeBadge type="NC" severity={null} />);
  expect(screen.getByText("NC")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  expect(screen.getByLabelText("Finding type: NC")).toBeInTheDocument();
});

test("FindingTypeBadge renders Observation (neutral) and OFI (info)", () => {
  r(<FindingTypeBadge type="OBSERVATION" severity={null} />);
  expect(screen.getByText("Observation")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
  expect(screen.getByLabelText("Finding type: Observation")).toBeInTheDocument();

  r(<FindingTypeBadge type="OFI" severity={null} />);
  expect(screen.getByText("OFI")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.info)).toBeInTheDocument();
  expect(screen.getByLabelText("Finding type: OFI")).toBeInTheDocument();
});
