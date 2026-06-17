import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { useLeadershipAuthorization, useRequestLeadershipAuthorization } from "./hooks";

const TERMINAL_STATES = new Set(["COMPLETED", "REJECTED", "NEEDS_ATTENTION"]);

// S-leadership-1: the inline release-gate panel shared by all three leadership-artifact surfaces (the
// generic document ApprovalsTab + the Objective + Management-Review detail pages). It reads the
// leadership-authorization status and SELF-SUPPRESSES (renders null) for any non-leadership doc or when
// the org flag is off — so it is safe to drop onto every document/objective/MR detail. When the gate is
// required it explains the state (authorized / awaiting / needs-attention / request) so Release is never
// a silent 409. The caller ALSO guards its Release button with the same status (`blocksRelease`), so the
// disabled-Release reason and this panel can never disagree.
export function LeadershipReleaseGate({
  documentId,
  currentState,
}: {
  documentId: string;
  currentState: string;
}) {
  const perms = usePermissions();
  const { data, isLoading } = useLeadershipAuthorization(documentId);
  const request = useRequestLeadershipAuthorization(documentId);
  const [error, setError] = useState<string | null>(null);

  if (isLoading || !data || !data.is_leadership_artifact || !data.required) return null;

  const isApproved = currentState === "Approved";
  const inst = data.instance;
  const inProgress = inst !== null && !TERMINAL_STATES.has(inst.current_state);
  const needsAttention = inst?.current_state === "NEEDS_ATTENTION";

  if (data.authorized) {
    // The gate is satisfied — confirm WHY Release is now available. Only meaningful while release is
    // pending (Approved); once Effective there is nothing to release.
    if (!isApproved) return null;
    return (
      <Alert color="teal" title="Top-Management authorization recorded">
        <Text size="sm">
          A Top-Management member has authorized this release — release may proceed.
        </Text>
      </Alert>
    );
  }

  if (inProgress) {
    return (
      <Alert color="blue" title="Awaiting Top-Management authorization">
        <Text size="sm">
          A Top-Management release authorization is in progress. Release is held until a member
          signs off.
        </Text>
      </Alert>
    );
  }

  if (needsAttention) {
    return (
      <Alert color="orange" title="Authorization can't proceed">
        <Text size="sm">
          No Top-Management member is assigned to authorize this release — contact an administrator.
        </Text>
      </Alert>
    );
  }

  // No cycle yet, or a prior one was declined → offer to request, but only when there is an Approved
  // version to authorize (the request 409s document_not_approved otherwise).
  if (!isApproved) return null;

  const canRequest = perms.can("document.approve");

  async function onRequest() {
    setError(null);
    try {
      await request.mutateAsync(undefined);
    } catch (e) {
      // already_authorized / authorization_in_progress / document_not_approved / not_a_leadership_artifact
      // all arrive as 409 — surface the server's word; the invalidated status re-reads to the right panel.
      setError(
        e instanceof ApiError ? e.message : "Could not request authorization. Please retry.",
      );
    }
  }

  return (
    <Alert color="yellow" title="Top-Management authorization required">
      <Stack gap="sm">
        <Text size="sm">
          This is a leadership artifact (Quality Policy / Objectives / Management Review). Release
          to Effective is held until a Top-Management member authorizes it.
        </Text>
        {error && (
          <Text size="sm" c="red">
            {error}
          </Text>
        )}
        {canRequest ? (
          <Group>
            <Button onClick={() => void onRequest()} loading={request.isPending}>
              Request Top-Management authorization
            </Button>
          </Group>
        ) : (
          <Text size="xs" c="dimmed">
            You don't hold the permission to request authorization — an approver must start it.
          </Text>
        )}
      </Stack>
    </Alert>
  );
}
