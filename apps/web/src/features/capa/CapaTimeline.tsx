import { Badge, Group, Text, Timeline } from "@mantine/core";
import type { CapaStage, DirectoryUser } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";
import { EvidenceLinker } from "./EvidenceLinker";

function actorLabel(userId: string, directory: DirectoryUser[]): string {
  const hit = directory.find((u) => u.id === userId);
  return hit?.display_name ?? `${userId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

const EVIDENCE_STAGES = new Set(["Implement", "Verify"]);

export function CapaTimeline({
  stages,
  directory,
  capaId,
  cycleMarker,
}: {
  stages: CapaStage[];
  directory: DirectoryUser[];
  capaId: string;
  cycleMarker: number;
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
          {(s.evidence_links?.length ?? 0) > 0 && (
            <Group gap="xs" mt={4}>
              <Text size="xs" fw={600} c="dimmed">
                Linked records:
              </Text>
              {(s.evidence_links ?? []).map((l) => (
                // A label chip, not a link — the record identifier is read-only context here (no
                // navigation target), so it must NOT render as a focusable/styled anchor.
                <Badge key={l.id} variant="light" color="gray" size="sm">
                  {l.record_identifier ?? l.record_id}
                </Badge>
              ))}
            </Group>
          )}
          {/* Only the CURRENT cycle's Implement/Verify stages get a linker: links on a superseded cycle
              can't satisfy the close gate (which reads current-cycle evidence), and rendering past-cycle
              linkers would duplicate the "Record (Verify)" accessible name across cycles (the S-web-6
              getByLabelText trap). Within one cycle there is at most one Implement + one Verify, so the
              per-stage suffix keeps the two labels distinct. */}
          {EVIDENCE_STAGES.has(s.stage) && s.cycle_marker === cycleMarker && (
            <div style={{ marginTop: 6 }}>
              <EvidenceLinker capaId={capaId} stageId={s.id} labelSuffix={` (${s.stage})`} />
            </div>
          )}
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
