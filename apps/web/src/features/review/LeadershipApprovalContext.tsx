import { Alert, Stack, Text, Title } from "@mantine/core";
import { ApiError } from "../../lib/api";
import { LoadingState } from "../../lib/states";
import { StateBadge } from "../document/StateBadge";
import { useDocument } from "../document/useDocument";

// S-leadership-1: the document-subject context on the /tasks decision page — the leadership artifact
// (POL/OBJ/MR) whose RELEASE the Top-Management member is authorizing. Read BEST-EFFORT via
// document.read (a separate gate from candidate-pool membership); a 403 degrades calmly to the identity
// carried on the task and never blocks the decision card (authority is pool membership, server-side —
// the InitiativeApprovalContext / DcrApprovalContext shape). NEVER routes the welded approval/redline.
export function LeadershipApprovalContext({
  documentId,
  fallbackIdentifier,
  fallbackTitle,
}: {
  documentId: string;
  fallbackIdentifier?: string | null;
  fallbackTitle?: string | null;
}) {
  const {
    data: doc,
    isLoading,
    isError,
    error,
  } = useDocument(documentId, {
    enabled: true,
    retry: false,
  });
  const forbidden = error instanceof ApiError && error.status === 403;

  if (isLoading) return <LoadingState label="Loading the document" />;

  if (isError || !doc) {
    return (
      <Alert color="yellow" title="Document not fully visible to you">
        <Stack gap="xs">
          {(fallbackIdentifier || fallbackTitle) && (
            <div>
              {fallbackIdentifier && (
                <Text size="xs" c="dimmed">
                  {fallbackIdentifier}
                </Text>
              )}
              {fallbackTitle && <Text fw={600}>{fallbackTitle}</Text>}
            </div>
          )}
          <Text size="sm">
            {forbidden
              ? "You can authorize this release, but reading the full document isn't granted to you."
              : "Could not load the document."}
          </Text>
        </Stack>
      </Alert>
    );
  }

  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {doc.identifier}
        </Text>
        <Title order={3}>{doc.title}</Title>
      </div>
      <StateBadge state={doc.current_state} />
      <Text size="sm" c="dimmed">
        Authorizing the Top-Management release of this leadership artifact to Effective. Your
        sign-off records a verify signature on the Approved version; declining leaves it Approved
        (re-requestable).
      </Text>
    </Stack>
  );
}
