import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportFile } from "../../lib/types";
import { ingestionFilesFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { TriageTable } from "./TriageTable";

// The Task-1 fixture order is [HIGH_DOC, DUP_FILE, MED_DOC, LOW_UNKNOWN, QUARANTINE_FILE].
const FILES = ingestionFilesFixture as unknown as ImportFile[];
const HIGH = FILES[0]!;
const DUP = FILES[1]!;

// A clone of HIGH with the named review fields overridden — to exercise the per-row render guards
// (null process_names, a corrected effective type) and the non-candidate gating, without mutating the
// shared fixture.
function highWith(reviewOver: Partial<NonNullable<ImportFile["review"]>>): ImportFile {
  return { ...HIGH, review: { ...HIGH.review!, ...reviewOver } };
}

// DUP (a4) is the redundant member of the near-cluster whose canonical id is SOP-PUR-014; HIGH (a1)
// is the canonical effective member of a 2-member family. The maps are the ReviewCockpit join output.
const DUPE_MAP = new Map<string, string>([[DUP.id, "SOP-PUR-014"]]);
const FAMILY_MAP = new Map<string, number>([[HIGH.id, 2]]);

function baseProps(over: Partial<Parameters<typeof TriageTable>[0]> = {}) {
  return {
    files: FILES,
    dupeMap: DUPE_MAP,
    familyMap: FAMILY_MAP,
    loading: false,
    selected: new Set<string>(),
    onToggle: vi.fn(),
    onToggleAllOnPage: vi.fn(),
    allOnPageSelected: false,
    onConfirmKind: vi.fn(),
    onOpenDetail: vi.fn(),
    onRowAction: vi.fn(),
    ...over,
  };
}

test("renders the high-confidence row with its identifier and a 'High' confidence", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText("SOP-PUR-014 Purchasing.docx")).toBeInTheDocument();
  // ConfidenceCell carries aria-label="Confidence: High · 92%" (title-cased band · kind_conf%,
  // unified visible/accessible label after the S-statusbadge-2 StatusBadge migration).
  expect(screen.getByLabelText("Confidence: High · 92%")).toBeInTheDocument();
});

test("shows the family member-count meta line for a file in familyMap", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText(/2 versions in family/)).toBeInTheDocument();
});

test("the dup row shows 'Duplicate of SOP-PUR-014' (from dupeMap)", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  // IdentifierCell (Task 5) renders the dupeOf danger text when dupeMap has the file.
  expect(screen.getByText(/Duplicate of SOP-PUR-014/)).toBeInTheDocument();
});

test("the quarantine row shows the reason and offers no Accept action", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText("broken.bin")).toBeInTheDocument();
  expect(screen.getByText(/Quarantined: sniff_failed/)).toBeInTheDocument();
  // the quarantine row carries no per-row Accept button
  const cell = screen.getByText("broken.bin").closest("tr")!;
  expect(within(cell).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
});

test("the quarantine row's selection checkbox is disabled (not a commit candidate)", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  // a quarantined file is not selectable — its checkbox is disabled so select-all can't sweep it in.
  expect(screen.getByRole("checkbox", { name: "Select broken.bin" })).toBeDisabled();
});

test("a non-candidate (non-quarantine) row's checkbox is also disabled", () => {
  // included_candidate === false covers more than quarantine (other scan-excluded dispositions). Such
  // a row classifies/renders normally but must NOT be selectable (the backend 422s a decision on it).
  const nonCandidate: ImportFile = {
    ...HIGH,
    id: "f0000000-0000-0000-0000-0000000000b1",
    filename: "excluded-by-scan.docx",
    rel_path: "excluded-by-scan.docx",
    scan_flags: { disposition: "excluded" },
    included_candidate: false,
  };
  renderWithProviders(<TriageTable {...baseProps({ files: [nonCandidate] })} />);
  expect(screen.getByRole("checkbox", { name: "Select excluded-by-scan.docx" })).toBeDisabled();
});

test("a row whose review.process_names is null renders '—' (no crash)", () => {
  // The backend folds process_names to null (no process signal); the cell must guard before .length.
  renderWithProviders(
    <TriageTable {...baseProps({ files: [highWith({ process_names: null })] })} />,
  );
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  // The process cell degrades to a dash rather than throwing on null.length.
  expect(within(row).getByText("—")).toBeInTheDocument();
});

test("the type column shows the corrected effective type, overriding the classifier proposal", () => {
  // HIGH's classifier proposes SOP; a "Correct to type" decision folds WI onto review.type_code.
  renderWithProviders(<TriageTable {...baseProps({ files: [highWith({ type_code: "WI" })] })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  // The type cell shows the corrected WI, not the classifier's SOP.
  expect(within(row).getByText("WI")).toBeInTheDocument();
  expect(within(row).queryByText("SOP")).not.toBeInTheDocument();
});

test("toggling a row checkbox calls onToggle(file.id)", async () => {
  const user = userEvent.setup();
  const onToggle = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onToggle })} />);
  await user.click(screen.getByRole("checkbox", { name: "Select SOP-PUR-014 Purchasing.docx" }));
  expect(onToggle).toHaveBeenCalledWith(HIGH.id);
});

test("the header 'Select all on page' checkbox calls onToggleAllOnPage", async () => {
  const user = userEvent.setup();
  const onToggleAllOnPage = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onToggleAllOnPage })} />);
  await user.click(screen.getByRole("checkbox", { name: "Select all on page" }));
  expect(onToggleAllOnPage).toHaveBeenCalledTimes(1);
});

test("a row checkbox reflects `selected.has(id)`", () => {
  renderWithProviders(<TriageTable {...baseProps({ selected: new Set([HIGH.id]) })} />);
  expect(
    screen.getByRole("checkbox", { name: "Select SOP-PUR-014 Purchasing.docx" }),
  ).toBeChecked();
});

test("clicking a row's Accept calls onRowAction(file, 'accept')", async () => {
  const user = userEvent.setup();
  const onRowAction = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onRowAction })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Accept" }));
  expect(onRowAction).toHaveBeenCalledWith(HIGH, "accept");
});

test("clicking a row's Open calls onOpenDetail(file.id)", async () => {
  const user = userEvent.setup();
  const onOpenDetail = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onOpenDetail })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Open" }));
  expect(onOpenDetail).toHaveBeenCalledWith(HIGH.id);
});

test("confirming kind on a row calls onConfirmKind(file.id, 'DOCUMENT')", async () => {
  const user = userEvent.setup();
  const onConfirmKind = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onConfirmKind })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  // KindCell (Task 5) renders Confirm as a Menu trigger: open it, then choose Document. TriageTable
  // wraps the cell's onConfirm(kind) with file.id, so it arrives as onConfirmKind(file.id, "DOCUMENT").
  await user.click(within(row).getByRole("button", { name: /Confirm/ }));
  await user.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirmKind).toHaveBeenCalledWith(HIGH.id, "DOCUMENT");
});

test("loading renders skeleton rows, not the empty state", () => {
  renderWithProviders(<TriageTable {...baseProps({ files: [], loading: true })} />);
  expect(screen.getByLabelText("Loading files")).toBeInTheDocument();
  expect(screen.queryByText("Nothing in this queue.")).not.toBeInTheDocument();
});

test("an empty file list shows the calm empty state", () => {
  renderWithProviders(<TriageTable {...baseProps({ files: [], loading: false })} />);
  expect(screen.getByText("Nothing in this queue.")).toBeInTheDocument();
});

test("no axe violations (populated table)", async () => {
  const { container } = renderWithProviders(<TriageTable {...baseProps()} />);
  expect(await axe(container)).toHaveNoViolations();
});
