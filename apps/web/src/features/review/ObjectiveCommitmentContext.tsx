import { Card, Stack, Table, Text } from "@mantine/core";

// Pinned to the api build_commitment serializer (domain/objectives/commitment.py) — all decimals
// are STRINGS, direction is the enum .value, dates are ISO strings.
export interface ObjectiveCommitment {
  target_value: string;
  unit: string;
  direction: "HIGHER_IS_BETTER" | "LOWER_IS_BETTER";
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id: string | null;
}

const DIRECTION_LABEL: Record<ObjectiveCommitment["direction"], string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// S-obj-3: the approver's left column for an objective approval — the FROZEN commitment from the
// version's metadata_snapshot (read off useDocumentVersions, which the approver holds document.read
// for). Replaces the page redline (meaningless for a first-release JSON-source objective).
export function ObjectiveCommitmentContext({
  commitment,
  title,
  identifier,
}: {
  commitment: ObjectiveCommitment;
  title?: string;
  identifier?: string;
}) {
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
            The objective commitment you are approving.
          </Text>
        </div>
        <Table withRowBorders={false} aria-label="Objective commitment">
          <Table.Tbody>
            {row("Target", `${commitment.target_value} ${commitment.unit}`)}
            {row("Direction", DIRECTION_LABEL[commitment.direction])}
            {row(
              "At-risk threshold",
              commitment.at_risk_threshold !== null
                ? `${commitment.at_risk_threshold} ${commitment.unit}`
                : "—",
            )}
            {row(
              "Baseline",
              commitment.baseline_value !== null
                ? `${commitment.baseline_value} ${commitment.unit}`
                : "—",
            )}
            {row("Due date", commitment.due_date)}
          </Table.Tbody>
        </Table>
      </Stack>
    </Card>
  );
}
