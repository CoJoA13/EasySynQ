import { Group, Paper, Text } from "@mantine/core";
import type { InterestedParty } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { INFLUENCE_GLYPH, INFLUENCE_TONE } from "./labels";

// The register page's scorecard — computed CLIENT-SIDE from the live rows the table shows (the WORKING
// view). It rolls up what the board scatters per-chip (the influence distribution) plus the freshness
// signal: the active/closed split (the "X of Y active" headline), the high/medium/low influence counts,
// and the never-reviewed count (rows with no last_reviewed_at). Distinct BY DESIGN from GET
// /interested-parties/summary (the GOVERNING read-of-record Home consumes) — the page is the working
// register, the endpoint is the published read-of-record.
export function InterestedPartyScorecardBand({ rows }: { rows: InterestedParty[] }) {
  const total = rows.length;
  const active = rows.filter((r) => r.status === "active").length;
  const high = rows.filter((r) => r.influence === "high").length;
  const medium = rows.filter((r) => r.influence === "medium").length;
  const low = rows.filter((r) => r.influence === "low").length;
  const unspecified = rows.filter((r) => r.influence === null).length;
  const neverReviewed = rows.filter((r) => r.last_reviewed_at === null).length;

  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          {active} of {total} active
        </Text>
        <Group gap="xs">
          <StatusBadge
            tone={INFLUENCE_TONE.high}
            glyph={INFLUENCE_GLYPH.high}
            label={`${high} high`}
            kind="Influence"
          />
          <StatusBadge
            tone={INFLUENCE_TONE.medium}
            glyph={INFLUENCE_GLYPH.medium}
            label={`${medium} medium`}
            kind="Influence"
          />
          <StatusBadge
            tone={INFLUENCE_TONE.low}
            glyph={INFLUENCE_GLYPH.low}
            label={`${low} low`}
            kind="Influence"
          />
          {unspecified > 0 && (
            <StatusBadge tone="neutral" label={`${unspecified} unspecified`} kind="Influence" />
          )}
          <StatusBadge
            tone={neverReviewed > 0 ? "warning" : "neutral"}
            label={`${neverReviewed} never reviewed`}
            kind="Interested parties"
          />
        </Group>
      </Group>
    </Paper>
  );
}
