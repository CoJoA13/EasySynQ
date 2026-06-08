import { Badge, type MantineSize } from "@mantine/core";
import type { ImportRunStatus } from "../../lib/types";

// Maps an import-run status to a label + a leading glyph + a token color. Status is NEVER color-only
// (DP-7): the text label carries the meaning, the glyph adds a second non-color channel. Mirrors
// TaskStateBadge. The `status` prop is a plain string (not the enum) so the badge tolerates additive
// commit stages beyond ImportRunStatus — an unknown status degrades to a `?? fallback`, never crashes.
const META: Record<ImportRunStatus, { label: string; mark: string; color: string }> = {
  Created: { label: "Created", mark: "○", color: "var(--es-text-muted)" },
  Scanning: { label: "Scanning", mark: "◔", color: "var(--es-info)" },
  Scanned: { label: "Scanned", mark: "◑", color: "var(--es-info)" },
  Extracting: { label: "Extracting", mark: "◔", color: "var(--es-info)" },
  Classifying: { label: "Classifying", mark: "◑", color: "var(--es-info)" },
  Classified: { label: "Classified", mark: "◕", color: "var(--es-info)" },
  Deduping: { label: "Deduping", mark: "◑", color: "var(--es-info)" },
  Proposing: { label: "Proposing", mark: "◕", color: "var(--es-info)" },
  Proposed: { label: "Proposed", mark: "◆", color: "var(--es-warning)" },
  Reviewing: { label: "Reviewing", mark: "✎", color: "var(--es-warning)" },
  Committing: { label: "Committing", mark: "◔", color: "var(--es-info)" },
  Completed: { label: "Completed", mark: "★", color: "var(--es-success)" },
  PartiallyCommitted: { label: "Partially committed", mark: "◐", color: "var(--es-warning)" },
  Failed: { label: "Failed", mark: "▲", color: "var(--es-danger)" },
  Cancelled: { label: "Cancelled", mark: "⊘", color: "var(--es-text-muted)" },
};

export function ImportStatusBadge({ status, size = "sm" }: { status: string; size?: MantineSize }) {
  const meta = META[status as ImportRunStatus] ?? {
    label: status,
    mark: "•",
    color: "var(--es-text-muted)",
  };
  return (
    <Badge
      variant="light"
      color={meta.color}
      size={size}
      leftSection={<span aria-hidden="true">{meta.mark}</span>}
      aria-label={`Run status: ${meta.label}`}
    >
      {meta.label}
    </Badge>
  );
}
