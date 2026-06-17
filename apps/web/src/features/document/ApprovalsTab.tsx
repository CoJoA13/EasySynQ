import { Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../../app/shell/useMe";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { ConfirmDestructive } from "../../lib/ConfirmDestructive";
import type { DocumentSummary } from "../../lib/types";
import { useReleaseDocument } from "../authoring/hooks";
import { useLeadershipAuthorization } from "../leadership/hooks";
import { LeadershipReleaseGate } from "../leadership/LeadershipReleaseGate";
import { ApprovalStepper } from "./ApprovalStepper";
import { useDocumentApproval } from "./useDocumentApproval";

// S-web-5: the document-page Approvals card — the stepper + the contextual actions. Release shows only
// when capabilities.release is true (already SoD-2-enriched) AND the doc is Approved; the "Review &
// approve" link shows only when the caller is on the open APPROVE task (candidate-pool membership).
export function ApprovalsTab({ doc }: { doc: DocumentSummary }) {
  // app_user.id (GET /me), NOT the OIDC sub — candidate_pool/assignee_user_id are app_user ids.
  const { data: me } = useMe();
  const myId = me?.id ?? null;
  const { data: instance, isLoading, isError, error } = useDocumentApproval(doc.id);
  const { data: directory } = useUserDirectory();
  const release = useReleaseDocument();
  // S-leadership-1: for a leadership artifact (POL/OBJ/MR) with the org flag on, release is held until a
  // Top-Management member authorizes it. `blocksRelease` suppresses the Release button (capabilities.release
  // does NOT fold in the gate); the panel below explains/offers the request.
  const lead = useLeadershipAuthorization(doc.id);
  const [confirming, setConfirming] = useState(false);

  const nameOf = (id: string | null) =>
    id ? (directory?.find((u) => u.id === id)?.display_name ?? "a user") : "—";

  if (isLoading)
    return (
      <Text size="sm" c="dimmed">
        Loading approvals…
      </Text>
    );
  if (isError && error instanceof ApiError && error.status === 403)
    return (
      <Text size="sm" c="dimmed">
        You don't have access to the approval history.
      </Text>
    );
  if (isError)
    return (
      <Text size="sm" c="red">
        Could not load approvals.
      </Text>
    );
  if (!instance)
    return (
      <Text size="sm" c="dimmed">
        No approval activity yet.
      </Text>
    );

  const myOpenTask = (instance.tasks ?? []).find(
    (t) =>
      t.state === "PENDING" &&
      (t.assignee_user_id === myId || (t.candidate_pool ?? []).includes(myId ?? "")),
  );
  const canRelease =
    doc.capabilities?.release === true && doc.current_state === "Approved" && !lead.blocksRelease;

  return (
    <Stack gap="md">
      <ApprovalStepper
        instance={instance}
        docState={doc.current_state}
        effectiveFrom={doc.effective_from}
        nameOf={nameOf}
      />
      {myOpenTask && (
        <Anchor component={Link} to={`/tasks/${myOpenTask.id}`}>
          Review &amp; approve →
        </Anchor>
      )}
      {/* The gate renders after the !instance guard above — safe because an Approved doc (the only state
          where the gate is actionable) always has an approval instance. OBJ/MR guard their card with
          lead.blocksRelease instead, since their Lifecycle card can collapse without an instance. */}
      <LeadershipReleaseGate documentId={doc.id} currentState={doc.current_state} />
      {canRelease && (
        <Group>
          <Button color="teal" onClick={() => setConfirming(true)}>
            Release
          </Button>
          <Text size="xs" c="dimmed">
            Releases the Approved version → Effective.
          </Text>
        </Group>
      )}
      <ConfirmDestructive
        opened={confirming}
        onCancel={() => setConfirming(false)}
        onConfirm={async () => {
          await release.mutateAsync(doc.id);
          setConfirming(false);
        }}
        title="Release this document?"
        consequence="Releases the Approved version to Effective and supersedes the current Effective version."
        confirmLabel="Release document"
        confirmColor="teal"
        mapError={(e) => (e instanceof ApiError ? e.message : "Release failed. Please retry.")}
      />
    </Stack>
  );
}
