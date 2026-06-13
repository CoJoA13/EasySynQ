import { Anchor, Badge, Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { useTask } from "../review/hooks";
import { TaskStateBadge } from "../document/TaskStateBadge";
import type { ReviewOutput } from "../../lib/types";
import { OUTPUT_LABEL } from "./labels";
import { useDeleteOutput } from "./mutations";
import { AddOutputModal } from "./AddOutputModal";
import { RaiseMrCapaModal } from "./RaiseMrCapaModal";

function ActionRow({ output, nameOf }: { output: ReviewOutput; nameOf: (id: string | null) => string }) {
  // best-effort: the spawned task 404s unless the caller is the action owner → the badge simply
  // doesn't render (the query is gated on a non-null id; a 404 never crashes the row). retry:false —
  // the 404 is the EXPECTED non-owner outcome, not a transient to re-hammer 3× (engineering-patterns).
  const { data: task } = useTask(output.spawned_task_id ?? null, { retry: false });
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm">{output.description}</Text>
      <Text size="xs" c="dimmed">
        · {nameOf(output.owner_user_id)}
        {output.due_date ? ` · due ${output.due_date}` : ""}
      </Text>
      {output.spawned_task_id && task && <TaskStateBadge state={task.state} />}
    </Group>
  );
}

export function ReviewOutputsSection({ reviewId, outputs, editable, tracking = false }: {
  reviewId: string; outputs: ReviewOutput[]; editable: boolean; tracking?: boolean;
}) {
  const { can } = usePermissions();
  const { data: directory } = useUserDirectory();
  const navigate = useNavigate();
  const del = useDeleteOutput();
  const [addOpen, setAddOpen] = useState(false);
  const [raiseFor, setRaiseFor] = useState<string | null>(null);
  const nameOf = (id: string | null) =>
    id ? (directory?.find((u) => u.id === id)?.display_name ?? "a user") : "—";
  const byType = (t: ReviewOutput["output_type"]) => outputs.filter((o) => o.output_type === t);
  const canEdit = editable && can("mgmtReview.record_outputs");
  const canRaiseCapa = tracking && can("capa.create");

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={3}>Review outputs (9.3.3)</Title>
        {canEdit && <Button size="xs" variant="light" onClick={() => setAddOpen(true)}>Add output</Button>}
      </Group>
      {(["DECISION", "ACTION", "IMPROVEMENT"] as const).map((t) => {
        const rows = byType(t);
        if (rows.length === 0) return null;
        return (
          <Card key={t} withBorder>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text fw={600}>{OUTPUT_LABEL[t]}</Text>
                <Badge variant="light">{rows.length}</Badge>
              </Group>
              {rows.map((o) => (
                <Group key={o.id} justify="space-between" wrap="nowrap">
                  {t === "ACTION" ? <ActionRow output={o} nameOf={nameOf} />
                    : <Text size="sm">{o.description}</Text>}
                  <Group gap="xs" wrap="nowrap">
                    {/* View link shows whenever a CAPA was spawned (even on a Closed review — the
                        CAPA is still viewable); only Raise is gated on the tracking window via
                        canRaiseCapa (= tracking && capa.create). */}
                    {t === "ACTION" && (
                      o.spawned_capa_id ? (
                        <Anchor component={Link} size="xs" to={`/capa?capa=${o.spawned_capa_id}`}>
                          View CAPA →
                        </Anchor>
                      ) : canRaiseCapa ? (
                        <Button size="compact-xs" variant="light" onClick={() => setRaiseFor(o.id)}>
                          Raise CAPA
                        </Button>
                      ) : null
                    )}
                    {canEdit && (
                      <Button size="compact-xs" variant="subtle" color="red"
                        onClick={() => void del.mutateAsync({ id: reviewId, oid: o.id })}>Remove</Button>
                    )}
                  </Group>
                </Group>
              ))}
            </Stack>
          </Card>
        );
      })}
      {outputs.length === 0 && <Text size="sm" c="dimmed">No outputs recorded yet.</Text>}
      {addOpen && <AddOutputModal opened reviewId={reviewId} onClose={() => setAddOpen(false)} />}
      {raiseFor && (
        <RaiseMrCapaModal
          opened
          reviewId={reviewId}
          outputId={raiseFor}
          onClose={() => setRaiseFor(null)}
          onCreated={(capaId) => navigate(`/capa?capa=${capaId}`)}
        />
      )}
    </Stack>
  );
}
