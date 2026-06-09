import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { BulkActionBar } from "./BulkActionBar";

function setup(count: number) {
  const onBulk = vi.fn();
  const onConfirmKind = vi.fn();
  const onAcceptAllHigh = vi.fn();
  const utils = renderWithProviders(
    <BulkActionBar
      count={count}
      onBulk={onBulk}
      onConfirmKind={onConfirmKind}
      onAcceptAllHigh={onAcceptAllHigh}
    />,
  );
  return { ...utils, onBulk, onConfirmKind, onAcceptAllHigh };
}

test("renders nothing when no rows are selected", () => {
  const { container } = setup(0);
  // MantineProvider injects <style> tags into the container in jsdom — check no section rendered
  expect(container.querySelector("section")).toBeNull();
});

test("shows the selected-item count when rows are selected", () => {
  const { getByText } = setup(3);
  expect(getByText(/3 items selected/)).toBeInTheDocument();
});

test("Confirm kind → Document confirms DOCUMENT for the selection (R10 act)", async () => {
  const u = userEvent.setup();
  const { getByRole, onConfirmKind } = setup(3);
  await u.click(getByRole("button", { name: /confirm kind/i }));
  await u.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirmKind).toHaveBeenCalledOnce();
  expect(onConfirmKind).toHaveBeenCalledWith("DOCUMENT");
});

test("Exclude posts a bulk exclude over the selection", async () => {
  const u = userEvent.setup();
  const { getByRole, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /exclude/i }));
  expect(onBulk).toHaveBeenCalledOnce();
  expect(onBulk).toHaveBeenCalledWith("exclude");
});

test("Correct to type → an item posts a correct decision with the chosen type", async () => {
  const u = userEvent.setup();
  const { getByRole, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /correct to type/i }));
  await u.click(await screen.findByRole("menuitem", { name: "SOP" }));
  expect(onBulk).toHaveBeenCalledOnce();
  expect(onBulk).toHaveBeenCalledWith("correct", { type_code: "SOP" });
});

test("Bulk accept all High triggers the selector-based accept (does NOT confirm kind)", async () => {
  const u = userEvent.setup();
  const { getByRole, onAcceptAllHigh, onConfirmKind, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /bulk accept all high/i }));
  expect(onAcceptAllHigh).toHaveBeenCalledOnce();
  expect(onConfirmKind).not.toHaveBeenCalled();
  expect(onBulk).not.toHaveBeenCalled();
});

test("reinforces R10 — setting kind counts as confirmation", () => {
  const { getByText } = setup(3);
  expect(getByText(/setting .*kind.* counts as your confirmation/i)).toBeInTheDocument();
});

test("has no a11y violations", async () => {
  const { container } = setup(3);
  expect(await axe(container)).toHaveNoViolations();
});
