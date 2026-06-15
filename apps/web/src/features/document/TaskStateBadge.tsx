import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { TaskState } from "../../lib/types";

// Each task state maps to a label + a canonical status tone. The tone supplies both the AA-tuned colour
// pair AND the non-colour glyph via StatusBadge (status is NEVER colour-only, DP-7). TaskState is an
// enum, but the component accepts free-form values — so the lookup keeps the open-string fallback below.
const META: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: "Pending", tone: "warning" },
  CLAIMED: { label: "Claimed", tone: "info" },
  DONE: { label: "Done", tone: "success" },
  SKIPPED: { label: "Skipped", tone: "neutral" },
  ESCALATED: { label: "Escalated", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "neutral" },
};

export function TaskStateBadge({ state, size = "sm" }: { state: TaskState; size?: MantineSize }) {
  const { label, tone } = META[state] ?? { label: state, tone: "neutral" };
  return <StatusBadge tone={tone} label={label} kind="Task state" size={size} />;
}
