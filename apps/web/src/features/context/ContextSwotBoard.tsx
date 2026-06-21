import { Anchor, Card, Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import type { ContextIssue } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { CLASSIFICATION_GLYPH, CLASSIFICATION_LABEL } from "./labels";
import { bucketByCategory, SWOT_QUADRANTS, type SwotQuadrant } from "./swot";

// The SWOT board — the clause-4.1 visualization analogue of the risk 5×5 matrix (S-context-fe). Context
// has NO graded axis, so the natural at-a-glance view is the ISO-native SWOT 2×2: the rows bucketed by
// `category` into Strengths / Weaknesses (Internal) and Opportunities / Threats (External); Helpful left,
// Harmful right. Built CLIENT-SIDE from the live working rows (the working view — matches the table; the
// governing summary is Home's read-of-record). Like the matrix, the board is the visual SUMMARY; the
// per-row classification/status badges live (accessibly) on the table below.
export function ContextSwotBoard({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ContextIssue[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const buckets = bucketByCategory(rows);
  const uncategorized = buckets.uncategorized;

  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      component="section"
      aria-label={`SWOT analysis of ${rows.length} context ${rows.length === 1 ? "issue" : "issues"}`}
    >
      <Group justify="space-between" mb="sm">
        <Text fw={600}>SWOT analysis</Text>
        <Group gap="xl" visibleFrom="sm">
          <Text size="xs" c="dimmed">
            Helpful
          </Text>
          <Text size="xs" c="dimmed">
            Harmful
          </Text>
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        {SWOT_QUADRANTS.map((q) => (
          <Quadrant
            key={q.category}
            quadrant={q}
            issues={buckets[q.category]}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
      </SimpleGrid>

      {uncategorized.length > 0 && (
        <Card
          withBorder
          padding="sm"
          radius="sm"
          mt="sm"
          role="group"
          aria-label={groupLabel("Uncategorized", uncategorized.length)}
        >
          <Group justify="space-between" mb={6}>
            <Group gap="xs">
              <StatusBadge tone="neutral" label="Uncategorized" kind="SWOT" />
              <Text size="xs" c="dimmed">
                no SWOT category set
              </Text>
            </Group>
            <Text size="sm" c="dimmed">
              {uncategorized.length}
            </Text>
          </Group>
          <Stack gap={4}>
            {uncategorized.map((row) => (
              <IssueChip
                key={row.id}
                row={row}
                selected={row.id === selectedId}
                onSelect={onSelect}
              />
            ))}
          </Stack>
        </Card>
      )}
    </Paper>
  );
}

function groupLabel(label: string, count: number): string {
  return `${label}, ${count} ${count === 1 ? "issue" : "issues"}`;
}

function Quadrant({
  quadrant,
  issues,
  selectedId,
  onSelect,
}: {
  quadrant: SwotQuadrant;
  issues: ContextIssue[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const axis = `${CLASSIFICATION_LABEL[quadrant.classification]} · ${quadrant.helpful ? "Helpful" : "Harmful"}`;
  return (
    <Card
      withBorder
      padding="sm"
      radius="sm"
      role="group"
      aria-label={groupLabel(quadrant.label, issues.length)}
    >
      <Group justify="space-between" mb={6} wrap="nowrap">
        <div>
          <StatusBadge tone={quadrant.tone} label={quadrant.label} kind="SWOT" />
          <Text size="xs" c="dimmed" mt={2}>
            {axis}
          </Text>
        </div>
        <Text size="sm" c="dimmed">
          {issues.length}
        </Text>
      </Group>
      {issues.length === 0 ? (
        <Text size="sm" c="dimmed">
          No {quadrant.label.toLowerCase()} recorded.
        </Text>
      ) : (
        <Stack gap={4}>
          {issues.map((row) => (
            <IssueChip
              key={row.id}
              row={row}
              selected={row.id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </Stack>
      )}
    </Card>
  );
}

// One issue chip — a clickable row opening the detail drawer. The classification glyph (⌂ internal / ◇
// external) is a visual aid; the description is the button's accessible name (the table carries the full
// per-row classification badge for AT). A closed issue is de-emphasized (dimmed + a Closed badge).
function IssueChip({
  row,
  selected,
  onSelect,
}: {
  row: ContextIssue;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const closed = row.status === "closed";
  return (
    <Anchor
      component="button"
      type="button"
      onClick={() => onSelect(row.id)}
      data-rownav
      ta="left"
      underline="never"
      // The accessible name carries the classification the board groups by (the visual glyph is
      // aria-hidden) — so AT hears "Internal: …" and the board chips don't collide with the table's
      // plain-description anchors (label-in-name holds: the name contains the visible description).
      aria-label={`${CLASSIFICATION_LABEL[row.classification]}: ${row.description}`}
      bg={selected ? "var(--mantine-color-default-hover)" : undefined}
      style={{ borderRadius: 4, padding: "2px 4px" }}
    >
      <Group gap={6} wrap="nowrap">
        <Text span aria-hidden c="dimmed" title={CLASSIFICATION_LABEL[row.classification]}>
          {CLASSIFICATION_GLYPH[row.classification]}
        </Text>
        <Text
          size="sm"
          lineClamp={1}
          c={closed ? "dimmed" : undefined}
          td={closed ? "line-through" : undefined}
        >
          {row.description}
        </Text>
        {closed && <StatusBadge tone="neutral" label="Closed" kind="Status" size="xs" />}
      </Group>
    </Anchor>
  );
}
