import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { QueueTabs } from "./QueueTabs";

const COUNTS = { needs: 4, medium: 1, high: 2, quarantine: 1, vault: 0 };

test("renders the five queue tabs with their labels + counts (mockup order)", () => {
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={() => {}} />,
  );
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.textContent)).toEqual([
    "Needs decision4",
    "Medium1",
    "High2",
    "Quarantine1",
    "Already in vault0",
  ]);
});

test("marks the active tab selected from `value`", () => {
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="high" onChange={() => {}} />,
  );
  expect(screen.getByRole("tab", { name: /High/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /Needs decision/ })).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

test("clicking a tab reports its queue value via onChange", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={onChange} />,
  );
  await user.click(screen.getByRole("tab", { name: /High/ }));
  expect(onChange).toHaveBeenCalledWith("high");
});

test("a missing count key renders 0 (noUncheckedIndexedAccess fallback)", () => {
  renderWithProviders(<QueueTabs counts={{}} value="needs" onChange={() => {}} />);
  // every tab badge degrades to 0 when its count key is absent
  expect(screen.getByRole("tab", { name: /Needs decision/ })).toHaveTextContent("0");
  expect(screen.getByRole("tab", { name: /Quarantine/ })).toHaveTextContent("0");
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
