import { useMgmtReviewNextDue } from "../management-review/hooks";
import type { Rag } from "./rag";
import { StatLine } from "./StatLine";

// CHECK-tile status line for the management-review cadence (clause 9.3 lives under clause 9). N9: the
// tone is status against a coded rule (the server-computed review_state), read at render — never an
// asserted verdict. A forbidden/errored/unset read renders NOTHING so it can never drag the tile red.
const STATE_TONE: Record<string, Rag> = { overdue: "red", due_soon: "amber", current: "green" };

/**
 * The line's content, as data. CheckCard folds the CHECK header from this SAME function, so the
 * header and the line cannot disagree — the card previously derived its own observation from
 * `review_state` alone, ignoring the not-configured and none-released branches below, and could
 * therefore state a review cadence the tile never displayed.
 */
export function nextReviewObservation(
  q: ReturnType<typeof useMgmtReviewNextDue>,
): { label: string; rag: Rag } | null {
  const { data, forbidden, isError } = q;
  if (forbidden || isError || !data) return null;
  if (!data.owner_configured) return { label: "Review cadence not configured", rag: "neutral" };
  if (!data.next_review_due || !data.review_state) {
    return { label: "No management review released yet", rag: "neutral" };
  }
  return {
    label:
      data.review_state === "overdue"
        ? `Management review overdue (was due ${data.next_review_due})`
        : `Next management review due ${data.next_review_due}`,
    rag: STATE_TONE[data.review_state] ?? "neutral",
  };
}

export function NextReviewLine() {
  const observation = nextReviewObservation(useMgmtReviewNextDue());
  if (!observation) return null;
  return <StatLine label={observation.label} tone={observation.rag} />;
}
