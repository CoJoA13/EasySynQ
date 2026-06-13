import { Badge, type MantineSize } from "@mantine/core";
import type { DcrState } from "../../lib/types";

// Maps the 9-state DCR lifecycle to a label + a leading glyph + a token-driven color. Status is
// NEVER color-only (DP-7): the text label always carries the meaning, and the glyph adds a second
// non-color channel. Colors reference the mockup's --es-* feedback hues.
const META: Record<DcrState, { label: string; mark: string; color: string }> = {
  Open: { label: "Open", mark: "✎", color: "var(--es-info)" },
  Assessed: { label: "Assessed", mark: "◔", color: "var(--es-info)" },
  Routed: { label: "Routed", mark: "→", color: "var(--es-info)" },
  InApproval: { label: "In approval", mark: "◔", color: "var(--es-warning)" },
  Approved: { label: "Approved", mark: "✓", color: "var(--es-info)" },
  Implemented: { label: "Implemented", mark: "★", color: "var(--es-success)" },
  Closed: { label: "Closed", mark: "✓", color: "var(--es-success)" },
  Cancelled: { label: "Cancelled", mark: "⊘", color: "var(--es-text-muted)" },
  Rejected: { label: "Rejected", mark: "⊘", color: "var(--es-danger)" },
};

export function DcrStateBadge({ state, size = "sm" }: { state: DcrState; size?: MantineSize }) {
  const { label, mark, color } = META[state];
  return (
    <Badge
      variant="light"
      color={color}
      size={size}
      leftSection={<span aria-hidden="true">{mark}</span>}
      aria-label={`State: ${label}`}
    >
      {label}
    </Badge>
  );
}
