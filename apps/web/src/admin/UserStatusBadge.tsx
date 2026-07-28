import { StatusBadge } from "../lib/StatusBadge";
import { humanizeToken } from "../lib/labels";
import type { Tone } from "../lib/status";

export type UserStatus = "INVITED" | "ACTIVE" | "LOCKED" | "DISABLED" | "RETIRED";

const USER_STATUS_META: Record<UserStatus, { label: string; tone: Tone }> = {
  INVITED: { label: "Invited", tone: "info" },
  ACTIVE: { label: "Active", tone: "success" },
  LOCKED: { label: "Locked", tone: "danger" },
  DISABLED: { label: "Disabled", tone: "warning" },
  RETIRED: { label: "Retired", tone: "neutral" },
};

function userStatusMeta(status: string): { label: string; tone: Tone } {
  if (Object.hasOwn(USER_STATUS_META, status)) return USER_STATUS_META[status as UserStatus];
  return {
    label: status.trim() ? humanizeToken(status.toLowerCase()) : "Unknown",
    tone: "neutral",
  };
}

export function UserStatusBadge({ status }: { status: string }) {
  const { label, tone } = userStatusMeta(status);
  return <StatusBadge tone={tone} label={label} kind="User status" />;
}
