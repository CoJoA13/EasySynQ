import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { RunTerminalSummary } from "./RunTerminalSummary";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("a Completed run shows the committed/failed counts and links to the Import Report record", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({
        status: "Completed",
        counts: { commit: { committed: 5, failed: 0 } },
        report_record_id: "r0000000-0000-0000-0000-0000000000r1",
      })}
    />,
  );
  expect(screen.getByText("Import complete")).toBeInTheDocument();
  expect(screen.getByLabelText("Committed: 5")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed: 0")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /Import Report/ });
  expect(link).toHaveAttribute("href", "/records/r0000000-0000-0000-0000-0000000000r1");
});

test("a Completed run with no report record shows a calm note instead of a link", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 1, failed: 0 } }, report_record_id: null })}
    />,
  );
  expect(screen.queryByRole("link", { name: /Import Report/ })).not.toBeInTheDocument();
  expect(screen.getByText(/report isn't available/)).toBeInTheDocument();
});

test("a PartiallyCommitted run shows a Resume commit button that calls onResume", async () => {
  const user = userEvent.setup();
  const onResume = vi.fn();
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "PartiallyCommitted", counts: { commit: { committed: 4, failed: 2 } } })}
      onResume={onResume}
    />,
  );
  expect(screen.getByText("Import partially committed")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Resume commit" }));
  expect(onResume).toHaveBeenCalled();
});

test("a Completed run does NOT show Resume", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 5, failed: 0 } } })}
      onResume={() => {}}
    />,
  );
  expect(screen.queryByRole("button", { name: "Resume commit" })).not.toBeInTheDocument();
});

test("has no axe violations (completed + partial)", async () => {
  const completed = renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 5, failed: 0 } }, report_record_id: "r1" })}
    />,
  );
  expect(await axe(completed.container)).toHaveNoViolations();
  completed.unmount();
  const partial = renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "PartiallyCommitted", counts: { commit: { committed: 4, failed: 2 } } })}
      onResume={() => {}}
    />,
  );
  expect(await axe(partial.container)).toHaveNoViolations();
});
