import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { renderWithProviders } from "../../test/render";
import { ReviewStateBadge } from "./ReviewStateBadge";

describe("ReviewStateBadge", () => {
  test("current renders Current with its accessible name + tone glyph", () => {
    renderWithProviders(<ReviewStateBadge state="current" />);
    expect(screen.getByLabelText("Review state: Current")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText(TONE_GLYPH.success)).toBeInTheDocument();
  });
  test("due_soon renders Due soon with its accessible name + tone glyph", () => {
    renderWithProviders(<ReviewStateBadge state="due_soon" />);
    expect(screen.getByLabelText("Review state: Due soon")).toBeInTheDocument();
    expect(screen.getByText("Due soon")).toBeInTheDocument();
    expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  });
  test("overdue renders Overdue with its accessible name + tone glyph (▲ retired → ✕)", () => {
    renderWithProviders(<ReviewStateBadge state="overdue" />);
    expect(screen.getByLabelText("Review state: Overdue")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  });
  test("null (not scheduled) renders nothing", () => {
    renderWithProviders(<ReviewStateBadge state={null} />);
    expect(screen.queryByText(/current|due soon|overdue/i)).toBeNull();
  });
});
