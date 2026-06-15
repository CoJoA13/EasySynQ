import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { ConfirmDestructive } from "./ConfirmDestructive";

function renderConfirm(props: Partial<React.ComponentProps<typeof ConfirmDestructive>> = {}) {
  const onCancel = props.onCancel ?? vi.fn();
  const onConfirm = props.onConfirm ?? vi.fn().mockResolvedValue(undefined);
  render(
    <MantineProvider>
      <ConfirmDestructive
        opened
        onCancel={onCancel}
        onConfirm={onConfirm}
        title="Release document?"
        consequence="Releases the Approved version to Effective."
        confirmLabel="Release"
        {...props}
      />
    </MantineProvider>,
  );
  return { onCancel, onConfirm };
}

it("shows the consequence and the irreversible note by default", () => {
  renderConfirm();
  expect(screen.getByText("Releases the Approved version to Effective.")).toBeInTheDocument();
  expect(screen.getByText(/can.t be undone/i)).toBeInTheDocument();
});

it("omits the irreversible note when irreversible={false}", () => {
  renderConfirm({ irreversible: false });
  expect(screen.queryByText(/can.t be undone/i)).not.toBeInTheDocument();
});

it("Cancel calls onCancel and does not fire onConfirm", async () => {
  const user = userEvent.setup();
  const { onCancel, onConfirm } = renderConfirm();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onCancel).toHaveBeenCalledOnce();
  expect(onConfirm).not.toHaveBeenCalled();
});

it("the confirm button fires onConfirm", async () => {
  const user = userEvent.setup();
  const { onConfirm } = renderConfirm();
  await user.click(screen.getByRole("button", { name: "Release" }));
  expect(onConfirm).toHaveBeenCalledOnce();
});

it("surfaces a thrown server error and keeps the dialog open", async () => {
  const user = userEvent.setup();
  const onConfirm = vi
    .fn()
    .mockRejectedValue(new ApiError(409, "release_blocked", "Release is blocked."));
  renderConfirm({ onConfirm });
  await user.click(screen.getByRole("button", { name: "Release" }));
  expect(await screen.findByText("Release is blocked.")).toBeInTheDocument();
  // still open — the confirm button is still there
  expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument();
});

it("uses mapError to translate a known code", async () => {
  const user = userEvent.setup();
  const onConfirm = vi.fn().mockRejectedValue(new ApiError(409, "x", "raw"));
  renderConfirm({ onConfirm, mapError: () => "Friendly message." });
  await user.click(screen.getByRole("button", { name: "Release" }));
  expect(await screen.findByText("Friendly message.")).toBeInTheDocument();
});
