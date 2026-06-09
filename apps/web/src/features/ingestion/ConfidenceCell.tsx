import { Badge, Stack, Text } from "@mantine/core";
import type { ImportClassification, ImportConfidenceBand } from "../../lib/types";

// DP-7: the band is NEVER color-only — the text label ("<Band> · <conf>%") carries the meaning and
// color is the third redundant channel (mirrors StateBadge). HIGH=success, MEDIUM=warning,
// LOW/AMBIGUOUS=danger — matching the mockup's .es-confidence band hues. The % is kind_conf (the
// dimension the human confirms). `classification.ambiguous` adds a `⚖ ambiguous` caption.
const BAND_META: Record<ImportConfidenceBand, { label: string; color: string }> = {
  HIGH: { label: "High", color: "var(--es-success)" },
  MEDIUM: { label: "Medium", color: "var(--es-warning)" },
  LOW: { label: "Low", color: "var(--es-danger)" },
  AMBIGUOUS: { label: "Ambiguous", color: "var(--es-danger)" },
};

export function ConfidenceCell({ classification }: { classification: ImportClassification | null }) {
  if (!classification) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  // noUncheckedIndexedAccess: an unexpected band string degrades to a calm neutral label.
  const meta = BAND_META[classification.band] ?? { label: classification.band, color: "var(--es-text-muted)" };
  const pct = Math.round(classification.kind_conf);
  // When ambiguous, the aria-label reflects "Ambiguous" so the human gets the full picture;
  // the displayed text still shows the band name for the visual breakdown.
  const ariaLabel = classification.ambiguous
    ? `Confidence: Ambiguous ${pct}%`
    : `Confidence: ${meta.label} ${pct}%`;
  return (
    <Stack gap={2} align="flex-start">
      <Badge variant="light" color={meta.color} aria-label={ariaLabel}>
        {meta.label} · {pct}%
      </Badge>
      {classification.ambiguous && (
        <Text span size="xs" c="dimmed">
          ⚖ ambiguous
        </Text>
      )}
    </Stack>
  );
}
