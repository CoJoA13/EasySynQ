// apps/web/src/features/capa/AdvancePanel.tsx
import { Alert, Loader, Stack, Text } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Capa } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";
import { useCapaApproval } from "./hooks";
import {
  ActionPlanForm,
  CloseAction,
  ContainmentForm,
  ImplementForm,
  RootCauseForm,
  VerifyForm,
} from "./StageForms";

// An approval instance is "pending" until it reaches a terminal sentinel. NEEDS_ATTENTION = an
// abandoned fail-closed instance (no approver assigned) → re-propose is allowed after assigning one.
const APPROVAL_TERMINAL = ["COMPLETED", "REJECTED", "NEEDS_ATTENTION"];

export function AdvancePanel({ capa }: { capa: Capa }) {
  const scope: { level: string; id?: string } = capa.process_id
    ? { level: "PROCESS", id: capa.process_id }
    : { level: "SYSTEM" };
  const perms = usePermissions(scope);
  // Only the RootCause state needs the approval read (to distinguish propose-vs-awaiting).
  const approval = useCapaApproval(capa.close_state === "RootCause" ? capa.id : null);

  function gated(key: string, node: React.ReactNode) {
    if (perms.isLoading) return <Loader size="sm" />;
    if (!perms.can(key))
      return (
        <Text size="sm" c="dimmed">
          You don't hold the permission to advance this CAPA.
        </Text>
      );
    return node;
  }

  switch (capa.close_state) {
    case "Raised":
      return gated("capa.update", <ContainmentForm capa={capa} />);
    case "Containment":
      return gated("capa.record_rca", <RootCauseForm capa={capa} />);
    case "RootCause": {
      // Wait for the approval read before deciding propose-vs-awaiting — otherwise the propose form flashes
      // briefly (data is undefined) before the "awaiting approval" banner replaces it.
      if (approval.isLoading) return <Loader size="sm" />;
      const inst = approval.data?.instance;
      if (inst && inst.current_state === "NEEDS_ATTENTION")
        // NEEDS_ATTENTION = an abandoned, fail-closed instance (no approver was assignable) — the server
        // treats it as terminal and ALLOWS a fresh proposal. So show the warning AND the propose form, or
        // the user is stuck (the latest instance stays NEEDS_ATTENTION until a new proposal is submitted).
        return (
          <Stack gap="sm">
            <Alert color="yellow" title="No approver assigned">
              Assign a QMS Owner / Top Management approver, then propose a revised action plan below.
            </Alert>
            {gated("capa.plan_action", <ActionPlanForm capa={capa} />)}
          </Stack>
        );
      if (inst && !APPROVAL_TERMINAL.includes(inst.current_state))
        return (
          <Alert color="blue" title="Action plan awaiting approval">
            <Text size="sm" mb="xs">
              Decided in <b>My Tasks</b> by the assigned approver.
            </Text>
            <ContentBlock block={approval.data?.proposed_action_plan ?? {}} />
          </Alert>
        );
      return gated("capa.plan_action", <ActionPlanForm capa={capa} />);
    }
    case "ActionPlan":
      return gated("capa.capture_effectiveness", <ImplementForm capa={capa} />);
    case "Implement":
      return gated("capa.verify", <VerifyForm capa={capa} />);
    case "Verify":
      return gated("capa.close", <CloseAction capa={capa} />);
    default:
      return null; // Closed / Rejected — terminal
  }
}
