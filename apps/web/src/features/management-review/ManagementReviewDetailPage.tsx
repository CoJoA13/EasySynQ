import { Alert, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError, useApi } from "../../lib/api";
import { ApprovalStepper } from "../document/ApprovalStepper";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReview, useMgmtReviewApproval } from "./hooks";
import { useCloseReview, useCompileInputs, useReleaseReview, useSubmitReview } from "./mutations";
import { ReviewInputsSection } from "./ReviewInputsSection";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

// The as-built close-gate codes (api/mgmt_review.py) → calm copy. review_close_blocked = an action
// output's spawned task isn't DONE yet; review_not_open_to_close = the review hasn't been released.
const CLOSE_CODE_COPY: Record<string, string> = {
  review_close_blocked: "Close is blocked — an action output's task isn't complete yet.",
  review_not_open_to_close: "This review isn't open to close yet (release it first).",
};

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return CLOSE_CODE_COPY[e.code] ?? e.message;
  return "Something went wrong. Please retry.";
}

export function ManagementReviewDetailPage() {
  const { id = null } = useParams();
  const { data: mr, isLoading, isError, forbidden } = useMgmtReview(id);
  const { data: instance } = useMgmtReviewApproval(id);
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();
  const api = useApi();
  const [packLoading, setPackLoading] = useState(false);
  const [packError, setPackError] = useState<string | null>(null);
  const compile = useCompileInputs();
  const submit = useSubmitReview();
  const release = useReleaseReview();
  const close = useCloseReview();
  const [actionError, setActionError] = useState<string | null>(null);

  if (isError || !mr) {
    if (isLoading)
      return (
        <Container size="lg" py="md">
          <Loader />
        </Container>
      );
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this review">
          {forbidden
            ? "You don't have access to this management review."
            : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }

  const nameOf = (uid: string | null) =>
    uid ? (directory?.find((u) => u.id === uid)?.display_name ?? "a user") : "—";
  const isDraft = mr.current_state === "Draft";
  // Affordances derive from state + permission key — EXCEPT release, which the serializer computes
  // with the SoD-2 overlay (author/approver ≠ releaser) so the button never show-then-403s (Codex #1).
  const canRecord = can("mgmtReview.record_outputs");
  const canCompile = canRecord && isDraft;
  const canSubmit = canRecord && isDraft;
  const canRelease = mr.capabilities?.release === true && mr.current_state === "Approved";
  const canClose = canRecord && mr.close_state === "ActionsTracked";

  async function run(fn: () => Promise<unknown>) {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errMsg(e));
    }
  }

  const mrId = mr.id;
  const mrIdentifier = mr.identifier;

  async function downloadPack() {
    setPackError(null);
    setPackLoading(true);
    try {
      const blob = await api.getBlob(`/api/v1/management-reviews/${mrId}/pack`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${mrIdentifier}-minutes.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setPackError(
        e instanceof ApiError && e.status === 409
          ? "Available once the review is released."
          : "Couldn't generate the pack. Please retry.",
      );
    } finally {
      setPackLoading(false);
    }
  }
  const isReleased = mr.current_state === "Effective";

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-start">
          <div>
            <Group gap="xs" mb={4}>
              <Text c="dimmed" size="sm" fw={500}>
                {mr.identifier}
              </Text>
              <StateBadge state={mr.current_state} />
            </Group>
            <Title order={2}>{mr.title}</Title>
            <Text size="sm" c="dimmed">
              {mr.period_label ?? "—"}
              {mr.review_date ? ` · ${mr.review_date}` : ""}
              {mr.attendees?.length ? ` · ${mr.attendees.map((a) => a.name).join(", ")}` : ""}
            </Text>
          </div>
          {isReleased && (
            <Button
              variant="default"
              size="xs"
              loading={packLoading}
              onClick={() => void downloadPack()}
            >
              Download minutes pack (PDF)
            </Button>
          )}
        </Group>
        {packError && (
          <Alert color="red" withCloseButton onClose={() => setPackError(null)}>
            {packError}
          </Alert>
        )}

        <ReviewInputsSection inputs={mr.inputs} />
        <ReviewOutputsSection
          reviewId={mr.id}
          outputs={mr.outputs}
          editable={isDraft}
          tracking={mr.close_state === "ActionsTracked"}
        />

        {(canCompile || canSubmit || canRelease || canClose || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {instance && (
                <ApprovalStepper
                  instance={instance}
                  docState={mr.current_state}
                  effectiveFrom={null}
                  nameOf={nameOf}
                />
              )}
              {actionError && (
                <Alert color="red" withCloseButton onClose={() => setActionError(null)}>
                  {actionError}
                </Alert>
              )}
              {canCompile && (
                <Group>
                  <Button
                    variant="light"
                    loading={compile.isPending}
                    onClick={() => void run(() => compile.mutateAsync(mr.id))}
                  >
                    Compile inputs
                  </Button>
                  <Text size="xs" c="dimmed">
                    Re-compiles the 9.3.2 inputs as-of now (Draft only).
                  </Text>
                </Group>
              )}
              {canSubmit && (
                <Group>
                  <Button
                    color="teal"
                    loading={submit.isPending}
                    disabled={compile.isPending}
                    onClick={() => void run(() => submit.mutateAsync(mr.id))}
                  >
                    Submit for review
                  </Button>
                  <Text size="xs" c="dimmed">
                    Freezes the minutes and starts approval.
                  </Text>
                </Group>
              )}
              {canRelease && (
                <Group>
                  <Button
                    color="teal"
                    loading={release.isPending}
                    onClick={() => void run(() => release.mutateAsync(mr.id))}
                  >
                    Release
                  </Button>
                  <Text size="xs" c="dimmed">
                    Releases the review → Effective (flips the 9.3 ★) and spawns action tasks.
                  </Text>
                </Group>
              )}
              {canClose && (
                <Group>
                  <Button
                    loading={close.isPending}
                    onClick={() => void run(() => close.mutateAsync(mr.id))}
                  >
                    Close review
                  </Button>
                  <Text size="xs" c="dimmed">
                    Closes once every action output's task is complete.
                  </Text>
                </Group>
              )}
            </Stack>
          </Card>
        )}
      </Stack>
    </Container>
  );
}
