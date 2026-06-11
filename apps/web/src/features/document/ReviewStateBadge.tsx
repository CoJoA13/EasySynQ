import { Badge } from "@mantine/core";
import type { ReviewState } from "../../lib/types";

// S-web-8: the derived review-currency badge. review_state is SERVER-computed (org tz, 30-day
// due_soon window) — null means "no scheduled review" and renders nothing.
// DP-7: status is never color-only — label + glyph carry the meaning, color is the third
// redundant channel (mirrors CoverageBadge). Tokens from theme/tokens.css.
const META: Record<ReviewState, { label: string; mark: string; color: string }> = {
  current: { label: "Current", mark: "✓", color: "var(--es-success)" },
  due_soon: { label: "Due soon", mark: "◔", color: "var(--es-warning)" },
  overdue: { label: "Overdue", mark: "▲", color: "var(--es-danger)" },
};

export function ReviewStateBadge({ state }: { state: ReviewState | null }) {
  if (state === null) return null;
  const { label, mark, color } = META[state];
  return (
    <Badge
      variant="light"
      color={color}
      leftSection={<span aria-hidden="true">{mark}</span>}
      aria-label={`Review state: ${label}`}
    >
      {label}
    </Badge>
  );
}
