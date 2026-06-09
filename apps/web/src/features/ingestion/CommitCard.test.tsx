import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportChecklist } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { CommitCard } from "./CommitCard";

// A not-ready checklist (the Task-1 fixture shape): one blocking conflict, commit_ready = 1.
const NOT_READY: ImportChecklist = {
  run_id: "10000000-0000-0000-0000-000000000001",
  status: "Reviewing",
  ready: false,
  blocking: [{ code: "duplicate_identifier_within_import" }],
  advisory: { star_coverage: { total: 20, satisfied: 17 }, unknown_low: 2, kind_unconfirmed: 4 },
  review: {
    keep_items: 4, decided: 0, accepted: 0, corrected: 0, excluded: 0, deferred: 0,
    undecided: 4, kind_confirmed: 1, commit_ready: 1,
  },
};
// A ready checklist: zero blocking, commit_ready = 3.
const READY: ImportChecklist = {
  ...NOT_READY,
  ready: true,
  blocking: [],
  review: { ...NOT_READY.review, commit_ready: 3 },
};

test("the button is disabled when the checklist is not ready", () => {
  renderWithProviders(
    <CommitCard checklist={NOT_READY} canCommit committing={false} onCommit={() => {}} />,
  );
  const btn = screen.getByRole("button", { name: /Commit 1 confirmed/ });
  expect(btn).toBeDisabled();
});

test("the button is enabled and clicking it calls onCommit when ready + commit_ready >= 1 + canCommit", async () => {
  const onCommit = vi.fn();
  const user = userEvent.setup();
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={onCommit} />,
  );
  const btn = screen.getByRole("button", { name: /Commit 3 confirmed/ });
  expect(btn).toBeEnabled();
  await user.click(btn);
  expect(onCommit).toHaveBeenCalledTimes(1);
});

test("the button is disabled while committing (and shows a loading state)", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 3 confirmed/ })).toBeDisabled();
});

test("the button is disabled when commit_ready is 0 (nothing to commit)", () => {
  const none: ImportChecklist = { ...READY, review: { ...READY.review, commit_ready: 0 } };
  renderWithProviders(
    <CommitCard checklist={none} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 0 confirmed/ })).toBeDisabled();
});

test("renders the held-by-another-role note (no enabled button) when !canCommit", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit={false} committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByText("Commit is held by another role (import.commit).")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Commit/ })).not.toBeInTheDocument();
});

test("the button label includes the commit_ready count", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 3 confirmed/ })).toBeInTheDocument();
});

test("renders the provenance definition list (baseline · signature · storage · provenance)", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByText("On commit")).toBeInTheDocument();
  expect(screen.getByText("Per-item, transactional, audited.")).toBeInTheDocument();
  expect(screen.getByText("Effective Rev A")).toBeInTheDocument();
  expect(screen.getByText("import_baseline")).toBeInTheDocument();
  expect(screen.getByText("WORM vault blob · content-addressed")).toBeInTheDocument();
  expect(screen.getByText("source path · sha256 · run · decided-by")).toBeInTheDocument();
  expect(screen.getByText(/3 ready/)).toBeInTheDocument();
});

test("has no axe violations (enabled, disabled, and held-by-another-role)", async () => {
  const enabled = renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(await axe(enabled.container)).toHaveNoViolations();
  enabled.unmount();

  const disabled = renderWithProviders(
    <CommitCard checklist={NOT_READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(await axe(disabled.container)).toHaveNoViolations();
  disabled.unmount();

  const held = renderWithProviders(
    <CommitCard checklist={READY} canCommit={false} committing={false} onCommit={() => {}} />,
  );
  expect(await axe(held.container)).toHaveNoViolations();
});
