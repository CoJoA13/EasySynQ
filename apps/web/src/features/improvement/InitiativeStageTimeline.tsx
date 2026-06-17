import { Text, Timeline } from "@mantine/core";
import type { DirectoryUser, InitiativeStage, InitiativeStageEvent } from "../../lib/types";
import { INITIATIVE_STAGE_META } from "./labels";

function actorLabel(actorId: string | null, directory: DirectoryUser[]): string {
  if (!actorId) return "system";
  const hit = directory.find((u) => u.id === actorId);
  return hit?.display_name ?? `${actorId.slice(0, 8)}…`;
}

function stageLabel(stage: InitiativeStage): string {
  return INITIATIVE_STAGE_META[stage].label;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

// A Closed move folds a free-text realized-benefit note into payload.outcome (the lightweight 10.3
// evidence). Surface it as plain text (a String() text node — never raw HTML; the XSS rule). The
// genesis {source} key is provenance, already shown on the drawer header, so it is not re-rendered.
function outcomeOf(payload: Record<string, unknown> | null): string | null {
  if (!payload) return null;
  const value = payload["outcome"];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function InitiativeStageTimeline({
  events,
  directory,
}: {
  events: InitiativeStageEvent[];
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
      {events.map((e) => {
        const outcome = outcomeOf(e.payload);
        return (
          <Timeline.Item
            key={e.id}
            title={
              <Text span fw={600}>
                {e.from_state
                  ? `${stageLabel(e.from_state)} → ${stageLabel(e.to_state)}`
                  : stageLabel(e.to_state)}
              </Text>
            }
          >
            <Text size="xs" c="dimmed" mb={4}>
              {formatDate(e.occurred_at)} · {actorLabel(e.actor_id, directory)}
            </Text>
            {e.comment ? <Text size="sm">{e.comment}</Text> : null}
            {outcome ? (
              <Text size="sm" mt={4}>
                <Text span c="dimmed">
                  Realized benefit:{" "}
                </Text>
                {outcome}
              </Text>
            ) : null}
          </Timeline.Item>
        );
      })}
    </Timeline>
  );
}
