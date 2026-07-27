import { Drawer, Modal } from "@mantine/core";
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/render";

it("gives every Modal close button an accessible name", async () => {
  renderWithProviders(
    <Modal opened onClose={vi.fn()} title="Example dialog">
      Dialog content
    </Modal>,
  );

  expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  // Modal content is portalled outside Testing Library's render container.
  expect(await axe(document.body)).toHaveNoViolations();
});

it("gives every Drawer close button an accessible name", async () => {
  renderWithProviders(
    <Drawer opened onClose={vi.fn()} title="Example drawer">
      Drawer content
    </Drawer>,
  );

  expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  // Drawer content is portalled outside Testing Library's render container.
  expect(await axe(document.body)).toHaveNoViolations();
});
