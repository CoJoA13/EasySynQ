import { expect, it } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { renderWithProviders } from "../../test/render";
import { DcrStateBadge } from "./DcrStateBadge";

it("renders the human label and a non-color aria-label", () => {
  const { getByLabelText, getByText } = renderWithProviders(<DcrStateBadge state="InApproval" />);
  expect(getByLabelText("State: In approval")).toBeInTheDocument();
  expect(getByText("In approval")).toBeInTheDocument();
  // The glyph is the second (non-colour) channel — InApproval is warning (◔), DP-7.
  expect(getByText(TONE_GLYPH.warning)).toBeInTheDocument();
});

it("renders a terminal state", () => {
  const { getByLabelText, getByText } = renderWithProviders(<DcrStateBadge state="Rejected" />);
  expect(getByLabelText("State: Rejected")).toBeInTheDocument();
  // Rejected is a hard integrity-style failure → danger (✕).
  expect(getByText(TONE_GLYPH.danger)).toBeInTheDocument();
});

it("maps each of the 9 DCR states to its canonical tone glyph (locks the change-control semantics)", () => {
  const cases = [
    ["Open", "Open", TONE_GLYPH.info],
    ["Assessed", "Assessed", TONE_GLYPH.info],
    ["Routed", "Routed", TONE_GLYPH.info],
    ["InApproval", "In approval", TONE_GLYPH.warning],
    ["Approved", "Approved", TONE_GLYPH.info],
    ["Implemented", "Implemented", TONE_GLYPH.emphasisSuccess],
    ["Closed", "Closed", TONE_GLYPH.success],
    ["Cancelled", "Cancelled", TONE_GLYPH.neutral],
    ["Rejected", "Rejected", TONE_GLYPH.danger],
  ] as const;
  for (const [state, label, glyph] of cases) {
    const { unmount, getByLabelText, getByText } = renderWithProviders(
      <DcrStateBadge state={state} />,
    );
    // Label disambiguates same-glyph states (Open/Assessed/Routed/Approved all map to info).
    expect(getByLabelText(`State: ${label}`)).toBeInTheDocument();
    expect(getByText(glyph)).toBeInTheDocument();
    unmount();
  }
});
