import { Button, Group, Loader } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import type { DcrDetail } from "../../lib/types";
import { CancelDcrModal } from "./CancelDcrModal";
import { EditDcrModal } from "./EditDcrModal";

const CANCELLABLE = ["Open", "Assessed", "Routed"];

// ui-2a: the early-state write affordances (Edit while Open, Cancel while not-yet-implemented). ui-2b
// grows this into the full assess/route/implement/close panel. DCR gating is SYSTEM-scoped — the _dcr
// serializer carries no process_id, so the FE can't resolve the PROCESS scope (the read-spine precedent;
// a PROCESS-only grant-holder rides the v1 SYSTEM override).
export function DcrAdvancePanel({ dcr }: { dcr: DcrDetail }) {
  const { can, isLoading } = usePermissions();
  const [editing, setEditing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  if (isLoading) return <Loader size="sm" />;
  const canEdit = can("changeRequest.assess") && dcr.state === "Open";
  const canCancel = can("changeRequest.close") && CANCELLABLE.includes(dcr.state);
  if (!canEdit && !canCancel) return null;
  return (
    <Group gap="xs">
      {canEdit && (
        <Button size="xs" variant="light" onClick={() => setEditing(true)}>
          Edit details
        </Button>
      )}
      {canCancel && (
        <Button size="xs" variant="subtle" color="red" onClick={() => setCancelling(true)}>
          Cancel
        </Button>
      )}
      {editing && <EditDcrModal dcr={dcr} onClose={() => setEditing(false)} />}
      {cancelling && <CancelDcrModal dcr={dcr} onClose={() => setCancelling(false)} />}
    </Group>
  );
}
