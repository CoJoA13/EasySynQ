import { describe, expect, it } from "vitest";
import type { ContextIssue } from "../../lib/types";
import { bucketByCategory, SWOT_QUADRANTS } from "./swot";

function issue(over: Partial<ContextIssue> & Pick<ContextIssue, "id">): ContextIssue {
  return {
    register_doc_id: "head",
    classification: "internal",
    category: "strength",
    status: "active",
    description: "x",
    last_reviewed_at: null,
    row_version: 1,
    created_at: null,
    updated_at: null,
    ...over,
  };
}

// The golden layout pin (the matrix-golden-test analogue): the 2×2 board reads Internal row first
// (Strengths, Weaknesses), External row second (Opportunities, Threats); Helpful column first.
describe("SWOT_QUADRANTS golden layout", () => {
  it("lays out the four quadrants in the canonical SWOT order with the right axes", () => {
    expect(SWOT_QUADRANTS.map((q) => q.category)).toEqual([
      "strength",
      "weakness",
      "opportunity",
      "threat",
    ]);
    // Strengths/Weaknesses are Internal; Opportunities/Threats are External (the canonical SWOT spine).
    expect(SWOT_QUADRANTS.map((q) => q.classification)).toEqual([
      "internal",
      "internal",
      "external",
      "external",
    ]);
    // Helpful = Strengths + Opportunities; Harmful = Weaknesses + Threats.
    expect(SWOT_QUADRANTS.filter((q) => q.helpful).map((q) => q.category)).toEqual([
      "strength",
      "opportunity",
    ]);
    // Tone is colour-SAFE only because the label + glyph carry the meaning, but pin it: helpful reads
    // success, harmful reads danger (never colour alone — DP-5).
    expect(SWOT_QUADRANTS.every((q) => q.tone === (q.helpful ? "success" : "danger"))).toBe(true);
  });
});

describe("bucketByCategory", () => {
  it("buckets rows by SWOT category and drops NULL-category rows to uncategorized", () => {
    const rows = [
      issue({ id: "1", category: "strength" }),
      issue({ id: "2", category: "weakness" }),
      issue({ id: "3", category: "opportunity" }),
      issue({ id: "4", category: "threat" }),
      issue({ id: "5", category: null }),
      issue({ id: "6", category: "strength" }),
    ];
    const b = bucketByCategory(rows);
    expect(b.strength.map((r) => r.id)).toEqual(["1", "6"]);
    expect(b.weakness.map((r) => r.id)).toEqual(["2"]);
    expect(b.opportunity.map((r) => r.id)).toEqual(["3"]);
    expect(b.threat.map((r) => r.id)).toEqual(["4"]);
    expect(b.uncategorized.map((r) => r.id)).toEqual(["5"]);
  });

  it("returns empty buckets for an empty register", () => {
    const b = bucketByCategory([]);
    expect(b.strength).toEqual([]);
    expect(b.uncategorized).toEqual([]);
  });
});
