import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type {
  ConfirmedKind,
  ImportClassification,
  ImportFileReview,
} from "../../lib/types";
import { KindCell } from "./KindCell";

const DOC_CLASS: ImportClassification = {
  kind: "DOCUMENT",
  kind_conf: 92,
  type_code: "SOP",
  type_conf: 90,
  clause_numbers: ["8.4"],
  clause_conf: 88,
  process_names: ["Purchasing"],
  process_conf: 80,
  pdca_phase: "DO",
  band: "HIGH",
  ambiguous: false,
  top2_margin: 30,
  classifier_version: "v1.4",
};

const RECORD_CLASS: ImportClassification = { ...DOC_CLASS, kind: "RECORD", type_code: "FRM" };
const UNKNOWN_CLASS: ImportClassification = { ...DOC_CLASS, kind: "UNKNOWN", type_code: null };

const UNCONFIRMED_REVIEW: ImportFileReview = {
  disposition: "undecided",
  kind: "UNCONFIRMED",
  identifier: "SOP-PUR-014",
  identifier_source: "preserved_doc_code",
  type_code: "SOP",
  clause_numbers: ["8.4"],
  process_names: ["Purchasing"],
  owner: null,
  decided: false,
  last_action: null,
  commit_ready: false,
  identifier_collidable: true,
};

const CONFIRMED_DOC_REVIEW: ImportFileReview = { ...UNCONFIRMED_REVIEW, kind: "DOCUMENT" };

function renderCell(props: {
  review: ImportFileReview | null;
  classification: ImportClassification | null;
  onConfirm: (kind: ConfirmedKind) => void;
  busy?: boolean;
}) {
  return render(
    <MantineProvider>
      <KindCell {...props} />
    </MantineProvider>,
  );
}

test("UNCONFIRMED renders the engine guess dimmed with a '?' + a Confirm affordance", () => {
  renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  expect(screen.getByText("Document?")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm kind" })).toBeInTheDocument();
});

test("UNCONFIRMED RECORD guess shows the lock glyph; UNKNOWN shows 'Unknown'", () => {
  const rec = renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: RECORD_CLASS,
    onConfirm: vi.fn(),
  });
  expect(rec.getByText("🔒 Record?")).toBeInTheDocument();
  rec.unmount();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: UNKNOWN_CLASS, onConfirm: vi.fn() });
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("choosing Document from the Confirm menu calls onConfirm('DOCUMENT')", async () => {
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: DOC_CLASS, onConfirm });
  await user.click(screen.getByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirm).toHaveBeenCalledWith("DOCUMENT");
});

test("choosing Record from the Confirm menu calls onConfirm('RECORD')", async () => {
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: DOC_CLASS, onConfirm });
  await user.click(screen.getByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Record" }));
  expect(onConfirm).toHaveBeenCalledWith("RECORD");
});

test("a confirmed kind renders a solid badge with no '?' and no Confirm button", () => {
  renderCell({
    review: CONFIRMED_DOC_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  expect(screen.getByLabelText("Kind: Document")).toBeInTheDocument();
  expect(screen.queryByText("Document?")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm kind" })).not.toBeInTheDocument();
});

test("busy disables the Confirm affordance", () => {
  renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
    busy: true,
  });
  expect(screen.getByRole("button", { name: "Confirm kind" })).toBeDisabled();
});

test("a null review/classification degrades to 'Unknown' (no crash)", () => {
  renderCell({ review: null, classification: null, onConfirm: vi.fn() });
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm kind" })).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});
