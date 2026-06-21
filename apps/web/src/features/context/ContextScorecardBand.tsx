import { Group, Paper, Text } from "@mantine/core";
import type { ContextIssue } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { CLASSIFICATION_GLYPH, CLASSIFICATION_TONE } from "./labels";

// The register page's scorecard — computed CLIENT-SIDE from the live rows the table shows (the WORKING
// view). It surfaces the axes the SWOT board doesn't emphasize: the internal/external split, the
// active/closed split, and the never-reviewed count (rows with no last_reviewed_at). Distinct BY DESIGN
// from GET /context/summary (the GOVERNING read-of-record Home consumes) — the page is the working
// register, the endpoint is the published read-of-record.
export function ContextScorecardBand({ rows }: { rows: ContextIssue[] }) {
  const total = rows.length;
  const internal = rows.filter((r) => r.classification === "internal").length;
  const external = total - internal;
  const active = rows.filter((r) => r.status === "active").length;
  const closed = total - active;
  const neverReviewed = rows.filter((r) => r.last_reviewed_at === null).length;

  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          {active} of {total} active
        </Text>
        <Group gap="xs">
          <StatusBadge
            tone={CLASSIFICATION_TONE.internal}
            glyph={CLASSIFICATION_GLYPH.internal}
            label={`${internal} internal`}
            kind="Context"
          />
          <StatusBadge
            tone={CLASSIFICATION_TONE.external}
            glyph={CLASSIFICATION_GLYPH.external}
            label={`${external} external`}
            kind="Context"
          />
          {closed > 0 && <StatusBadge tone="neutral" label={`${closed} closed`} kind="Context" />}
          <StatusBadge
            tone={neverReviewed > 0 ? "warning" : "neutral"}
            label={`${neverReviewed} never reviewed`}
            kind="Context"
          />
        </Group>
      </Group>
    </Paper>
  );
}
