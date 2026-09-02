import { ActionIcon, Button, Card, Group, Modal, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import type { ObjectivePlan } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { useRemovePlan } from "./mutations";
import { AddPlanModal } from "./AddPlanModal";
import { EmptyState } from "../../lib/states";
import { useMutationFeedback } from "../../lib/mutationFeedback";

function nameOf(userId: string | null, dir: { id: string; display_name: string | null }[]): string {
  if (!userId) return "no owner";
  return dir.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

export function PlansSection({
  objectiveId,
  plans,
}: {
  objectiveId: string;
  plans: ObjectivePlan[];
}) {
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();
  const manage = can("objective.manage");
  const remove = useRemovePlan(objectiveId);
  const [addOpen, setAddOpen] = useState(false);
  // U17: removing a plan is a PERMANENT delete with no undo — confirm before firing, and never
  // let the failure be silent (the mutation had no onError at all).
  const [confirming, setConfirming] = useState<ObjectivePlan | null>(null);
  const feedback = useMutationFeedback();

  function removePlan(plan: ObjectivePlan) {
    setConfirming(null);
    remove.mutate(plan.id, {
      onError: (error) =>
        feedback.report({
          key: `remove-plan:${plan.id}`,
          title: `This plan was not removed: ${plan.action}`,
          error,
          dismissLabel: `Dismiss remove error for ${plan.action}`,
        }),
    });
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={2} size="h3">
          Plans
        </Title>
        {manage && (
          <Button size="xs" onClick={() => setAddOpen(true)}>
            Add plan
          </Button>
        )}
      </Group>
      {plans.length === 0 ? (
        <EmptyState message="No plans yet." />
      ) : (
        plans.map((p) => (
          <Card key={p.id} withBorder padding="sm" radius="md">
            <Group justify="space-between">
              <div>
                <Text>{p.action}</Text>
                <Text c="dimmed" size="xs">
                  {nameOf(p.responsible_user_id, directory ?? [])}
                  {p.due_date ? ` · due ${p.due_date}` : " · no due date"}
                </Text>
              </div>
              {manage && (
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  // U17: one label per ROW — a repeated "Remove plan" makes getByLabelText
                  // ambiguous and gives AT users no way to tell the buttons apart.
                  aria-label={`Remove plan: ${p.action}`}
                  loading={remove.isPending && remove.variables === p.id}
                  onClick={() => setConfirming(p)}
                >
                  ✕
                </ActionIcon>
              )}
            </Group>
          </Card>
        ))
      )}
      {addOpen && (
        <AddPlanModal opened objectiveId={objectiveId} onClose={() => setAddOpen(false)} />
      )}
      <Modal
        opened={confirming !== null}
        onClose={() => setConfirming(null)}
        title="Remove this plan?"
      >
        <Stack gap="md">
          <Text size="sm">
            {confirming?.action} will be removed from this objective. This cannot be undone.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            {/* No `loading` here: removePlan closes the modal BEFORE mutating, so it could only
                ever reflect a DIFFERENT row's in-flight delete — Mantine maps loading onto the
                real `disabled`, which would silently block this confirm. The row icon carries
                the pending state, scoped by remove.variables. */}
            <Button color="red" onClick={() => confirming && removePlan(confirming)}>
              Remove plan
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
