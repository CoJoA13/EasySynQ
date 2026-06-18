import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { ApiError } from "./api";
import {
  EmptyState,
  ErrorState,
  InlineState,
  LoadingState,
  MutationErrorState,
  NoAccessState,
  SkeletonList,
} from "./states";

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

test("InlineState renders compact text per kind, with the right tone", () => {
  const { rerender } = renderWithProviders(
    <InlineState kind="forbidden">You don't have access.</InlineState>,
  );
  expect(screen.getByText("You don't have access.")).toBeInTheDocument();

  rerender(<InlineState kind="error">Could not load.</InlineState>);
  expect(screen.getByText("Could not load.")).toBeInTheDocument();

  rerender(<InlineState kind="empty">Nothing here yet.</InlineState>);
  expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
});

test("InlineState loading is a live region (role=status) so AT announces it", () => {
  renderWithProviders(<InlineState kind="loading">Loading approvals…</InlineState>);
  expect(screen.getByRole("status")).toHaveTextContent("Loading approvals…");
});

test("InlineState error wires the optional inline retry to onRetry", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  renderWithProviders(
    <InlineState kind="error" onRetry={onRetry}>
      Could not load version history.
    </InlineState>,
  );
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});

test("InlineState error shows no retry button unless onRetry is given", () => {
  renderWithProviders(<InlineState kind="error">Could not load.</InlineState>);
  expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
});

test("SkeletonList renders N rows in a named live region", () => {
  renderWithProviders(<SkeletonList rows={5} height={32} label="Loading documents" />);
  const region = screen.getByRole("status", { name: "Loading documents" });
  expect(region.children).toHaveLength(5);
});

test("MutationErrorState unwraps an ApiError message under the given title", () => {
  renderWithProviders(
    <MutationErrorState
      title="Couldn't create the audit"
      error={new ApiError(409, "audit_conflict", "An audit for that scope already exists.")}
    />,
  );
  expect(screen.getByText("Couldn't create the audit")).toBeInTheDocument();
  expect(screen.getByText("An audit for that scope already exists.")).toBeInTheDocument();
});

test("MutationErrorState falls back when the error isn't an ApiError", () => {
  renderWithProviders(
    <MutationErrorState title="Couldn't save the plan" error={new Error("boom")} />,
  );
  expect(screen.getByText("Couldn't save the plan")).toBeInTheDocument();
  expect(screen.getByText("Please try again.")).toBeInTheDocument();
});

test("the primitives have no axe violations", async () => {
  const { container } = renderWithProviders(
    <>
      <LoadingState label="Loading" />
      <ErrorState title="Couldn't load" onRetry={() => {}} />
      <NoAccessState message="No access here." />
      <EmptyState message="Nothing yet." />
      <InlineState kind="loading">Loading…</InlineState>
      <InlineState kind="forbidden">No access.</InlineState>
      <InlineState kind="error" onRetry={() => {}}>
        Could not load.
      </InlineState>
      <SkeletonList rows={3} height={32} label="Loading rows" />
      <MutationErrorState
        title="Couldn't save"
        error={new ApiError(400, "bad", "Invalid input.")}
      />
    </>,
  );
  expect(await axe(container)).toHaveNoViolations();
});
