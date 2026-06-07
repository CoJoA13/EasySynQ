import { Badge } from "@mantine/core";
import type { DocumentCurrentState } from "../../lib/types";

// Maps the 7 lifecycle states to a label + a token-driven color. Status is never color-only — the
// label text carries the meaning (DP-7 a11y). Colors reference the mockup's --es-* feedback hues.
const META: Record<DocumentCurrentState, { label: string; color: string }> = {
  Draft: { label: "Draft", color: "var(--es-text-muted)" },
  InReview: { label: "In review", color: "var(--es-warning)" },
  Approved: { label: "Approved", color: "var(--es-info)" },
  Effective: { label: "Effective", color: "var(--es-success)" },
  UnderRevision: { label: "Under revision", color: "var(--es-warning)" },
  Superseded: { label: "Superseded", color: "var(--es-text-muted)" },
  Obsolete: { label: "Obsolete", color: "var(--es-text-muted)" },
};

export function StateBadge({ state }: { state: DocumentCurrentState }) {
  const { label, color } = META[state];
  return (
    <Badge variant="light" color={color} aria-label={`State: ${label}`}>
      {label}
    </Badge>
  );
}
