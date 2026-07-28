import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import type { CapaCloseState } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { CapaStateBadge } from "./CapaStateBadge";

test("maps every CAPA close state to its canonical human label and tone", () => {
  const cases: [CapaCloseState, string, string][] = [
    ["Raised", "Raised", TONE_GLYPH.info],
    ["Containment", "Containment", TONE_GLYPH.info],
    ["RootCause", "Root cause", TONE_GLYPH.info],
    ["ActionPlan", "Action plan", TONE_GLYPH.info],
    ["Implement", "Implementation", TONE_GLYPH.info],
    ["Verify", "Verification", TONE_GLYPH.info],
    ["Closed", "Closed", TONE_GLYPH.success],
    ["Rejected", "Rejected", TONE_GLYPH.danger],
  ];

  for (const [state, label, glyph] of cases) {
    const { unmount } = renderWithProviders(<CapaStateBadge state={state} />);
    const badge = screen.getByLabelText(`CAPA state: ${label}`);
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveTextContent(glyph);
    unmount();
  }
});
