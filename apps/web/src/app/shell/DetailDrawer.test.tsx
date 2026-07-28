import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DetailDrawer } from "./DetailDrawer";

function renderDrawer() {
  return renderWithProviders(
    <DetailDrawer opened onClose={vi.fn()} title="Accessible detail">
      Drawer content
    </DetailDrawer>,
  );
}

test("the resize separator supports keyboard sizing and reports its current value", async () => {
  const user = userEvent.setup();
  renderDrawer();
  const separator = await screen.findByRole("separator", { name: "Resize panel" });

  expect(separator).toHaveAttribute("tabindex", "0");
  expect(separator).toHaveAttribute("aria-valuemin", "360");
  expect(separator).toHaveAttribute("aria-valuemax", "640");
  expect(separator).toHaveAttribute("aria-valuenow", "420");

  separator.focus();
  await user.keyboard("{ArrowLeft}");
  expect(separator).toHaveAttribute("aria-valuenow", "452");
  await user.keyboard("{ArrowRight}");
  expect(separator).toHaveAttribute("aria-valuenow", "420");
  await user.keyboard("{Home}");
  expect(separator).toHaveAttribute("aria-valuenow", "360");
  await user.keyboard("{End}");
  expect(separator).toHaveAttribute("aria-valuenow", "640");
});

test("single-pointer controls narrow and widen the panel without dragging", async () => {
  const user = userEvent.setup();
  renderDrawer();
  const separator = await screen.findByRole("separator", { name: "Resize panel" });

  await user.click(screen.getByRole("button", { name: "Widen panel" }));
  expect(separator).toHaveAttribute("aria-valuenow", "452");
  await user.click(screen.getByRole("button", { name: "Narrow panel" }));
  expect(separator).toHaveAttribute("aria-valuenow", "420");
});

test("the open drawer and resize controls have no axe violations", async () => {
  renderDrawer();
  await screen.findByRole("dialog");
  expect(await axe(document.body)).toHaveNoViolations();
});
