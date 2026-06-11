import { Card, Group, Stack, Text, Title } from "@mantine/core";
import type { ObjectivePlan } from "../../lib/types";
import { useUserDirectory } from "../../app/shell/useUserDirectory";

function nameOf(userId: string | null, dir: { id: string; display_name: string | null }[]): string {
  if (!userId) return "no owner";
  return dir.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

export function PlansSection({ objectiveId, plans }: { objectiveId: string; plans: ObjectivePlan[] }) {
  const { data: directory } = useUserDirectory();
  void objectiveId; // used by the manage affordances in Task 14
  return (
    <Stack gap="sm">
      <Title order={4}>Plans</Title>
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
            </Group>
          </Card>
        ))
      )}
    </Stack>
  );
}
