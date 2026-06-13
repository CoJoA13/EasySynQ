import { Text, Timeline } from "@mantine/core";
import type { DcrStageEvent, DirectoryUser } from "../../lib/types";

function actorLabel(actorId: string | null, directory: DirectoryUser[]): string {
  if (!actorId) return "system";
  const hit = directory.find((u) => u.id === actorId);
  return hit?.display_name ?? `${actorId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function DcrStageTimeline({
  events,
  directory,
}: {
  events: DcrStageEvent[];
  directory: DirectoryUser[];
}) {
  if (events.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No history yet.
      </Text>
    );
  }
  return (
    <Timeline active={events.length} bulletSize={16} lineWidth={2}>
      {events.map((e) => (
        <Timeline.Item
          key={e.id}
          title={
            <Text span fw={600}>
              {e.from_state ? `${e.from_state} → ${e.to_state}` : e.to_state}
            </Text>
          }
        >
          <Text size="xs" c="dimmed" mb={4}>
            {formatDate(e.occurred_at)} · {actorLabel(e.actor_id, directory)}
          </Text>
          {e.comment ? <Text size="sm">{e.comment}</Text> : null}
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
