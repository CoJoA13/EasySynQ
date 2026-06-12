import type { ObjectiveAttainment, ObjectiveDirection, ObjectiveRag } from "../../lib/types";

export const RAG_COLOR: Record<ObjectiveRag, string> = {
  green: "green",
  amber: "yellow",
  red: "red",
  unmeasured: "gray",
};

export const RAG_LABEL: Record<ObjectiveRag, string> = {
  green: "Green",
  amber: "Amber",
  red: "Red",
  unmeasured: "Unmeasured",
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
    return { zones: validAmber ? ["red", "amber", "green"] : ["red", "green"], hasAmber: validAmber, warn };
  }
  const validAmber = threshold !== null && threshold > target;
  const warn =
    threshold !== null && threshold <= target
      ? "The at-risk threshold should be above the target for a “lower is better” objective."
      : null;
  return { zones: validAmber ? ["green", "amber", "red"] : ["green", "red"], hasAmber: validAmber, warn };
}
