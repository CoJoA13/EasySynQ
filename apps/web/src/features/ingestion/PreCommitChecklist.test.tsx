import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportChecklist } from "../../lib/types";
import { ingestionChecklistFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { PreCommitChecklist } from "./PreCommitChecklist";

const CHECKLIST = ingestionChecklistFixture as unknown as ImportChecklist;

test("renders the header + calm advisory subtitle", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  expect(screen.getByText("Pre-commit checklist")).toBeInTheDocument();
  expect(
    screen.getByText(
      "A calm gate before anything becomes controlled — advisory, never an auto-compliance judgment.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("Commit can proceed with gaps.")).toBeInTheDocument();
});

test("renders the duplicate-identifier blocking row with a Show items button that calls onShowBlocker", async () => {
  const user = userEvent.setup();
  const onShowBlocker = vi.fn();
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={onShowBlocker} />);
  const blockerRow = screen.getByLabelText("Blocking: Duplicate-identifier conflicts");
  expect(blockerRow).toBeInTheDocument();
  await user.click(within(blockerRow).getByRole("button", { name: "Show items" }));
  expect(onShowBlocker).toHaveBeenCalledTimes(1);
  expect(onShowBlocker).toHaveBeenCalledWith(CHECKLIST.blocking[0]);
});

test("the blocking row surfaces the offending identifier + file count inline", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const blockerRow = screen.getByLabelText("Blocking: Duplicate-identifier conflicts");
  // identifier "SOP-PUR-014" + "2 files" (file_ids has the two offenders) render next to the label.
  expect(within(blockerRow).getByText(/SOP-PUR-014/)).toBeInTheDocument();
  expect(within(blockerRow).getByText(/2 files/)).toBeInTheDocument();
});

test("renders the ★ mandatory ISO clause coverage as '17 / 20 satisfied' (advisory, not a blocker)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const coverageRow = screen.getByLabelText("Advisory: Mandatory ISO clause coverage");
  expect(within(coverageRow).getByText("17 / 20 satisfied")).toBeInTheDocument();
});

test("renders the kind-confirmed advisory row as '1 / 4' (warning, never danger)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const kindRow = screen.getByLabelText("Advisory: Kind confirmed on every item");
  expect(within(kindRow).getByText("1 / 4")).toBeInTheDocument();
  // it carries no Show-items affordance — advisory rows never read as blockers
  expect(within(kindRow).queryByRole("button", { name: "Show items" })).not.toBeInTheDocument();
});

test("a warn advisory row uses the canonical ◔ glyph (the retired ▲ is gone)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  // kind-confirmed (1/4) is a warn row → ◔
  const kindRow = screen.getByLabelText("Advisory: Kind confirmed on every item");
  expect(within(kindRow).getByText("◔")).toBeInTheDocument();
  expect(screen.queryByText("▲")).not.toBeInTheDocument();
});

test("renders the Unknown / Low triaged advisory row from unknown_low", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const triagedRow = screen.getByLabelText("Advisory: Unknown / Low triaged");
  expect(within(triagedRow).getByText("2")).toBeInTheDocument();
});

test("only blocking rows expose a Show items button (advisory rows do not)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  // exactly one blocker → exactly one Show items button across the whole card
  expect(screen.getAllByRole("button", { name: "Show items" })).toHaveLength(1);
});

test("degrades calmly when advisory.star_coverage is undefined", () => {
  const noCoverage: ImportChecklist = {
    ...CHECKLIST,
    advisory: { unknown_low: 0, kind_unconfirmed: 4 },
  };
  renderWithProviders(<PreCommitChecklist checklist={noCoverage} onShowBlocker={() => {}} />);
  const coverageRow = screen.getByLabelText("Advisory: Mandatory ISO clause coverage");
  expect(within(coverageRow).getByText("— / — satisfied")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
