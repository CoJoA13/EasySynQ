import { ActionIcon, Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import type { ObjectivePlan } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { useRemovePlan } from "./mutations";
import { AddPlanModal } from "./AddPlanModal";

function nameOf(userId: string | null, dir: { id: string; display_name: string | null }[]): string {
  if (!userId) return "no owner";
  return dir.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

export function PlansSection({ objectiveId, plans }: { objectiveId: string; plans: ObjectivePlan[] }) {
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();
  const manage = can("objective.manage");
  const remove = useRemovePlan(objectiveId);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={3}>Plans</Title>
        {manage && <Button size="xs" onClick={() => setAddOpen(true)}>Add plan</Button>}
      </Group>
      {plans.length === 0 ? (
        <Text c="dimmed" size="sm">No plans yet.</Text>
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
                  variant="subtle" color="gray" aria-label="Remove plan"
                  loading={remove.isPending && remove.variables === p.id}
                  onClick={() => remove.mutate(p.id)}
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
    </Stack>
  );
}
