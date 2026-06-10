import { Badge } from "@mantine/core";
import type { ReviewState } from "../../lib/types";

const META: Record<ReviewState, { color: string; label: string }> = {
  current: { color: "green", label: "Current" },
  due_soon: { color: "yellow", label: "Due soon" },
  overdue: { color: "red", label: "Overdue" },
};

// S-web-8: the derived review-currency badge. review_state is SERVER-computed (org tz, 30-day
// due_soon window) — null means "no scheduled review" and renders nothing.
export function ReviewStateBadge({ state }: { state: ReviewState | null }) {
  if (state === null) return null;
  const m = META[state];
  return (
    <Badge color={m.color} variant="light">
      {m.label}
    </Badge>
  );
}
