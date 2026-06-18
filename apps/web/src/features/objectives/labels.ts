import type { ObjectiveAttainment, ObjectiveDirection, ObjectiveRag } from "../../lib/types";
import { TONE_GLYPH, type Tone } from "../../lib/status";

// Raw Mantine colour names — still used by the NON-badge Progress bar in CommitmentHero (a plain
// progress fill, not a status pill). RAG badges route through StatusBadge via RAG_TONE below.
export const RAG_COLOR: Record<ObjectiveRag, string> = {
  green: "green",
  amber: "yellow",
  red: "red",
  unmeasured: "gray",
};

// RAG → canonical status tone (the feature-local map; only Tone + glyphs are shared — S-statusbadge-2).
// green→success ✓ (on-target), amber→warning ◔ (at-risk), red→danger ✕ (off-target), unmeasured→neutral ○ (no data).
export const RAG_TONE: Record<ObjectiveRag, Tone> = {
  green: "success",
  amber: "warning",
  red: "danger",
  unmeasured: "neutral",
};

// RAG → the canonical non-colour glyph (DP-5: status is never colour-alone). Derived from the ONE
// glyph source (TONE_GLYPH) via RAG_TONE — so the trend-chart markers + band zones carry the SAME
// vocabulary the StatusBadge pills do (✓/◔/✕/○), never a second drifting set (S-obj-rag-legibility).
export const RAG_GLYPH: Record<ObjectiveRag, string> = {
  green: TONE_GLYPH[RAG_TONE.green],
  amber: TONE_GLYPH[RAG_TONE.amber],
  red: TONE_GLYPH[RAG_TONE.red],
  unmeasured: TONE_GLYPH[RAG_TONE.unmeasured],
};

// RAG → the MEANING (not the colour word) — a greyscale / colour-blind reader gets "On track", not
// "Green" (DP-5; closes the #144 deferral). The first three match the Home dashboard's RAG_META.label
// verbatim (one cross-surface vocabulary); "unmeasured" reads "Not yet measured" (objectives-precise:
// no KPI reading recorded yet).
export const RAG_LABEL: Record<ObjectiveRag, string> = {
  green: "On track",
  amber: "Needs attention",
  red: "Action required",
  unmeasured: "Not yet measured",
};

export const ATTAINMENT_LABEL: Record<ObjectiveAttainment, string> = {
  in_progress: "In progress",
  met: "Met",
  missed: "Missed",
};

export const DIRECTION_LABEL: Record<ObjectiveDirection, string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// Decimal-string value + unit, or an em dash when unmeasured.
export function fmtValueUnit(value: string | null, unit: string): string {
  if (value === null) return "—";
  return `${value} ${unit}`.trim();
}

export type RagZone = "red" | "amber" | "green";

export interface BandModel {
  zones: RagZone[]; // left→right display order
  hasAmber: boolean; // a valid amber band exists
  warn: string | null; // a soft, non-blocking warning when the threshold is on the wrong side
}

// Pure: derive the green/amber/red display zones + a soft warning from the target, the optional
// at-risk threshold, and the direction. The amber band only exists when the threshold sits on the
// correct side of the target (below, for higher-is-better; above, for lower-is-better). A backwards
// threshold collapses to red on the server, so we warn (never block) the author.
export function bandZones(args: {
  target: number;
  threshold: number | null;
  direction: ObjectiveDirection;
}): BandModel {
  const { target, threshold, direction } = args;
  if (direction === "HIGHER_IS_BETTER") {
    const validAmber = threshold !== null && threshold < target;
    const warn =
      threshold !== null && threshold >= target
        ? "The at-risk threshold should be below the target for a “higher is better” objective."
        : null;
    return {
      zones: validAmber ? ["red", "amber", "green"] : ["red", "green"],
      hasAmber: validAmber,
      warn,
    };
  }
  const validAmber = threshold !== null && threshold > target;
  const warn =
    threshold !== null && threshold <= target
      ? "The at-risk threshold should be above the target for a “lower is better” objective."
      : null;
  return {
    zones: validAmber ? ["green", "amber", "red"] : ["green", "red"],
    hasAmber: validAmber,
    warn,
  };
}
