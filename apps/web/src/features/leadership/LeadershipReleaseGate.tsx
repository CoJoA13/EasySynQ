import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
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

  // No cycle yet, a prior one was declined (REJECTED), or it stalled with no Top-Management member
  // (NEEDS_ATTENTION) — all three are terminal/re-requestable, so offer to request, but only when there
  // is an Approved version to authorize (the request 409s document_not_approved otherwise). CX-2: the
  // NEEDS_ATTENTION case keeps the Request affordance so an approver can retry once an admin assigns a
  // Top-Management member (the backend permits a fresh cycle from a terminal NEEDS_ATTENTION instance).
  if (!isApproved) return null;

  // CX-1: gate on the server-computed, ABAC-aware per-document capability (does the caller hold
  // document.approve at THIS doc's scope) — NOT a SYSTEM-scoped /me/permissions probe, which missed a
  // content/process/folder/doc-class-scoped approver. `data` is non-null past the early-return guard.
  const canRequest = data.can_request === true;

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
    <Alert
      color={needsAttention ? "orange" : "yellow"}
      title={
        needsAttention
          ? "Top-Management authorization couldn't proceed"
          : "Top-Management authorization required"
      }
    >
      <Stack gap="sm">
        <Text size="sm">
          {needsAttention
            ? "The previous request couldn't proceed — no Top-Management member was assigned. Once an administrator assigns one, request authorization again."
            : "This is a leadership artifact (Quality Policy / Objectives / Management Review). Release to Effective is held until a Top-Management member authorizes it."}
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
