import { Alert, Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DocumentSummary } from "../../lib/types";
import { useReleaseDocument } from "../authoring/hooks";
import { ApprovalStepper } from "./ApprovalStepper";
import { useDocumentApproval } from "./useDocumentApproval";

// S-web-5: the document-page Approvals card — the stepper + the contextual actions. Release shows only
// when capabilities.release is true (already SoD-2-enriched) AND the doc is Approved; the "Review &
// approve" link shows only when the caller is on the open APPROVE task (candidate-pool membership).
export function ApprovalsTab({ doc }: { doc: DocumentSummary }) {
  const { user } = useAuth();
  const myId = user?.profile?.sub ?? null;
  const { data: instance, isLoading, isError, error } = useDocumentApproval(doc.id);
  const { data: directory } = useUserDirectory();
  const release = useReleaseDocument();
  const [relErr, setRelErr] = useState<string | null>(null);

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
  const canRelease = doc.capabilities?.release === true && doc.current_state === "Approved";

  async function doRelease() {
    setRelErr(null);
    try {
      await release.mutateAsync(doc.id);
    } catch (e) {
      setRelErr(e instanceof ApiError ? e.message : "Release failed. Please retry.");
    }
  }

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
      {relErr && (
        <Alert color="red" withCloseButton onClose={() => setRelErr(null)}>
          {relErr}
        </Alert>
      )}
      {canRelease && (
        <Group>
          <Button color="teal" loading={release.isPending} onClick={() => void doRelease()}>
            Release
          </Button>
          <Text size="xs" c="dimmed">
            Releases the Approved version → Effective.
          </Text>
        </Group>
      )}
    </Stack>
  );
}
