import { Alert, Button, Divider, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";
import { CheckInPanel } from "./CheckInPanel";
import { ClauseMapper } from "./ClauseMapper";
import { useClauseMappings, useStartRevision, useSubmitReview } from "./hooks";

// The drawer's capability + state + lock-gated authoring section (DP-6). It owns the *continue /
// submit / revise* paths for an existing document (the New-Document wizard owns *new*). Renders
// nothing the caller can't do: absent capability/state yields quiet absence, never a dead button.
// Scope = the author's half (create → submit-review); approve / release are S-web-5.
function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

export function AuthorActions({ doc }: { doc: DocumentSummary }) {
  const caps = doc.capabilities;
  const state = doc.current_state;
  const draftLike = state === "Draft" || state === "UnderRevision";
  const { data: mappings } = useClauseMappings(doc.id, !!caps && draftLike);
  const startRevision = useStartRevision();
  const submitReview = useSubmitReview();
  const [error, setError] = useState<string | null>(null);

  // capabilities arrive with the detail fetch; the seeded list row has none → render nothing yet.
  if (!caps) return null;

  const clauseCount = mappings?.length ?? 0;
  const canRevise = state === "Effective" && caps.edit;
  const isDraftAuthoring = draftLike && (caps.edit || caps.manage_metadata || caps.submit);

  async function submit() {
    setError(null);
    try {
      await submitReview.mutateAsync(doc.id);
    } catch (e) {
      setError(errMsg(e));
    }
  }
  async function revise() {
    setError(null);
    try {
      await startRevision.mutateAsync(doc.id);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  if (state === "InReview") {
    return (
      <Alert color="blue" title="Awaiting review" mt="md">
        This document is in review. An approver will decide — you cannot approve your own version
        (separation of duties).
      </Alert>
    );
  }
  if (!isDraftAuthoring && !canRevise) return null;

  return (
    <Stack gap="md" mt="md">
      <Divider label="Author actions" labelPosition="left" />
      {error && (
        <Alert color="red" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {draftLike && caps.edit && (
        <CheckInPanel documentId={doc.id} sourceVersionId={doc.current_effective_version_id} />
      )}
      {draftLike && caps.manage_metadata && <ClauseMapper documentId={doc.id} />}
      {draftLike && caps.submit && (
        <Group>
          <Button
            color="teal"
            loading={submitReview.isPending}
            disabled={clauseCount < 1}
            onClick={() => void submit()}
          >
            Submit for review
          </Button>
          {clauseCount < 1 && (
            <Text size="xs" c="dimmed">
              Map at least one clause first.
            </Text>
          )}
        </Group>
      )}
      {canRevise && (
        <Group>
          <Button loading={startRevision.isPending} onClick={() => void revise()}>
            ＋ Start revision
          </Button>
        </Group>
      )}
    </Stack>
  );
}
