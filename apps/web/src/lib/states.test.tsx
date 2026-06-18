import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "./states";

test("LoadingState exposes an accessible name (the bare <Loader/> announced nothing)", () => {
  renderWithProviders(<LoadingState label="Loading change requests" />);
  expect(screen.getByLabelText("Loading change requests")).toBeInTheDocument();
});

test("LoadingState defaults its accessible name to 'Loading'", () => {
  renderWithProviders(<LoadingState />);
  expect(screen.getByLabelText("Loading")).toBeInTheDocument();
});

test("ErrorState renders the title + default copy, with no retry button unless onRetry is given", () => {
  renderWithProviders(<ErrorState title="Couldn't load change requests" />);
  expect(screen.getByText("Couldn't load change requests")).toBeInTheDocument();
  expect(screen.getByText("Please try again.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
});

test("ErrorState wires the retry button to onRetry", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  renderWithProviders(<ErrorState title="Couldn't load" onRetry={onRetry} />);
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});

test("NoAccessState renders the verbatim 'No access' title + the gating message", () => {
  renderWithProviders(<NoAccessState message="It requires the change-request read permission." />);
  expect(screen.getByText("No access")).toBeInTheDocument();
  expect(screen.getByText("It requires the change-request read permission.")).toBeInTheDocument();
});

test("EmptyState renders a dimmed message, and an optional action when provided", () => {
  const { rerender } = renderWithProviders(<EmptyState message="No change requests yet." />);
  expect(screen.getByText("No change requests yet.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  rerender(
    <EmptyState
      message="No change requests yet."
      action={<button type="button">Raise one</button>}
    />,
  );
  expect(screen.getByRole("button", { name: "Raise one" })).toBeInTheDocument();
});

test("the primitives have no axe violations", async () => {
  const { container } = renderWithProviders(
    <>
      <LoadingState label="Loading" />
      <ErrorState title="Couldn't load" onRetry={() => {}} />
      <NoAccessState message="No access here." />
      <EmptyState message="Nothing yet." />
    </>,
  );
  expect(await axe(container)).toHaveNoViolations();
});
