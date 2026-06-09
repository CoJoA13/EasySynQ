import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import type { ImportClassification, ImportFileReview } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ConfidenceCell } from "./ConfidenceCell";
import { IdentifierCell } from "./IdentifierCell";
import { TypeCell } from "./TypeCell";

function classification(over: Partial<ImportClassification> = {}): ImportClassification {
  return {
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
    ...over,
  };
}

function review(over: Partial<ImportFileReview> = {}): ImportFileReview {
  return {
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
    ...over,
  };
}

// ---- ConfidenceCell ----
test("ConfidenceCell renders the band label with the kind_conf percentage", () => {
  renderWithProviders(<ConfidenceCell classification={classification({ band: "HIGH", kind_conf: 92 })} />);
  const badge = screen.getByLabelText("Confidence: High 92%");
  expect(badge).toHaveTextContent("High · 92%");
});

test("ConfidenceCell labels MEDIUM and LOW bands", () => {
  const { unmount } = renderWithProviders(
    <ConfidenceCell classification={classification({ band: "MEDIUM", kind_conf: 73 })} />,
  );
  expect(screen.getByLabelText("Confidence: Medium 73%")).toHaveTextContent("Medium · 73%");
  unmount();
  renderWithProviders(<ConfidenceCell classification={classification({ band: "LOW", kind_conf: 22 })} />);
  expect(screen.getByLabelText("Confidence: Low 22%")).toHaveTextContent("Low · 22%");
});

test("ConfidenceCell adds an ambiguous caption when classification.ambiguous", () => {
  renderWithProviders(
    <ConfidenceCell classification={classification({ band: "LOW", kind_conf: 41, ambiguous: true })} />,
  );
  expect(screen.getByLabelText("Confidence: Ambiguous 41%")).toBeInTheDocument();
  expect(screen.getByText("⚖ ambiguous")).toBeInTheDocument();
});

test("ConfidenceCell renders a dash for a null classification", () => {
  renderWithProviders(<ConfidenceCell classification={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.queryByLabelText(/^Confidence:/)).not.toBeInTheDocument();
});

// ---- IdentifierCell ----
test("IdentifierCell shows a danger 'Duplicate of' line when dupeOf is set", () => {
  renderWithProviders(<IdentifierCell review={review()} dupeOf="SOP-PUR-014" />);
  expect(screen.getByText("Duplicate of SOP-PUR-014")).toBeInTheDocument();
});

test("IdentifierCell shows the mono identifier when present and no dupe", () => {
  renderWithProviders(<IdentifierCell review={review({ identifier: "WI-PRD-022" })} dupeOf={null} />);
  expect(screen.getByText("WI-PRD-022")).toBeInTheDocument();
});

test("IdentifierCell shows the record-no-code hint for a RECORD with no identifier", () => {
  renderWithProviders(
    <IdentifierCell review={review({ kind: "RECORD", identifier: null })} dupeOf={null} />,
  );
  expect(screen.getByText("— record (no code)")).toBeInTheDocument();
});

test("IdentifierCell shows 'suggest needed' for a non-record with no identifier", () => {
  renderWithProviders(
    <IdentifierCell review={review({ kind: "UNCONFIRMED", identifier: null })} dupeOf={null} />,
  );
  expect(screen.getByText("— suggest needed")).toBeInTheDocument();
});

test("IdentifierCell renders a dash for a null review", () => {
  renderWithProviders(<IdentifierCell review={null} dupeOf={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

// ---- TypeCell ----
test("TypeCell renders the type_code verbatim", () => {
  renderWithProviders(<TypeCell classification={classification({ type_code: "SOP" })} />);
  expect(screen.getByText("SOP")).toBeInTheDocument();
});

test("TypeCell adds an ambiguous caption when classification.ambiguous", () => {
  renderWithProviders(
    <TypeCell classification={classification({ type_code: "WI", ambiguous: true })} />,
  );
  expect(screen.getByText("WI")).toBeInTheDocument();
  expect(screen.getByText("ambiguous")).toBeInTheDocument();
});

test("TypeCell renders a dash for a null classification or a missing type_code", () => {
  const { unmount } = renderWithProviders(<TypeCell classification={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
  unmount();
  renderWithProviders(<TypeCell classification={classification({ type_code: null })} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

// ---- a11y: a small wrapper rendering all three cells together ----
test("all three cells together have no axe violations", async () => {
  const { container } = renderWithProviders(
    <table>
      <tbody>
        <tr>
          <td>
            <IdentifierCell review={review()} dupeOf={null} />
          </td>
          <td>
            <TypeCell classification={classification({ ambiguous: true })} />
          </td>
          <td>
            <ConfidenceCell classification={classification({ band: "LOW", kind_conf: 41, ambiguous: true })} />
          </td>
        </tr>
      </tbody>
    </table>,
  );
  expect(await axe(container)).toHaveNoViolations();
});
