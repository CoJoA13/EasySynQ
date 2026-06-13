import { useMgmtReviewNextDue } from "../management-review/hooks";
import type { Rag } from "./rag";
import { StatLine } from "./StatLine";

// CHECK-tile status line for the management-review cadence (clause 9.3 lives under clause 9). N9: the
// tone is status against a coded rule (the server-computed review_state), read at render — never an
// asserted verdict. A forbidden/errored/unset read renders NOTHING so it can never drag the tile red.
const STATE_TONE: Record<string, Rag> = { overdue: "red", due_soon: "amber", current: "green" };

export function NextReviewLine() {
  const { data, forbidden, isError } = useMgmtReviewNextDue();
  if (forbidden || isError || !data) return null;
  if (!data.owner_configured) {
    return <StatLine label="Review cadence not configured" tone="neutral" />;
  }
  if (!data.next_review_due || !data.review_state) {
    return <StatLine label="No management review released yet" tone="neutral" />;
  }
  const tone = STATE_TONE[data.review_state] ?? "neutral";
  const label =
    data.review_state === "overdue"
      ? `Management review overdue (was due ${data.next_review_due})`
      : `Next management review due ${data.next_review_due}`;
  return <StatLine label={label} tone={tone} />;
}
