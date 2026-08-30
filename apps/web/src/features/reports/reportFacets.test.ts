import { describe, expect, it } from "vitest";
import type { DocumentControlRegister, RegisterRow } from "../../lib/types";
import { buildProcessFacetOptions, deriveRegisterFacetSource } from "./reportFacets";

function row(id: string, clauses: string[], processes: string[]): RegisterRow {
  return {
    id,
    identifier: id,
    title: id,
    document_type_id: null,
    document_type: null,
    current_state: "Effective",
    owner_user_id: "owner",
    owner_display: null,
    effective_revision_label: null,
    effective_from: null,
    blob_sha256: null,
    clause_refs: clauses.map((clause) => ({ clause, starred: false })),
    process_links: processes,
    approved_by: null,
    approved_on: null,
    next_review_due: null,
    review_state: null,
  };
}

const REPORT: DocumentControlRegister = {
  provenance: {
    report_name: "Master Document List",
    generated_by: "reader",
    generated_at: "2026-07-28T12:00:00+00:00",
    as_of: "2026-07-28T12:00:00+00:00",
    scope: "org:DEFAULT",
    app_version: "0.1.0",
    filters: {},
    row_count: 2,
    content_hash: "sha256:test",
    process_scope: [
      { id: "process-2", name: "Scoped process" },
      // Scope alone must not create a choice when no visible row carries the process.
      { id: "process-hidden", name: "No visible row" },
    ],
    excluded_processes: null,
  },
  rows: [row("A", ["10", "8.4"], ["process-10", "process-2"]), row("B", ["8.4"], ["process-2"])],
};

describe("register report facet derivation", () => {
  it("deduplicates, naturally sorts, and rolls clause ANCESTORS into the options", () => {
    const source = deriveRegisterFacetSource(REPORT);
    // S-clause-rollup: 8.4 also contributes 8 — every ancestor is a representable subtree filter,
    // and a controlled Select can only display an accepted parent deep link it has as an option.
    expect(source.clauseValues).toEqual(["8", "8.4", "10"]);
    expect(source.processValues).toEqual(["process-2", "process-10"]);
    expect(source.processValues).not.toContain("process-hidden");
  });

  it("expands deep refs to every ancestor level, deduplicated across rows", () => {
    const deep: DocumentControlRegister = {
      ...REPORT,
      rows: [row("C", ["7.5.3"], []), row("D", ["7.5.2", "7.5.3"], [])],
    };
    expect(deriveRegisterFacetSource(deep).clauseValues).toEqual(["7", "7.5", "7.5.2", "7.5.3"]);
  });

  it("uses provenance and optional catalog names without letting either add choices", () => {
    const source = deriveRegisterFacetSource(REPORT);
    expect(
      buildProcessFacetOptions(source, [{ id: "process-10", name: "Catalog process" }]),
    ).toEqual([
      { value: "process-10", label: "Catalog process" },
      { value: "process-2", label: "Scoped process" },
    ]);
  });
});
