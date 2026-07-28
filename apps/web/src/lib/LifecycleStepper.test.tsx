import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../test/render";
import { LifecycleStepper, type LifecycleStep } from "./LifecycleStepper";

const steps: LifecycleStep[] = [
  { key: "done", label: "Done", description: "Complete", status: "done" },
  { key: "current", label: "Current", description: "In progress", status: "current" },
  { key: "pending", label: "Pending", description: "Not started", status: "pending" },
  { key: "rejected", label: "Rejected", description: "Changes needed", status: "rejected" },
];

it("renders one accessible, token-driven treatment for every lifecycle status", async () => {
  const { container } = renderWithProviders(
    <LifecycleStepper ariaLabel="Example lifecycle" steps={steps} />,
  );

  expect(screen.getByLabelText("Example lifecycle").tagName).toBe("OL");
  expect(screen.getByText("Current").closest("li")).toHaveAttribute("aria-current", "step");
  expect(screen.getByText("Done").closest("li")).toHaveAttribute("data-lifecycle-status", "done");
  expect(screen.getByText("Pending").closest("li")).toHaveAttribute(
    "data-lifecycle-status",
    "pending",
  );
  expect(screen.getByText("Rejected").closest("li")).toHaveAttribute(
    "data-lifecycle-status",
    "rejected",
  );

  const done = container.querySelector('[data-lifecycle-marker="done"]');
  const current = container.querySelector('[data-lifecycle-marker="current"]');
  const pending = container.querySelector('[data-lifecycle-marker="pending"]');
  const rejected = container.querySelector('[data-lifecycle-marker="rejected"]');
  expect(done).toHaveStyle({
    background: "var(--es-success)",
    color: "var(--es-text-inverse)",
  });
  expect(current).toHaveStyle({
    background: "var(--es-info)",
    color: "var(--es-text-inverse)",
  });
  expect(pending).toHaveStyle({
    background: "var(--es-surface-2)",
    color: "var(--es-text-2)",
  });
  expect(rejected).toHaveStyle({
    background: "var(--es-danger)",
    color: "var(--es-text-inverse)",
  });
  expect(container.innerHTML).not.toContain("#fff");
  expect(await axe(container)).toHaveNoViolations();
});
