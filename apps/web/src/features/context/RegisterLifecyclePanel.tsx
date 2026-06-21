import { Alert, Anchor, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { ConfirmDestructive } from "../../lib/ConfirmDestructive";
import { StatusBadge } from "../../lib/StatusBadge";
import { StateBadge } from "../document/StateBadge";
import type { ContextRegisterState } from "../../lib/types";
import { PublishRegisterModal } from "./PublishRegisterModal";
import { useReleaseContextRegister, useStartContextRegisterRevision } from "./mutations";

interface Props {
  state: ContextRegisterState | null;
  canManage: boolean; // register.manage @ SYSTEM — start-revision / publish (the org-head steward)
  canRelease: boolean; // document.release over the head's multi-axis scope — the Approved→Effective cutover (SoD-2)
}

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

// The in-app register-steward lifecycle console — it drives the THREE steward-only acts; the
// approve/decide step is a separate persona's job in the /tasks inbox (a CTX version is a DOCUMENT
// task) so it's not surfaced here.
//
// Gating is STATE × permission, both from the SERVER-computed caps on GET /context/register (the CTX
// head has no per-entity `capabilities` block): canManage = register.manage @ SYSTEM (start-revision /
// publish), canRelease = document.release over the head's multi-axis release scope (the faithful gate —
// a single-axis FE probe can't replicate artifact + folder + level + SoD-2; S-context-fe). The console
// renders only for a steward (quiet absence for everyone else); each button appears only when its state
// allows the act (never a dead/disabled affordance).
//
// We DON'T gate Publish on a client row count: GET /context is register.read-filtered, so a
// manage-without-read steward would see 0 issues for a non-empty register. The server's empty-register
// 409 is the source of truth — it surfaces calmly in PublishRegisterModal.
export function RegisterLifecyclePanel({ state, canManage, canRelease }: Props) {
  const startRevision = useStartContextRegisterRevision();
  const [publishOpen, setPublishOpen] = useState(false);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Non-stewards never see the console.
  if (!canManage && !canRelease) return null;

  const editable = state === "Draft" || state === "UnderRevision";

  async function doStartRevision() {
    setActionError(null);
    try {
      await startRevision.mutateAsync();
    } catch (e) {
      setActionError(errMsg(e));
    }
  }

  return (
    <Card withBorder mt="md">
      <Stack gap="sm">
        <Group justify="space-between">
          <Text fw={600}>Register lifecycle</Text>
          {state ? (
            <StateBadge state={state} />
          ) : (
            <StatusBadge tone="neutral" label="Not started" kind="State" />
          )}
        </Group>

        {actionError && (
          <Alert color="red" withCloseButton onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}

        {canManage && editable && (
          <Group justify="space-between" wrap="nowrap" gap="md">
            <Text size="xs" c="dimmed">
              Freezes the context issues into a new version and submits it for approval.
            </Text>
            <Button onClick={() => setPublishOpen(true)}>Publish revision</Button>
          </Group>
        )}

        {canManage && state === "Effective" && (
          <Group justify="space-between" wrap="nowrap" gap="md">
            <Text size="xs" c="dimmed">
              Opens the next revision so context issues become editable again.
            </Text>
            <Button
              variant="default"
              onClick={() => void doStartRevision()}
              loading={startRevision.isPending}
            >
              Start revision
            </Button>
          </Group>
        )}

        {canRelease && state === "Approved" && (
          <Group justify="space-between" wrap="nowrap" gap="md">
            <Text size="xs" c="dimmed">
              Promotes the approved version to Effective (the new read-of-record). You can&rsquo;t
              release a revision you authored or approved.
            </Text>
            <Button color="teal" onClick={() => setReleaseOpen(true)}>
              Release
            </Button>
          </Group>
        )}

        {state === "InReview" && (
          <Alert color="gray" variant="light">
            Submitted for review — an approver decides in{" "}
            <Anchor component={Link} to="/tasks">
              Tasks
            </Anchor>
            .
          </Alert>
        )}
      </Stack>

      {publishOpen && <PublishRegisterModal opened onClose={() => setPublishOpen(false)} />}
      <ReleaseConfirm opened={releaseOpen} onClose={() => setReleaseOpen(false)} />
    </Card>
  );
}

// The release cutover is irreversible (the new read-of-record) → route through the shared
// ConfirmDestructive (it owns busy/error and STAYS OPEN on a 409 — e.g. the SoD-2 self-release
// violation — surfacing the server reason calmly). On success the mutation invalidates the head status
// so the page re-gates; we close here.
function ReleaseConfirm({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const release = useReleaseContextRegister();
  return (
    <ConfirmDestructive
      opened={opened}
      onCancel={onClose}
      onConfirm={async () => {
        await release.mutateAsync();
        onClose();
      }}
      title="Release the context register"
      consequence="This promotes the approved version to Effective — it becomes the governing read-of-record and the working issues lock until the next revision."
      confirmLabel="Release"
      confirmColor="teal"
      mapError={errMsg}
    />
  );
}
