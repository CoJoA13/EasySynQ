import { Card, Stack, Table, Text } from "@mantine/core";
import type { Objective, ObjectiveCommitment } from "../../lib/types";

const DIRECTION_LABEL: Record<ObjectiveCommitment["direction"], string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// The governing→pending field pairs, formatted for display. The detail's MAIN fields ARE the
// governing commitment (the API read-back switch), so "was" comes straight off the objective.
function rows(o: Objective, p: ObjectiveCommitment): Array<{ label: string; was: string; now: string }> {
  const fmtNum = (v: string | null, unit: string) => (v !== null ? `${v} ${unit}` : "—");
  const all = [
    { label: "Target", was: `${o.target_value} ${o.unit}`, now: `${p.target_value} ${p.unit}` },
    { label: "Direction", was: DIRECTION_LABEL[o.direction], now: DIRECTION_LABEL[p.direction] },
    { label: "At-risk threshold", was: fmtNum(o.at_risk_threshold, o.unit), now: fmtNum(p.at_risk_threshold, p.unit) },
    { label: "Baseline", was: fmtNum(o.baseline_value, o.unit), now: fmtNum(p.baseline_value, p.unit) },
    { label: "Due date", was: o.due_date, now: p.due_date },
    {
      label: "Quality Policy",
      was: o.policy_id !== null ? "Linked" : "—",
      now: p.policy_id !== null ? "Linked" : "—",
    },
  ];
  return all.filter((r) => r.was !== r.now);
}

// S-obj-4: the in-edit (unapproved) commitment, shown calmly beside the governing one. Renders
// nothing when there is no divergence — the steady state stays unmarked.
export function ProposedRevisionCard({ objective }: { objective: Objective }) {
  const pending = objective.pending_commitment;
  if (!pending) return null;
  const changed = rows(objective, pending);
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Text fw={600}>Proposed revision</Text>
        <Text size="xs" c="dimmed">
          Not yet in force — the released commitment keeps governing until this revision is
          approved and re-released.
        </Text>
        {changed.length === 0 ? (
          <Text size="sm" c="dimmed">No field changes (re-freeze pending).</Text>
        ) : (
          <Table withRowBorders={false} aria-label="Proposed commitment changes">
            <Table.Tbody>
              {changed.map((r) => (
                <Table.Tr key={r.label}>
                  <Table.Td>
                    <Text size="sm" c="dimmed">{r.label}</Text>
                  </Table.Td>
                  <Table.Td>{`${r.was} → ${r.now}`}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}
