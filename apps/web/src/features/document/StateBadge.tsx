import { Badge, type MantineSize } from "@mantine/core";
import type { DocumentCurrentState } from "../../lib/types";

// Maps the 7 lifecycle states to a label + a leading glyph + a token-driven color. Status is NEVER
// color-only (DP-7): the text label always carries the meaning, and the glyph adds a second
// non-color channel. Colors reference the mockup's --es-* feedback hues.
const META: Record<DocumentCurrentState, { label: string; mark: string; color: string }> = {
  Draft: { label: "Draft", mark: "✎", color: "var(--es-text-muted)" },
  InReview: { label: "In review", mark: "◔", color: "var(--es-warning)" },
  Approved: { label: "Approved", mark: "✓", color: "var(--es-info)" },
  Effective: { label: "Effective", mark: "★", color: "var(--es-success)" },
  UnderRevision: { label: "Under revision", mark: "✎", color: "var(--es-warning)" },
  Superseded: { label: "Superseded", mark: "⊘", color: "var(--es-text-muted)" },
  Obsolete: { label: "Obsolete", mark: "⊘", color: "var(--es-text-muted)" },
};

export function StateBadge({
  state,
  size = "sm",
}: {
  state: DocumentCurrentState;
  size?: MantineSize;
}) {
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
