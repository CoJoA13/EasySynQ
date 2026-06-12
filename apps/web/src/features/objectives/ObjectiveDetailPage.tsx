import { Alert, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { ApprovalStepper } from "../document/ApprovalStepper";
import { StateBadge } from "../document/StateBadge";
import { useObjective, useObjectiveApproval } from "./hooks";
import { useReleaseObjective, useStartObjectiveRevision, useSubmitObjectiveForReview } from "./mutations";
import { CommitmentHero } from "./CommitmentHero";
import { EditCommitmentModal } from "./EditCommitmentModal";
import { PlansSection } from "./PlansSection";
import { MeasurementsSection } from "./MeasurementsSection";
import { ProposedRevisionCard } from "./ProposedRevisionCard";

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

export function ObjectiveDetailPage() {
  const { id = null } = useParams();
  const { data: o, isLoading, isError, forbidden } = useObjective(id);
  const { data: instance } = useObjectiveApproval(id);
  const { data: directory } = useUserDirectory();
  const submit = useSubmitObjectiveForReview();
  const release = useReleaseObjective();
  const startRevision = useStartObjectiveRevision();
  const [actionError, setActionError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  if (isError || !o) {
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this objective">
          {forbidden
            ? "You don't have access to this objective."
            : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }

  // The ApprovalsTab nameOf: app_user.id → display name via the shared directory; the directory
  // read may 403 for a non-admin (data undefined) — fall back, never block the stepper.
  const nameOf = (userId: string | null) =>
    userId ? (directory?.find((u) => u.id === userId)?.display_name ?? "a user") : "—";

  const draftLike = o.current_state === "Draft" || o.current_state === "UnderRevision";
  const underRevision = o.current_state === "UnderRevision";
  // Affordances gate on capability AND state — quiet absence, never a dead button (the
  // AuthorActions posture: canRevise = Effective && caps; draftLike = Draft ∪ UnderRevision).
  const canSubmit = o.capabilities?.submit === true && draftLike;
  const canRelease = o.capabilities?.release === true && o.current_state === "Approved";
  const canStartRevision = o.capabilities?.start_revision === true && o.current_state === "Effective";
  const canEdit = o.capabilities?.edit === true && draftLike;

  async function doSubmit() {
    if (!id) return;
    setActionError(null);
    try {
      await submit.mutateAsync(id);
    } catch (e) {
      setActionError(errMsg(e));
    }
  }

  async function doRelease() {
    if (!id) return;
    setActionError(null);
    try {
      await release.mutateAsync(id);
    } catch (e) {
      setActionError(errMsg(e));
    }
  }

  async function doStartRevision() {
    if (!id) return;
    setActionError(null);
    try {
      await startRevision.mutateAsync(id);
    } catch (e) {
      setActionError(errMsg(e));
    }
  }

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4} aria-label="Objective reference">
            <Text c="dimmed" size="sm" fw={500}>{o.identifier}</Text>
            <StateBadge state={o.current_state} />
          </Group>
          <Title order={2}>{o.title}</Title>
        </div>
        <CommitmentHero objective={o} />
        <ProposedRevisionCard objective={o} />
        {(canSubmit || canRelease || canStartRevision || canEdit || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {underRevision ? (
                // O-6a: the latest-instance read still returns v1's COMPLETED cycle here — the
                // stepper would render "Not yet released" against a doc that IS released. A calm
                // panel replaces it until re-submit creates the v2 instance.
                <Alert color="yellow" title="Revision in progress">
                  The released commitment keeps governing until this revision is approved and
                  re-released.
                </Alert>
              ) : (
                instance && (
                  <ApprovalStepper
                    instance={instance}
                    docState={o.current_state}
                    effectiveFrom={o.effective_from ?? null}
                    nameOf={nameOf}
                  />
                )
              )}
              {actionError && (
                <Alert color="red" withCloseButton onClose={() => setActionError(null)}>
                  {actionError}
                </Alert>
              )}
              {canStartRevision && (
                <Group>
                  <Button
                    variant="default"
                    loading={startRevision.isPending}
                    onClick={() => void doStartRevision()}
                  >
                    Start revision
                  </Button>
                  <Text size="xs" c="dimmed">
                    Opens an editable draft — the released commitment keeps governing.
                  </Text>
                </Group>
              )}
              {canEdit && (
                <Group>
                  <Button variant="default" onClick={() => setEditOpen(true)}>
                    Edit commitment
                  </Button>
                </Group>
              )}
              {canSubmit && (
                <Group>
                  <Button color="teal" loading={submit.isPending} onClick={() => void doSubmit()}>
                    Submit for review
                  </Button>
                  <Text size="xs" c="dimmed">Freezes the commitment and starts approval.</Text>
                </Group>
              )}
              {canRelease && (
                <Group>
                  <Button color="teal" loading={release.isPending} onClick={() => void doRelease()}>
                    Release
                  </Button>
                  <Text size="xs" c="dimmed">Releases the Approved objective → Effective.</Text>
                </Group>
              )}
            </Stack>
          </Card>
        )}
        {editOpen && (
          <EditCommitmentModal opened objective={o} onClose={() => setEditOpen(false)} />
        )}
        <PlansSection objectiveId={o.id} plans={o.plans} />
        <MeasurementsSection objectiveId={o.id} unit={o.unit} />
      </Stack>
    </Container>
  );
}
