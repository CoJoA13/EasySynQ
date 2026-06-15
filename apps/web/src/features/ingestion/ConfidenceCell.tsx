import { Stack, Text } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { ImportClassification, ImportConfidenceBand } from "../../lib/types";

// DP-7: the band is NEVER colour-only — the canonical StatusBadge carries a text label, a non-colour
// glyph AND an AA-tuned colour pair (mirrors StateBadge). Owner design-call (S-statusbadge-2): HIGH→
// success, MEDIUM→warning, LOW→danger, AMBIGUOUS→danger. LOW and AMBIGUOUS both map to `danger` (✕),
// disambiguated by the text label only — a faithful 1:1 carry-over of the prior band hues; the
// LOW-vs-AMBIGUOUS semantic split is deferred to Phase 3. The displayed `% ` is kind_conf (the
// dimension the human confirms). The badge always shows the BAND (so the tone glyph never contradicts
// the label); `classification.ambiguous` is surfaced by the separate `⚖ ambiguous` caption.
const BAND_META: Record<ImportConfidenceBand, { label: string; tone: Tone }> = {
  HIGH: { label: "High", tone: "success" },
  MEDIUM: { label: "Medium", tone: "warning" },
  LOW: { label: "Low", tone: "danger" },
  AMBIGUOUS: { label: "Ambiguous", tone: "danger" },
};

export function ConfidenceCell({
  classification,
}: {
  classification: ImportClassification | null;
}) {
  if (!classification) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  // noUncheckedIndexedAccess: an unexpected band string degrades to a calm neutral label.
  const meta = BAND_META[classification.band] ?? {
    label: classification.band,
    tone: "neutral" as const,
  };
  const pct = Math.round(classification.kind_conf);
  return (
    <Stack gap={2} align="flex-start">
      <StatusBadge tone={meta.tone} label={`${meta.label} · ${pct}%`} kind="Confidence" />
      {classification.ambiguous && (
        <Text span size="xs" c="dimmed">
          ⚖ ambiguous
        </Text>
      )}
    </Stack>
  );
}
