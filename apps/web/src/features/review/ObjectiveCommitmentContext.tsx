import { Card, Stack, Table, Text } from "@mantine/core";
import type { ObjectiveCommitment } from "../../lib/types";

// Re-export so existing importers (ReviewApprovePage) keep their path.
export type { ObjectiveCommitment };

const DIRECTION_LABEL: Record<ObjectiveCommitment["direction"], string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// S-obj-3: the approver's left column for an objective approval — the FROZEN commitment from the
// version's metadata_snapshot (read off useDocumentVersions, which the approver holds document.read
// for). Replaces the page redline (meaningless for a first-release JSON-source objective).
// S-obj-4 (O-6b): on a revision, each CHANGED field renders "was → now" against the previous
// frozen commitment (the governing one being superseded); unchanged fields render plain.
export function ObjectiveCommitmentContext({
  commitment,
  previous = null,
  title,
  identifier,
}: {
  commitment: ObjectiveCommitment;
  previous?: ObjectiveCommitment | null;
  title?: string;
  identifier?: string;
}) {
  const val = (f: (c: ObjectiveCommitment) => string) =>
    previous !== null && f(previous) !== f(commitment)
      ? `${f(previous)} → ${f(commitment)}`
      : f(commitment);
  const fmtTarget = (c: ObjectiveCommitment) => `${c.target_value} ${c.unit}`;
  const fmtDirection = (c: ObjectiveCommitment) => DIRECTION_LABEL[c.direction];
  const fmtThreshold = (c: ObjectiveCommitment) =>
    c.at_risk_threshold !== null ? `${c.at_risk_threshold} ${c.unit}` : "—";
  const fmtBaseline = (c: ObjectiveCommitment) =>
    c.baseline_value !== null ? `${c.baseline_value} ${c.unit}` : "—";
  const fmtDue = (c: ObjectiveCommitment) => c.due_date;
  const fmtPolicy = (c: ObjectiveCommitment) =>
    c.policy_id !== null ? "Linked to the Quality Policy" : "—";
  const row = (label: string, value: string) => (
    <Table.Tr key={label}>
      <Table.Td>
        <Text size="sm" c="dimmed">
          {label}
        </Text>
      </Table.Td>
      <Table.Td>{value}</Table.Td>
    </Table.Tr>
  );
  return (
    <Card withBorder>
      <Stack gap="sm">
        <div>
          {identifier && (
            <Text ff="monospace" size="sm">
              {identifier}
            </Text>
          )}
          {title && <Text fw={600}>{title}</Text>}
          <Text size="xs" c="dimmed">
            {previous
              ? "The revised objective commitment you are approving — changes shown as was → now."
              : "The objective commitment you are approving."}
          </Text>
        </div>
        <Table withRowBorders={false} aria-label="Objective commitment">
          <Table.Tbody>
            {row("Target", val(fmtTarget))}
            {row("Direction", val(fmtDirection))}
            {row("At-risk threshold", val(fmtThreshold))}
            {row("Baseline", val(fmtBaseline))}
            {row("Due date", val(fmtDue))}
            {/* R25: the Quality Policy is a singleton, so presence is unambiguous (Codex P2 —
                the signer must see the frozen policy link, not have it silently hidden). */}
            {row("Quality Policy", val(fmtPolicy))}
          </Table.Tbody>
        </Table>
      </Stack>
    </Card>
  );
}
