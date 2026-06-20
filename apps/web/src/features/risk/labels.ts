import type { RiskBand, RiskType } from "../../lib/types";
import type { Tone } from "../../lib/status";

// The band MEANING (not the colour word) — a greyscale / colour-blind reader gets "High", not "red".
// unscored reads "Not yet measured" (the objectives "unmeasured" vocabulary; v1 never produces it but
// the map is total). The first four mirror the server RiskBand enum (domain/risk/rules.py).
export const RISK_BAND_LABEL: Record<RiskBand, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  unscored: "Not yet measured",
};

// Band → canonical status tone — mirrors the server BAND_TONE (domain/risk/rules.py): Critical + High
// = danger ✕, Medium = warning ◔, Low = success ✓, unscored = neutral ○. A row carries its own
// server `band_tone` (rendered verbatim, no FE re-grade); this map drives the FE-computed matrix cells
// (which have no server row to read a tone from) and agrees with the server values.
export const RISK_BAND_TONE: Record<RiskBand, Tone> = {
  critical: "danger",
  high: "danger",
  medium: "warning",
  low: "success",
  unscored: "neutral",
};

export const RISK_TYPE_LABEL: Record<RiskType, string> = {
  risk: "Risk",
  opportunity: "Opportunity",
};

// The four real bands worst→best (danger-first), for the scorecard chip order + the matrix legend.
// Mirrors the server BAND_RANK (critical 0 … low 3); unscored (rank 4) is omitted from the canonical
// display set (v1 never produces it) and surfaced only when a count is non-zero.
export const RISK_BAND_ORDER: RiskBand[] = ["critical", "high", "medium", "low"];
