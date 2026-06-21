import type { ContextCategory, ContextClassification, ContextIssue } from "../../lib/types";
import type { Tone } from "../../lib/status";

// The four SWOT quadrants in DISPLAY order — the board reads as a 2×2 of classification × helpful:
// Internal row first (Strengths, Weaknesses), External row second (Opportunities, Threats); Helpful
// column first (Strengths, Opportunities), Harmful second (Weaknesses, Threats). `classification` here
// is the CANONICAL SWOT definition (S/W are internal, O/T external); a row's OWN classification field
// is shown per-chip so any divergence stays visible (R50: the two axes are independent inputs). Pinned
// by swot.test.ts so the layout can't silently drift (the matrix golden-test precedent — there is no
// threshold table here: clause 4.1 has no graded axis, so the quadrant IS the category).
export interface SwotQuadrant {
  category: ContextCategory;
  label: string;
  classification: ContextClassification;
  helpful: boolean;
  tone: Tone;
}

export const SWOT_QUADRANTS: SwotQuadrant[] = [
  {
    category: "strength",
    label: "Strengths",
    classification: "internal",
    helpful: true,
    tone: "success",
  },
  {
    category: "weakness",
    label: "Weaknesses",
    classification: "internal",
    helpful: false,
    tone: "danger",
  },
  {
    category: "opportunity",
    label: "Opportunities",
    classification: "external",
    helpful: true,
    tone: "success",
  },
  {
    category: "threat",
    label: "Threats",
    classification: "external",
    helpful: false,
    tone: "danger",
  },
];

export type SwotBuckets = Record<ContextCategory | "uncategorized", ContextIssue[]>;

// Bucket the live working rows by SWOT category; NULL-category rows fall to `uncategorized` (the board's
// overflow strip). Pure — the board renders the result verbatim (categorical, no re-grade).
export function bucketByCategory(rows: ContextIssue[]): SwotBuckets {
  const buckets: SwotBuckets = {
    strength: [],
    weakness: [],
    opportunity: [],
    threat: [],
    uncategorized: [],
  };
  for (const row of rows) {
    if (row.category) buckets[row.category].push(row);
    else buckets.uncategorized.push(row);
  }
  return buckets;
}
