import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Initiative } from "../../lib/types";
import { EditInitiativeModal } from "./EditInitiativeModal";
import { RequestAuthorizationModal } from "./RequestAuthorizationModal";
import { TransitionModal } from "./TransitionModal";
import { useInitiativeAuthorization } from "./hooks";
import { useTransitionInitiative } from "./mutations";

// Instance states that mean an authorization cycle is no longer running (the engine sentinels).
const _AUTH_TERMINAL = ["COMPLETED", "REJECTED", "NEEDS_ATTENTION"];

// The clause-10.3 initiative cockpit. Affordances gate on improvement.manage at the INITIATIVE'S
// scope — PROCESS-scoped to its process_id (SYSTEM when unscoped), mirroring the CAPA AdvancePanel and
// the backend's _initiative_scope, so a Process-Owner with only a PROCESS-scoped grant can drive the
// FSM (the serializer carries NO capabilities block). Start/Complete are one-click (the FSM allows them
// with no comment); Cancel/Close open a comment-required modal (Close also folds an optional realized-
// benefit outcome). The terminal Closed/Cancelled stages expose no actions.
export function InitiativeAdvancePanel({ initiative }: { initiative: Initiative }) {
  const scope: { level: string; id?: string } = initiative.process_id
    ? { level: "PROCESS", id: initiative.process_id }
    : { level: "SYSTEM" };
  const { can } = usePermissions(scope);
  const transition = useTransitionInitiative(initiative.id);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [closing, setClosing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [requesting, setRequesting] = useState(false);

  const stage = initiative.stage;
  const active = stage === "Open" || stage === "InProgress" || stage === "Completed";

  // S-improvement-4: at Completed, a manager may EITHER close unsigned (/transition) OR request a
  // signed Top-Management authorization. Only fetch the cycle when Completed (the only state it can
  // exist in). authPending suppresses the unsigned close to avoid the close-vs-sign race (the server
  // 409 backstops it regardless); NEEDS_ATTENTION = no Top-Management member assigned (re-requestable).
  const { data: authorization } = useInitiativeAuthorization(
    stage === "Completed" ? initiative.id : null,
  );
  const authPending =
    authorization !== undefined &&
    authorization !== null &&
    !_AUTH_TERMINAL.includes(authorization.current_state);
  const authNeedsAttention = authorization?.current_state === "NEEDS_ATTENTION";

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
        {stage === "Completed" && !authPending && (
          <Button size="xs" onClick={() => setClosing(true)}>
            Close initiative
          </Button>
        )}
        {stage === "Completed" && !authPending && (
          <Button size="xs" variant="light" onClick={() => setRequesting(true)}>
            Request management authorization
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

      {authPending && (
        <Text size="xs" c="dimmed">
          Management authorization requested — awaiting a Top-Management sign-off.
        </Text>
      )}
      {authNeedsAttention && (
        <Text size="xs" c="orange.8">
          No Top-Management approver is assigned — assign one, then request again.
        </Text>
      )}

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
      {requesting && (
        <RequestAuthorizationModal initiative={initiative} onClose={() => setRequesting(false)} />
      )}
    </Stack>
  );
}
