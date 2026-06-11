import { Group, RingProgress, Stack, Text } from "@mantine/core";
import type { Coverage } from "../../lib/types";

// S-ack-2: the shared read-and-understood coverage widget (the Acknowledged tile + the Acks tab).
// Rides the document.read-gated distribution GET; coverage is null when there is no Effective version,
// and all-zeros when the ack flag is off (an Effective version exists but no obligations).
export function AckCoverageRing({ coverage, size = 88 }: { coverage: Coverage | null; size?: number }) {
  if (coverage === null) {
    return (
      <Stack gap={2}>
        <Text size="xl" fw={700}>—</Text>
        <Text size="xs" c="dimmed">Not yet effective</Text>
      </Stack>
    );
  }
  if (coverage.required === 0) {
    return (
      <Stack gap={2}>
        <Text size="xl" fw={700}>—</Text>
        <Text size="xs" c="dimmed">Not distributed for acknowledgement</Text>
      </Stack>
    );
  }
  const pct = Math.round((coverage.acknowledged / coverage.required) * 100);
  return (
    <Group gap="md" wrap="nowrap" align="center">
      <RingProgress
        size={size}
        thickness={8}
        roundCaps
        sections={[{ value: pct, color: "green" }]}
        label={<Text ta="center" size="sm" fw={700}>{pct}%</Text>}
        aria-label={`Acknowledgement coverage ${pct} percent`}
      />
      <Stack gap={2}>
        <Text size="xl" fw={700}>{coverage.acknowledged} / {coverage.required}</Text>
        <Text size="xs" c="dimmed">
          {coverage.pending} pending{coverage.overdue > 0 ? ` · ${coverage.overdue} overdue` : ""}
        </Text>
      </Stack>
    </Group>
  );
}
