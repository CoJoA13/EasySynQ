import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../lib/status";
import { renderWithProviders } from "../test/render";
import { UserStatusBadge, type UserStatus } from "./UserStatusBadge";

test("maps every user state to a human label and canonical status tone", () => {
  const cases: [UserStatus, string, string][] = [
    ["INVITED", "Invited", TONE_GLYPH.info],
    ["ACTIVE", "Active", TONE_GLYPH.success],
    ["LOCKED", "Locked", TONE_GLYPH.danger],
    ["DISABLED", "Disabled", TONE_GLYPH.warning],
    ["RETIRED", "Retired", TONE_GLYPH.neutral],
  ];

  for (const [status, label, glyph] of cases) {
    const { unmount } = renderWithProviders(<UserStatusBadge status={status} />);
    const badge = screen.getByLabelText(`User status: ${label}`);
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveTextContent(glyph);
    expect(badge).not.toHaveTextContent(status);
    unmount();
  }
});

test("degrades additive backend states to readable neutral copy", () => {
  const cases = [
    ["PENDING_REVIEW", "Pending review"],
    ["constructor", "Constructor"],
  ] as const;
  for (const [status, label] of cases) {
    const { unmount } = renderWithProviders(<UserStatusBadge status={status} />);
    const badge = screen.getByLabelText(`User status: ${label}`);
    expect(badge).toHaveTextContent(TONE_GLYPH.neutral);
    expect(badge).not.toHaveTextContent(status);
    unmount();
  }
});
