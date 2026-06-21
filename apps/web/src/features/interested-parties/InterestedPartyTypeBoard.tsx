import { Anchor, Card, Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import type { InterestedParty, InterestedPartyType } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import {
  INFLUENCE_GLYPH,
  INFLUENCE_LABEL,
  INFLUENCE_SHORT,
  PARTY_TYPE_LABEL,
  PARTY_TYPE_SINGULAR,
} from "./labels";
import { bucketByPartyType, PARTY_TYPE_ORDER } from "./board";

// The party-type board — the clause-4.2 visualization analogue of the context SWOT board / the risk
// 5×5 matrix (S-interested-parties-fe). Clause 4.2 has NO graded axis, so the natural at-a-glance view
// is the ISO-4.2 spine: the live working rows bucketed by `party_type` into the 7 fixed cards
// (customer … partner), each chip carrying the optional `influence` (the relevance axis). Built
// CLIENT-SIDE from the live working rows (the working view — matches the table; the governing summary
// is Home's read-of-record). The board is the visual SUMMARY; the per-row type/influence/status badges
// live (accessibly) on the table below. There is NO "uncategorized" strip — party_type is NOT NULL, so
// every row buckets; an EMPTY card still renders (a completeness prompt across the spine, the SWOT
// fixed-frame analogue).
export function InterestedPartyTypeBoard({
  rows,
  selectedId,
  onSelect,
}: {
  rows: InterestedParty[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const buckets = bucketByPartyType(rows);

  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      component="section"
      aria-label={`Interested parties by type — ${rows.length} ${rows.length === 1 ? "party" : "parties"}`}
    >
      <Group justify="space-between" mb="sm">
        <Text fw={600}>Interested parties by type</Text>
        <Text size="xs" c="dimmed" visibleFrom="sm">
          Influence: ● High · ◐ Medium · ○ Low
        </Text>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="sm">
        {PARTY_TYPE_ORDER.map((type) => (
          <TypeCard
            key={type}
            type={type}
            parties={buckets[type]}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
      </SimpleGrid>
    </Paper>
  );
}

function groupLabel(label: string, count: number): string {
  return `${label}, ${count} ${count === 1 ? "party" : "parties"}`;
}

function TypeCard({
  type,
  parties,
  selectedId,
  onSelect,
}: {
  type: InterestedPartyType;
  parties: InterestedParty[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const label = PARTY_TYPE_LABEL[type];
  return (
    <Card
      withBorder
      padding="sm"
      radius="sm"
      role="group"
      aria-label={groupLabel(label, parties.length)}
    >
      <Group justify="space-between" mb={6} wrap="nowrap">
        <Text fw={500} size="sm">
          {label}
        </Text>
        <Text size="sm" c="dimmed">
          {parties.length}
        </Text>
      </Group>
      {parties.length === 0 ? (
        <Text size="sm" c="dimmed">
          None recorded.
        </Text>
      ) : (
        <Stack gap={4}>
          {parties.map((row) => (
            <PartyChip
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

// One party chip — a clickable row opening the detail drawer. The influence glyph (●◐○ / · unspecified)
// is a visual aid; the party name is the chip's text. A closed party is de-emphasized (dimmed +
// strikethrough + a Closed badge).
function PartyChip({
  row,
  selected,
  onSelect,
}: {
  row: InterestedParty;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const closed = row.status === "closed";
  const glyph = row.influence ? INFLUENCE_GLYPH[row.influence] : "·";
  const influenceTitle = row.influence ? INFLUENCE_LABEL[row.influence] : "Influence unspecified";
  const influenceShort = row.influence ? INFLUENCE_SHORT[row.influence] : "Unspecified";
  return (
    <Anchor
      component="button"
      type="button"
      onClick={() => onSelect(row.id)}
      data-rownav
      ta="left"
      underline="never"
      // The accessible name carries the party type (the board groups by it — and this disambiguates from
      // the table's plain party-name anchors), the influence level, AND the closed state. An explicit
      // aria-label OVERRIDES descendant content per the ARIA name computation, so the nested influence /
      // "Closed" badges would otherwise be swallowed, leaving influence/closed as glyph + strikethrough
      // ALONE (a DP-5 / WCAG 2.2 AA violation). Label-in-name holds (it contains the visible party name).
      aria-label={`${PARTY_TYPE_SINGULAR[row.party_type]}: ${row.party_name} — ${
        row.influence ? INFLUENCE_LABEL[row.influence] : "influence unspecified"
      }${closed ? " (closed)" : ""}`}
      bg={selected ? "var(--mantine-color-default-hover)" : undefined}
      style={{ borderRadius: 4, padding: "2px 4px" }}
    >
      <Group gap={6} wrap="nowrap">
        <Text span aria-hidden c="dimmed" title={influenceTitle}>
          {glyph}
        </Text>
        <Text
          size="sm"
          lineClamp={1}
          c={closed ? "dimmed" : undefined}
          td={closed ? "line-through" : undefined}
          style={{ flex: 1 }}
        >
          {row.party_name}
        </Text>
        <Text span size="xs" c="dimmed" aria-hidden>
          {influenceShort}
        </Text>
        {closed && <StatusBadge tone="neutral" label="Closed" kind="Status" size="xs" />}
      </Group>
    </Anchor>
  );
}
