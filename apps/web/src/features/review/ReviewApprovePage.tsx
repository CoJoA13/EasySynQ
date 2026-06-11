import { Alert, Anchor, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { useCapaApproval } from "../capa/hooks";
import { CapaApprovalContext } from "./CapaApprovalContext";
import { DecisionCard } from "./DecisionCard";
import { PeriodicReviewContext } from "./PeriodicReviewContext";
import { AttestationCard } from "./AttestationCard";
import { DocAckContext } from "./DocAckContext";
import { useTask, useWorkflowInstance } from "./hooks";

// S-web-5 + S-web-7b: the per-task focus page. Branches on the task's subject type:
//  - DOCUMENT → instance → document → redline + the decision card (unchanged).
//  - CAPA → the CAPA approval context (identity + proposed plan, gated capa.read) + the decision card.
// The decision POST dispatches on subject type server-side, so the same DecisionCard drives both.
export function ReviewApprovePage() {
  const { id: taskId = null } = useParams();
  const { data: task, isLoading, isError, error } = useTask(taskId);
  const isCapa = task?.subject_type === "CAPA";
  const isPeriodic = task?.subject_type === "PERIODIC_REVIEW";
  const isDocAck = task?.subject_type === "DOC_ACK";
  // Document branch (unchanged): resolve the subject doc via the instance. Disabled for CAPA, periodic
  // AND DOC_ACK tasks — the subject id is on the task itself; the deciding owner may hold no workflow
  // read at all.
  const { data: instance } = useWorkflowInstance(!isCapa && !isPeriodic && !isDocAck && task ? task.instance_id : null);
  const docId = !isCapa && !isPeriodic && !isDocAck ? (instance?.subject_id ?? null) : null;
  const { data: doc } = useDocument(docId, { enabled: docId !== null });
  const { data: versions } = useDocumentVersions(docId, docId !== null);
  // For a CAPA task, the approver signs the PROPOSED action plan — load it (gated capa.read) and gate the
  // decision card on it, so they never approve before the plan they're signing has actually loaded.
  const capaApproval = useCapaApproval(isCapa ? (task?.subject_id ?? null) : null);

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
            {!decidable ? (
              decidedAlert
            ) : capaApproval.isLoading ? (
              <Loader aria-label="Loading the action plan" />
            ) : capaApproval.data?.proposed_action_plan ? (
              <DecisionCard taskId={task.id} subjectType="CAPA" subjectId={task.subject_id!} />
            ) : (
              <Alert color="yellow" title="Action plan unavailable">
                The proposed action plan couldn't be loaded — refresh before approving.
              </Alert>
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }

  if (isPeriodic) {
    // The subject id is on the task (S-web-7b's detail enrichment) → always present here.
    return (
      <Stack gap="lg">
        <Title order={2}>Periodic review</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <PeriodicReviewContext documentId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard
                taskId={task.id}
                subjectType="PERIODIC_REVIEW"
                subjectId={task.subject_id!}
              />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }

  if (isDocAck) {
    // R43: an acknowledgement is append-only evidence, NOT a signature — so this is a dedicated
    // AttestationCard, never the DecisionCard. The subject id is on the task (detail enrichment) →
    // always present here; the doc context is best-effort (a document.read 403 never blocks the ack).
    return (
      <Stack gap="lg">
        <Title order={2}>Document acknowledgement</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <DocAckContext documentId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <AttestationCard taskId={task.id} documentId={task.subject_id!} />
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
