import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { AuditStateBadge, FindingTypeBadge } from "./badges";

function r(ui: React.ReactElement) {
  return render(<MantineProvider theme={theme}>{ui}</MantineProvider>);
}

test("AuditStateBadge renders glyph + label per state (non-color)", () => {
  r(<AuditStateBadge state="InProgress" />);
  expect(screen.getByText(/● In progress/)).toBeInTheDocument();
});

test("AuditStateBadge renders the closed checkmark", () => {
  r(<AuditStateBadge state="Closed" />);
  expect(screen.getByText(/✓ Closed/)).toBeInTheDocument();
});

test("FindingTypeBadge renders severity + NC for an NC", () => {
  r(<FindingTypeBadge type="NC" severity="Major" />);
  expect(screen.getByText(/⚑ Major NC/)).toBeInTheDocument();
});

test("FindingTypeBadge renders Observation / OFI without severity", () => {
  r(<FindingTypeBadge type="OBSERVATION" severity={null} />);
  expect(screen.getByText(/◆ Observation/)).toBeInTheDocument();
  r(<FindingTypeBadge type="OFI" severity={null} />);
  expect(screen.getByText(/➚ OFI/)).toBeInTheDocument();
});
