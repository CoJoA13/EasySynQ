import { Alert, Anchor, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { DecisionCard } from "./DecisionCard";
import { useTask, useWorkflowInstance } from "./hooks";

// S-web-5: the per-task focus page. Task → instance → subject document → the redline of what changed
// + the decision card (rendered only for a PENDING task the caller can see; GET /tasks/{id}
// 404-collapses otherwise, which we render calmly).
export function ReviewApprovePage() {
  const { id: taskId = null } = useParams();
  const { data: task, isLoading, isError, error } = useTask(taskId);
  const { data: instance } = useWorkflowInstance(task?.instance_id ?? null);
  const docId = instance?.subject_id ?? null;
  const { data: doc } = useDocument(docId, { enabled: docId !== null });
  const { data: versions } = useDocumentVersions(docId, docId !== null);

  if (isLoading) return <Loader aria-label="Loading task" />;
  if (isError || !task) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color={status === 404 ? "yellow" : "red"} title="Task unavailable">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">
            {status === 404
              ? "This task doesn't exist or isn't assigned to you."
              : "Could not load this task."}
          </Text>
          <Anchor component={Link} to="/tasks">
            ← Back to your tasks
          </Anchor>
        </Stack>
      </Alert>
    );
  }

  const decidable = task.state === "PENDING";
  return (
    <Stack gap="lg">
      <Title order={2}>Review &amp; Approve{doc ? ` — ${doc.identifier}` : ""}</Title>
      <Grid gutter="lg" align="flex-start">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {doc && <Text fw={600}>{doc.title}</Text>}
            {docId && <VersionCompare documentId={docId} versions={versions ?? []} />}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          {decidable && docId ? (
            <DecisionCard taskId={task.id} documentId={docId} />
          ) : (
            <Alert color="blue" title="Decided">
              This task has already been decided.
            </Alert>
          )}
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
