import { Alert, Anchor, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { CapaApprovalContext } from "./CapaApprovalContext";
import { DecisionCard } from "./DecisionCard";
import { useTask, useWorkflowInstance } from "./hooks";

// S-web-5 + S-web-7b: the per-task focus page. Branches on the task's subject type:
//  - DOCUMENT → instance → document → redline + the decision card (unchanged).
//  - CAPA → the CAPA approval context (identity + proposed plan, gated capa.read) + the decision card.
// The decision POST dispatches on subject type server-side, so the same DecisionCard drives both.
export function ReviewApprovePage() {
  const { id: taskId = null } = useParams();
  const { data: task, isLoading, isError, error } = useTask(taskId);
  const isCapa = task?.subject_type === "CAPA";
  // Document branch (unchanged): resolve the subject doc via the instance. Disabled for a CAPA task.
  const { data: instance } = useWorkflowInstance(!isCapa && task ? task.instance_id : null);
  const docId = !isCapa ? (instance?.subject_id ?? null) : null;
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
  const decidedAlert = (
    <Alert color="blue" title="Decided">
      This task has already been decided.
    </Alert>
  );

  if (isCapa) {
    // The CAPA subject id is on the task (no document.read-gated instance read) → always present here.
    return (
      <Stack gap="lg">
        <Title order={2}>Review &amp; Approve — Action plan</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <CapaApprovalContext capaId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard taskId={task.id} subjectType="CAPA" subjectId={task.subject_id!} />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }

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
          {/* Byte-identical to S-web-5: gate on docId too, so the card only renders once the instance→doc
              resolved (subjectId is a real document id, never "") and the cache invalidation is correct. */}
          {decidable && docId ? (
            <DecisionCard taskId={task.id} subjectType="DOCUMENT" subjectId={docId} />
          ) : (
            decidedAlert
          )}
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
