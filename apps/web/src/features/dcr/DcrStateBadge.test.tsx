import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrStateBadge } from "./DcrStateBadge";

it("renders the human label and a non-color aria-label", () => {
  const { getByLabelText, getByText } = renderWithProviders(<DcrStateBadge state="InApproval" />);
  expect(getByLabelText("State: In approval")).toBeInTheDocument();
  expect(getByText("In approval")).toBeInTheDocument();
});

it("renders a terminal state", () => {
  const { getByLabelText } = renderWithProviders(<DcrStateBadge state="Rejected" />);
  expect(getByLabelText("State: Rejected")).toBeInTheDocument();
});
