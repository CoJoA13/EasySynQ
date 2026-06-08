import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { TaskStateBadge } from "./TaskStateBadge";

test("TaskStateBadge labels the state with text + a non-color glyph (DP-7)", () => {
  const { getByLabelText } = renderWithProviders(<TaskStateBadge state="PENDING" />);
  const badge = getByLabelText("Task state: Pending");
  expect(badge).toBeInTheDocument();
  expect(badge).toHaveTextContent("Pending");
});

test("TaskStateBadge renders an unknown state verbatim (free-form vocab)", () => {
  // @ts-expect-error — exercising the open-string fallback for an out-of-enum value
  const { getByLabelText } = renderWithProviders(<TaskStateBadge state="MYSTERY" />);
  expect(getByLabelText("Task state: MYSTERY")).toBeInTheDocument();
});
