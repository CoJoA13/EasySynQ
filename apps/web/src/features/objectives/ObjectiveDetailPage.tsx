import { Alert, Badge, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { ApprovalStepper } from "../document/ApprovalStepper";
import { useObjective, useObjectiveApproval } from "./hooks";
import { useReleaseObjective, useSubmitObjectiveForReview } from "./mutations";
import { CommitmentHero } from "./CommitmentHero";
import { PlansSection } from "./PlansSection";
import { MeasurementsSection } from "./MeasurementsSection";

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
  const [actionError, setActionError] = useState<string | null>(null);

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

  // Affordances gate on capability AND state — quiet absence, never a dead button.
  const canSubmit = o.capabilities?.submit === true && o.current_state === "Draft";
  const canRelease = o.capabilities?.release === true && o.current_state === "Approved";

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

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4} aria-label="Objective reference">
            <Text c="dimmed" size="sm" fw={500}>{o.identifier}</Text>
            <Badge color="gray" variant="light">{o.current_state}</Badge>
          </Group>
          <Title order={2}>{o.title}</Title>
        </div>
        <CommitmentHero objective={o} />
        {(canSubmit || canRelease || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {instance && (
                <ApprovalStepper
                  instance={instance}
                  docState={o.current_state}
                  effectiveFrom={o.effective_from ?? null}
                  nameOf={nameOf}
                />
              )}
              {actionError && (
                <Alert color="red" withCloseButton onClose={() => setActionError(null)}>
                  {actionError}
                </Alert>
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
        <PlansSection objectiveId={o.id} plans={o.plans} />
        <MeasurementsSection objectiveId={o.id} unit={o.unit} />
      </Stack>
    </Container>
  );
}
