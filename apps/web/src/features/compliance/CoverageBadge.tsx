import { Badge } from "@mantine/core";
import type { CoverageStatus } from "../../lib/types";

// DP-7: status is never color-only — the label + glyph carry the meaning, color is the third
// redundant channel (mirrors StateBadge). Tokens from theme/tokens.css.
const META: Record<CoverageStatus, { label: string; mark: string; color: string }> = {
  COVERED: { label: "Covered", mark: "✓", color: "var(--es-success)" },
  PARTIAL: { label: "Partial", mark: "◔", color: "var(--es-warning)" },
  GAP: { label: "Gap", mark: "✕", color: "var(--es-danger)" },
};

export function CoverageBadge({ status }: { status: CoverageStatus }) {
  const { label, mark, color } = META[status];
  return (
    <Badge
      variant="light"
      color={color}
      leftSection={<span aria-hidden="true">{mark}</span>}
      aria-label={`Coverage: ${label}`}
    >
      {label}
    </Badge>
  );
}
