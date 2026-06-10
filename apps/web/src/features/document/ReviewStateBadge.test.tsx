import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { describe, expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { ReviewStateBadge } from "./ReviewStateBadge";

function renderBadge(state: "current" | "due_soon" | "overdue" | null) {
  return render(
    <MantineProvider theme={theme}>
      <ReviewStateBadge state={state} />
    </MantineProvider>,
  );
}

describe("ReviewStateBadge", () => {
  test("current renders Current", () => {
    renderBadge("current");
    expect(screen.getByText("Current")).toBeInTheDocument();
  });
  test("due_soon renders Due soon", () => {
    renderBadge("due_soon");
    expect(screen.getByText("Due soon")).toBeInTheDocument();
  });
  test("overdue renders Overdue", () => {
    renderBadge("overdue");
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });
  test("null (not scheduled) renders nothing", () => {
    renderBadge(null);
    expect(screen.queryByText(/current|due soon|overdue/i)).toBeNull();
  });
});
