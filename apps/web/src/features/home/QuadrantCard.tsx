import { Anchor, Badge, Group, Paper, Skeleton, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import type { Rag } from "./rag";
import { RAG_META } from "./rag";

const PHASE_TOKEN: Record<PdcaPhase, string> = { PLAN: "plan", DO: "do", CHECK: "check", ACT: "act" };

// A calm no-access body (the whole tile's reads were forbidden) and a two-line skeleton (still loading).
export const TileNoAccess = () => (
  <Text size="sm" c="dimmed">No access to this section&apos;s data.</Text>
);
export const TileSkeleton = () => (
  <Stack gap={6}>
    <Skeleton height={14} width="80%" />
    <Skeleton height={14} width="55%" />
  </Stack>
);

// One PDCA region (doc-11 §5.1 "nav of four labeled regions"): an accent label chip + the headline RAG
// badge (omitted when rag is null) + the signal body + exactly one accent Open action (DP-2).
export function QuadrantCard({ phase, clauseLabel, rag, openTo, openLabel, children }: {
  phase: PdcaPhase;
  clauseLabel: string;
  rag: Rag | null;
  openTo: string;
  openLabel: string;
  children: ReactNode;
}) {
  const tok = PHASE_TOKEN[phase];
  return (
    <Paper withBorder radius="md" p="md" role="group" aria-label={`${phase} quadrant`}>
      <Stack gap="sm" h="100%">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Text
            span
            fw={500}
            style={{
              background: `var(--es-${tok}-soft)`,
              color: `var(--es-${tok}-text)`,
              borderRadius: 8,
              padding: "2px 10px",
              fontSize: 13,
            }}
          >
            {phase} · {clauseLabel}
          </Text>
          {rag && (
            <Badge
              variant="light"
              color={RAG_META[rag].color}
              leftSection={<span aria-hidden>{RAG_META[rag].glyph}</span>}
              aria-label={`Status: ${RAG_META[rag].label}`}
            >
              {RAG_META[rag].label}
            </Badge>
          )}
        </Group>
        <Stack gap={6} style={{ flex: 1 }}>
          {children}
        </Stack>
        <Anchor component={Link} to={openTo} size="sm">
          {openLabel} <span aria-hidden="true">→</span>
        </Anchor>
      </Stack>
    </Paper>
  );
}
