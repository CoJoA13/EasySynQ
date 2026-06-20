import type { RiskBand } from "../../lib/types";

// The v1 5×5-matrix band thresholds — a FE mirror of the backend `default_criteria(MATRIX_5X5)`
// (apps/api/.../domain/risk/rules.py): a band claims every rating ≥ its `min`, scanned worst→best.
// SAFE for v1 because there is exactly ONE golden-pinned `scoring_method` (`5x5_matrix`); the matrix is
// a REFERENCE heatmap of the band STRUCTURE, not a per-row verdict (a real row renders its own
// server-graded band/band_tone). matrix.test.ts pins these to the backend defaults (the FE golden).
// ⚠ v1.x residual: if a future scoring_method ever mints new thresholds, the matrix must read the
// governing criteria from the server instead of this mirror.
const BANDS: { band: RiskBand; min: number }[] = [
  { band: "critical", min: 20 },
  { band: "high", min: 12 },
  { band: "medium", min: 6 },
  { band: "low", min: 1 },
];

// The 5×5 axis values (likelihood, severity ∈ 1..5).
export const MATRIX_AXIS = [1, 2, 3, 4, 5] as const;

// risk_rating = likelihood × severity (the v1 rule, mirrored for the static reference grid).
export function cellRating(likelihood: number, severity: number): number {
  return likelihood * severity;
}

// The band for a (likelihood, severity) cell — total over 1..25 (a rating below the lowest min is
// unscored, unreachable for a 1..5 × 1..5 product but kept total).
export function bandForCell(likelihood: number, severity: number): RiskBand {
  const rating = cellRating(likelihood, severity);
  for (const entry of BANDS) {
    if (rating >= entry.min) return entry.band;
  }
  return "unscored";
}
