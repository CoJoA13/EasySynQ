import { Alert, Anchor, Button, Card, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { useCapaApproval } from "../capa/hooks";
import { CapaApprovalContext } from "./CapaApprovalContext";
import { DcrApprovalContext } from "./DcrApprovalContext";
import { InitiativeApprovalContext } from "./InitiativeApprovalContext";
import { LeadershipApprovalContext } from "./LeadershipApprovalContext";
import { DecisionCard } from "./DecisionCard";
import { ObjectiveCommitmentContext, type ObjectiveCommitment } from "./ObjectiveCommitmentContext";
import { PeriodicReviewContext } from "./PeriodicReviewContext";
import { AttestationCard } from "./AttestationCard";
import { DocAckContext } from "./DocAckContext";
import { MgmtReviewContext } from "./MgmtReviewContext";
import { MrActionCard } from "./MrActionCard";
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
  const isMgmtReview = task?.subject_type === "MGMT_REVIEW";
  const isDcr = task?.subject_type === "DCR";
  const isImprovement = task?.subject_type === "IMPROVEMENT_INITIATIVE";
  const isLeadership = task?.subject_type === "LEADERSHIP_AUTHORIZATION";
  // Only a DOCUMENT-subject task resolves its subject doc via the instance; every other subject (CAPA /
  // periodic / DOC_ACK / MGMT_REVIEW / DCR) carries its subject id on the task itself and the decider may
  // hold no workflow read at all. ⚠ One named invariant in ONE place so a future arm can't forget a
  // negation: a DCR (or MR) task must NOT resolve a subject document (its subject is the change request /
  // MR container, not a kind=DOCUMENT version) — that would apply the wrong document.read gate + a
  // meaningless redline.
  const isDocumentSubject =
    !isCapa &&
    !isPeriodic &&
    !isDocAck &&
    !isMgmtReview &&
    !isDcr &&
    !isImprovement &&
    !isLeadership;
  const { data: instance } = useWorkflowInstance(
    isDocumentSubject && task ? task.instance_id : null,
  );
  const docId = isDocumentSubject ? (instance?.subject_id ?? null) : null;
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

  // S-obj-3/4: an objective subject freezes its commitment into the version metadata_snapshot —
  // render that instead of a page redline. Detection keys on the snapshot field, never the
  // document type. versions is newest-first (version_seq DESC), so [0] is the InReview commitment
  // the approver is signing. The was→now "previous" is the newest commitment-bearing version that
  // is version_state Effective — the governing one being superseded (NEVER [1] blindly: a
  // changes_requested re-freeze leaves a commitment-bearing orphan Draft behind, and on a
  // first-release cycle there is no governing version at all → plain render). Pinned by the
  // two-version revision test + the orphan-Draft regression test.
  const commitmentVersions = (versions ?? [])
    .map((v) => ({
      version: v,
      commitment: (v.metadata_snapshot as { objective_commitment?: ObjectiveCommitment } | null)
        ?.objective_commitment,
    }))
    .filter((x): x is { version: (typeof x)["version"]; commitment: ObjectiveCommitment } =>
      Boolean(x.commitment),
    );
  const objectiveCommitment = commitmentVersions[0]?.commitment ?? null;
  const previousCommitment =
    commitmentVersions.slice(1).find((x) => x.version.version_state === "Effective")?.commitment ??
    null;

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

  if (isMgmtReview) {
    // S-mr-2: an MR task's subject is the management-review container (NOT a kind=DOCUMENT version) →
    // best-effort context (mgmtReview.read, calm-403 degrade), no redline. Branch on task.type:
    //  - MR_INPUT → nav-only (prepare-the-review): NO decide affordance. The FE enforces this — the
    //    backend decide_mr_task does NOT gate on task.type, so the only thing keeping a "prepare" task
    //    out of the decision path is the absence of a complete button here.
    //  - MR_ACTION → the one-click complete card (no signature — R43). The subject id is on the task.
    const title =
      task.type === "MR_INPUT" ? "Prepare management review" : "Management review action";
    // The MR_ACTION task's stage_key is `action:<output_id>` (spawn.py) — surface WHICH output this
    // completes so an owner with several same-owner actions can't confuse them (Codex P2).
    const actionOutputId = task.stage_key.startsWith("action:")
      ? task.stage_key.slice("action:".length)
      : null;
    return (
      <Stack gap="lg">
        <Title order={2}>{title}</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <MgmtReviewContext reviewId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {task.type === "MR_INPUT" ? (
              <Card withBorder>
                <Stack gap="sm">
                  <Text fw={600}>Prepare this review</Text>
                  <Text size="sm">
                    Compile the inputs and record the outputs, then submit it for review.
                  </Text>
                  <Button component={Link} to={`/management-reviews/${task.subject_id!}`}>
                    Open the review →
                  </Button>
                </Stack>
              </Card>
            ) : decidable ? (
              <MrActionCard
                taskId={task.id}
                reviewId={task.subject_id!}
                outputId={actionOutputId}
              />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }

  if (isDcr) {
    // S-dcr-ui-2b: a DCR task's subject is the change request (NOT a kind=DOCUMENT version) →
    // best-effort context (changeRequest.read, calm-403 degrade), no redline. The subject id is on
    // the task (detail enrichment) → always present here. It SIGNS (meaning=approval); authority is
    // candidate-pool membership (server-side 404-collapse), NOT a changeRequest.approve can() check,
    // so the card shows whenever the task is PENDING.
    return (
      <Stack gap="lg">
        <Title order={2}>Review &amp; Approve — Change request</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <DcrApprovalContext dcrId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard taskId={task.id} subjectType="DCR" subjectId={task.subject_id!} />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }

  if (isImprovement) {
    // S-improvement-4: an Improvement Initiative authorization. The subject is the initiative (NOT a
    // kind=DOCUMENT version) → best-effort context (improvement.read, calm-403 degrade), no redline.
    // It SIGNS (meaning=verify); authority is candidate-pool (Top Management) membership, enforced
    // server-side (404-collapse), NOT a can() check — so the card shows whenever the task is PENDING.
    return (
      <Stack gap="lg">
        <Title order={2}>Authorize improvement — management sign-off</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <InitiativeApprovalContext initiativeId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard
                taskId={task.id}
                subjectType="IMPROVEMENT_INITIATIVE"
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

  if (isLeadership) {
    // S-leadership-1: a document-backed Top-Management RELEASE authorization (POL/OBJ/MR). The subject
    // IS a kind=DOCUMENT (its id is the documented_information id), but it must NOT route the welded
    // document-approval path (that would resolve the version + a redline + apply the approval stepper).
    // It SIGNS (meaning=verify on the Approved version); authority is candidate-pool (Top Management)
    // membership, server-side (404-collapse), NOT a can() check — the card shows whenever PENDING.
    return (
      <Stack gap="lg">
        <Title order={2}>Authorize release — Top-Management sign-off</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <LeadershipApprovalContext
              documentId={task.subject_id!}
              fallbackIdentifier={task.subject_identifier}
              fallbackTitle={task.subject_title}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard
                taskId={task.id}
                subjectType="LEADERSHIP_AUTHORIZATION"
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

  return (
    <Stack gap="lg">
      <Title order={2}>Review &amp; Approve{doc ? ` — ${doc.identifier}` : ""}</Title>
      <Grid gutter="lg" align="flex-start">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {doc && !objectiveCommitment && <Text fw={600}>{doc.title}</Text>}
            {objectiveCommitment ? (
              <ObjectiveCommitmentContext
                commitment={objectiveCommitment}
                previous={previousCommitment}
                title={doc?.title}
                identifier={doc?.identifier}
              />
            ) : (
              docId && <VersionCompare documentId={docId} versions={versions ?? []} />
            )}
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
