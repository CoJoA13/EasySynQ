import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { CapaCloseState } from "../../lib/types";
import { CLOSE_STATE_LABEL, CLOSE_STATE_TONE } from "./columns";

export function CapaStateBadge({
  state,
  size = "sm",
}: {
  state: CapaCloseState;
  size?: MantineSize;
}) {
  return (
    <StatusBadge
      tone={CLOSE_STATE_TONE[state]}
      label={CLOSE_STATE_LABEL[state]}
      kind="CAPA state"
      size={size}
    />
  );
}
