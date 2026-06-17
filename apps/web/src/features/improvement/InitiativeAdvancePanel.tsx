import { Alert, Button, Group, Stack } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Initiative } from "../../lib/types";
import { EditInitiativeModal } from "./EditInitiativeModal";
import { TransitionModal } from "./TransitionModal";
import { useTransitionInitiative } from "./mutations";

// The clause-10.3 initiative cockpit. Affordances gate on usePermissions().can("improvement.manage")
// (PROCESS finest-scope, SYSTEM fallback in v1 — the MR cockpit precedent; the initiative serializer
// carries NO capabilities block) AND the FSM stage. Start/Complete are one-click (the FSM allows them
// with no comment); Cancel/Close open a comment-required modal (Close also folds an optional realized-
// benefit outcome). The terminal Closed/Cancelled stages expose no actions.
export function InitiativeAdvancePanel({ initiative }: { initiative: Initiative }) {
  const { can } = usePermissions();
  const transition = useTransitionInitiative(initiative.id);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [closing, setClosing] = useState(false);
  const [editing, setEditing] = useState(false);

  const stage = initiative.stage;
  const active = stage === "Open" || stage === "InProgress" || stage === "Completed";

  async function quickMove(toState: "InProgress" | "Completed") {
    setError(null);
    try {
      await transition.mutateAsync({ to_state: toState });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not update the initiative.");
    }
  }

  if (!can("improvement.manage")) return null;

  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      <Group gap="xs">
        {stage === "Open" && (
          <Button
            size="xs"
            onClick={() => void quickMove("InProgress")}
            loading={transition.isPending}
          >
            Start work
          </Button>
        )}
        {stage === "InProgress" && (
          <Button
            size="xs"
            onClick={() => void quickMove("Completed")}
            loading={transition.isPending}
          >
            Mark completed
          </Button>
        )}
        {stage === "Completed" && (
          <Button size="xs" onClick={() => setClosing(true)}>
            Close initiative
          </Button>
        )}
        {(stage === "Open" || stage === "InProgress") && (
          <Button size="xs" variant="subtle" color="red" onClick={() => setCancelling(true)}>
            Cancel initiative
          </Button>
        )}
        {active && (
          <Button size="xs" variant="light" onClick={() => setEditing(true)}>
            Edit details
          </Button>
        )}
      </Group>

      {cancelling && (
        <TransitionModal
          initiative={initiative}
          toState="Cancelled"
          title="Cancel initiative"
          description={`This cancels ${initiative.identifier}. It can't be undone.`}
          confirmLabel="Confirm cancellation"
          confirmColor="red"
          onClose={() => setCancelling(false)}
        />
      )}
      {closing && (
        <TransitionModal
          initiative={initiative}
          toState="Closed"
          title="Close initiative"
          description={`This closes ${initiative.identifier}. Record the realized benefit if there is one.`}
          confirmLabel="Confirm close"
          withOutcome
          onClose={() => setClosing(false)}
        />
      )}
      {editing && <EditInitiativeModal initiative={initiative} onClose={() => setEditing(false)} />}
    </Stack>
  );
}
