import { Badge, Group, Paper, Progress, SimpleGrid, Stack, Text } from "@mantine/core";
import type { Objective } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { ATTAINMENT_LABEL, DIRECTION_LABEL, RAG_COLOR, RAG_LABEL, RAG_TONE } from "./labels";

function clampPct(pct: number | null): number | null {
  if (pct === null) return null;
  return Math.max(0, Math.min(100, Math.round(pct * 100)));
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between">
      <Text c="dimmed" size="sm">
        {label}
      </Text>
      <Text size="sm">{value}</Text>
    </Group>
  );
}

export function CommitmentHero({ objective: o }: { objective: Objective }) {
  const pct = clampPct(o.pct_toward_target);
  const baselineToRisk =
    o.baseline_value || o.at_risk_threshold
      ? `${o.baseline_value ?? "—"} → ${o.at_risk_threshold ?? "—"}`
      : "—";
  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg">
        <Stack gap="xs">
          <Group align="baseline" gap={6}>
            <Text fw={600} fz={32}>
              {o.current_value ?? "—"}
            </Text>
            <Text c="dimmed">
              {o.current_value ? o.unit : ""} · target {o.target_value} {o.unit}
            </Text>
          </Group>
          {pct !== null && (
            <Progress value={pct} color={RAG_COLOR[o.rag]} aria-label="Progress toward target" />
          )}
          <Group gap="xs">
            <StatusBadge tone={RAG_TONE[o.rag]} label={RAG_LABEL[o.rag]} kind="Status" />
            <Badge color="gray" variant="light">
              {ATTAINMENT_LABEL[o.attainment]}
            </Badge>
          </Group>
        </Stack>
        <Stack gap={4}>
          <MetaRow label="Direction" value={DIRECTION_LABEL[o.direction]} />
          <MetaRow label="Baseline → at-risk" value={baselineToRisk} />
          <MetaRow label="Due" value={o.due_date} />
        </Stack>
      </SimpleGrid>
    </Paper>
  );
}
