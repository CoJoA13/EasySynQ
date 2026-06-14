import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrDetail } from "../../lib/types";
import { CancelDcrModal } from "./CancelDcrModal";
import { CloseDcrAction } from "./CloseDcrAction";
import { EditDcrModal } from "./EditDcrModal";
import { ImplementCreateDcrModal } from "./ImplementCreateDcrModal";
import { ImplementDcrModal } from "./ImplementDcrModal";
import { RouteDcrModal } from "./RouteDcrModal";
import { useAssessDcr } from "./mutations";

const CANCELLABLE = ["Open", "Assessed", "Routed"];

// The DCR lifecycle cockpit (ui-2b). Affordances gate on the detail-only dcr.capabilities (server-
// computed, PROCESS-scoped) AND the FSM state — replacing ui-2a's SYSTEM-scoped can() so a PROCESS/
// DOC_CLASS grant-holder isn't hidden. Approval is decided in /tasks (the candidate-pool leg), so
// InApproval shows only a banner. Approved+Implement opens the change-type-specific modal — the
// CREATE branch (ui-4) picks the approved new document; REVISE/RETIRE release/obsolete the target.
export function DcrAdvancePanel({ dcr }: { dcr: DcrDetail }) {
  const caps = dcr.capabilities;
  const [editing, setEditing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [routing, setRouting] = useState(false);
  const [implementing, setImplementing] = useState(false);
  const assess = useAssessDcr(dcr.id);
  const [assessError, setAssessError] = useState<string | null>(null);

  const canAssess = caps?.assess === true && dcr.state === "Open";
  const canEdit = caps?.assess === true && dcr.state === "Open";
  const canCancel = caps?.close === true && CANCELLABLE.includes(dcr.state);
  const canRoute = caps?.route === true && dcr.state === "Assessed";
  const canImplement = caps?.implement === true && dcr.state === "Approved";
  const canClose = caps?.close === true && dcr.state === "Implemented";

  async function runAssess() {
    setAssessError(null);
    try {
      await assess.mutateAsync();
    } catch (e) {
      setAssessError(e instanceof ApiError ? e.message : "Could not assess the change request.");
    }
  }

  return (
    <Stack gap="xs">
      {assessError && <Alert color="red">{assessError}</Alert>}

      {dcr.state === "InApproval" && (
        <Alert color="blue" title="Awaiting approval">
          <Text size="sm">
            Decided in <b>My Tasks</b> by the assigned approver(s).
          </Text>
        </Alert>
      )}

      {canClose && <CloseDcrAction dcrId={dcr.id} />}

      <Group gap="xs">
        {canAssess && (
          <Button size="xs" onClick={() => void runAssess()} loading={assess.isPending}>
            Assess
          </Button>
        )}
        {canRoute && (
          <Button size="xs" onClick={() => setRouting(true)}>
            Route
          </Button>
        )}
        {canImplement && (
          // "Implement change" — DISTINCT from the modal's "Implement"/"Retire document"/"Force-retire"
          // submit labels, so the trigger + the open modal's submit never share an accessible name.
          <Button size="xs" onClick={() => setImplementing(true)}>
            Implement change
          </Button>
        )}
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
      </Group>

      {editing && <EditDcrModal dcr={dcr} onClose={() => setEditing(false)} />}
      {cancelling && <CancelDcrModal dcr={dcr} onClose={() => setCancelling(false)} />}
      {routing && (
        <RouteDcrModal
          dcrId={dcr.id}
          significance={dcr.change_significance}
          onClose={() => setRouting(false)}
        />
      )}
      {implementing &&
        (dcr.change_type === "CREATE" ? (
          <ImplementCreateDcrModal dcrId={dcr.id} onClose={() => setImplementing(false)} />
        ) : (
          <ImplementDcrModal
            dcrId={dcr.id}
            changeType={dcr.change_type}
            onClose={() => setImplementing(false)}
          />
        ))}
    </Stack>
  );
}
