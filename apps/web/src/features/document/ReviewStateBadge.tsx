import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { ReviewState } from "../../lib/types";

// S-web-8: the derived review-currency badge. review_state is SERVER-computed (org tz, 30-day
// due_soon window) — null means "no scheduled review" and renders nothing.
// Each state maps to a label + a canonical status tone; the tone supplies the AA-tuned colour pair AND
// the non-colour glyph via StatusBadge (status is NEVER colour-only, DP-7). overdue is `danger` (✕ —
// the previously-divergent ▲ glyph is retired this slice).
const META: Record<ReviewState, { label: string; tone: Tone }> = {
  current: { label: "Current", tone: "success" },
  due_soon: { label: "Due soon", tone: "warning" },
  overdue: { label: "Overdue", tone: "danger" },
};

export function ReviewStateBadge({ state }: { state: ReviewState | null }) {
  if (state === null) return null;
  const { label, tone } = META[state];
  return <StatusBadge tone={tone} label={label} kind="Review state" />;
}
