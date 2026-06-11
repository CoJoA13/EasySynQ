import { Badge, Group, Paper, Text } from "@mantine/core";
import type { ObjectiveScorecard } from "../../lib/types";

interface Props {
  total: number;
  onTarget: number;
  byRag: ObjectiveScorecard["by_rag"];
}

const CHIPS: { key: keyof ObjectiveScorecard["by_rag"]; color: string }[] = [
  { key: "green", color: "green" },
  { key: "amber", color: "yellow" },
  { key: "red", color: "red" },
  { key: "unmeasured", color: "gray" },
];

export function ObjectiveScorecardBand({ total, onTarget, byRag }: Props) {
  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          {onTarget} / {total} on target
        </Text>
        <Group gap="xs">
          {CHIPS.map((c) => (
            <Badge key={c.key} color={c.color} variant="light">
              {byRag[c.key]} {c.key}
            </Badge>
          ))}
        </Group>
      </Group>
    </Paper>
  );
}
