import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaDrawer } from "./CapaDrawer";

test("renders the title, the closed-loop thread and the close gate", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />);
  expect(await screen.findByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  expect(screen.getByText("Closed-loop thread")).toBeInTheDocument();
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
});

test("renders the Verify→RootCause loop honestly (cycle_marker>0)", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000005-0005-0005-0005-000000000005" onClose={vi.fn()} />);
  expect(await screen.findByText(/Cycle 2/)).toBeInTheDocument();
});

test("is closed (renders no dialog) when capaId is null", () => {
  const { container } = renderWithProviders(<CapaDrawer capaId={null} onClose={vi.fn()} />);
  expect(container.querySelector('[role="dialog"]')).toBeNull();
});

test("no axe violations when open", async () => {
  const { container } = renderWithProviders(
    <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
  );
  await screen.findByText(/Supplier re-evaluation overdue/);
  expect(await axe(container)).toHaveNoViolations();
});
