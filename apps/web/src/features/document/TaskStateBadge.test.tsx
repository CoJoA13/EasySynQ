import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { renderWithProviders } from "../../test/render";
import { TaskStateBadge } from "./TaskStateBadge";

test("TaskStateBadge labels the state with text + a non-color glyph (DP-7)", () => {
  const { getByLabelText } = renderWithProviders(<TaskStateBadge state="PENDING" />);
  const badge = getByLabelText("Task state: Pending");
  expect(badge).toBeInTheDocument();
  expect(badge).toHaveTextContent("Pending");
  // The non-colour glyph is the second channel — PENDING maps to the warning tone (◔).
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
});

test("maps each task state to its canonical tone glyph (locks the intended semantics)", () => {
  const cases = [
    ["PENDING", "Pending", TONE_GLYPH.warning],
    ["CLAIMED", "Claimed", TONE_GLYPH.info],
    ["DONE", "Done", TONE_GLYPH.success],
    ["SKIPPED", "Skipped", TONE_GLYPH.neutral],
    ["ESCALATED", "Escalated", TONE_GLYPH.danger],
    ["EXPIRED", "Expired", TONE_GLYPH.neutral],
  ] as const;
  for (const [state, label, glyph] of cases) {
    const { unmount } = renderWithProviders(<TaskStateBadge state={state} />);
    // The label disambiguates same-glyph states (SKIPPED/EXPIRED both neutral ○).
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByLabelText(`Task state: ${label}`)).toBeInTheDocument();
    expect(screen.getByText(glyph)).toBeInTheDocument();
    unmount();
  }
});

test("TaskStateBadge renders an unknown state verbatim (free-form vocab)", () => {
  // @ts-expect-error — exercising the open-string fallback for an out-of-enum value
  const { getByLabelText } = renderWithProviders(<TaskStateBadge state="MYSTERY" />);
  expect(getByLabelText("Task state: MYSTERY")).toBeInTheDocument();
  // The fallback tone is neutral (○).
  expect(screen.getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
});
