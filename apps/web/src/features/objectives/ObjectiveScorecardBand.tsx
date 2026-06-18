import { Group, Paper, Text } from "@mantine/core";
import type { ObjectiveRag, ObjectiveScorecard } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { RAG_LABEL, RAG_TONE } from "./labels";

interface Props {
  total: number;
  onTarget: number;
  byRag: ObjectiveScorecard["by_rag"];
}

// The RAG keys carry the count + the canonical status tone (success/warning/danger/neutral) — so each
// scorecard chip routes through StatusBadge: the tone supplies the AA-tuned colour pair AND a non-colour
// glyph, and the "{count} {meaning}" label disambiguates (status is NEVER colour-only, DP-7). The label
// is the MEANING ("1 on track"), never the colour word ("1 green") — S-obj-rag-legibility.
const KEYS: ObjectiveRag[] = ["green", "amber", "red", "unmeasured"];

export function ObjectiveScorecardBand({ total, onTarget, byRag }: Props) {
  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          {onTarget} / {total} on target
        </Text>
        <Group gap="xs">
          {KEYS.map((k) => (
            <StatusBadge
              key={k}
              tone={RAG_TONE[k]}
              label={`${byRag[k]} ${RAG_LABEL[k].toLowerCase()}`}
              kind="Objectives"
            />
          ))}
        </Group>
      </Group>
    </Paper>
  );
}
