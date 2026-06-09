import { Text, Timeline } from "@mantine/core";
import type { CapaStage, DirectoryUser } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";

function actorLabel(userId: string, directory: DirectoryUser[]): string {
  const hit = directory.find((u) => u.id === userId);
  return hit?.display_name ?? `${userId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function CapaTimeline({
  stages,
  directory,
}: {
  stages: CapaStage[];
  directory: DirectoryUser[];
}) {
  if (stages.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No stages yet.
      </Text>
    );
  }
  return (
    <Timeline active={stages.length} bulletSize={16} lineWidth={2}>
      {stages.map((s) => (
        <Timeline.Item
          key={s.id}
          title={
            <Text span fw={600}>
              {s.stage}
              {s.cycle_marker > 0 ? (
                <Text span size="xs" c="dimmed">
                  {" "}
                  &middot; Cycle {s.cycle_marker + 1}
                </Text>
              ) : null}
            </Text>
          }
        >
          <Text size="xs" c="dimmed" mb={4}>
            {formatDate(s.created_at)} &middot; {actorLabel(s.created_by, directory)}
          </Text>
          <ContentBlock block={s.content_block} />
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
