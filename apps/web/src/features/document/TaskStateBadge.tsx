import { Badge, type MantineSize } from "@mantine/core";
import type { TaskState } from "../../lib/types";

// Maps a task state to a label + a leading glyph + a token color. Status is NEVER color-only (DP-7):
// the text label carries the meaning, the glyph adds a second non-color channel. Mirrors StateBadge.
const META: Record<string, { label: string; mark: string; color: string }> = {
  PENDING: { label: "Pending", mark: "◔", color: "var(--es-warning)" },
  CLAIMED: { label: "Claimed", mark: "◑", color: "var(--es-info)" },
  DONE: { label: "Done", mark: "✓", color: "var(--es-success)" },
  SKIPPED: { label: "Skipped", mark: "⊘", color: "var(--es-text-muted)" },
  ESCALATED: { label: "Escalated", mark: "▲", color: "var(--es-danger)" },
  EXPIRED: { label: "Expired", mark: "⊗", color: "var(--es-text-muted)" },
};

export function TaskStateBadge({ state, size = "sm" }: { state: TaskState; size?: MantineSize }) {
  const meta = META[state] ?? { label: state, mark: "•", color: "var(--es-text-muted)" };
  return (
    <Badge
      variant="light"
      color={meta.color}
      size={size}
      leftSection={<span aria-hidden="true">{meta.mark}</span>}
      aria-label={`Task state: ${meta.label}`}
    >
      {meta.label}
    </Badge>
  );
}
