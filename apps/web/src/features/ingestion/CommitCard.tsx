import { Alert, Badge, Button, Card, Group, Progress, Stack, Text } from "@mantine/core";
import type { ImportChecklist } from "../../lib/types";

// The "On commit" card (mockup #screen-ingestion §10). Presentational: ReviewCockpit owns the
// permission + mutation state and passes `canCommit` (= can("import.commit")) and `committing`
// (the useCommitRun mutation's isPending) down. The commit-enable predicate is the spec D-3 rule —
// commit is enabled iff the run is `ready` (zero blocking conflicts) AND ≥1 item is commit_ready AND
// the caller holds import.commit AND a commit isn't already in flight. Unconfirmed kind is ADVISORY
// (surfaced in PreCommitChecklist), never a hard block here. When the caller lacks import.commit a
// deployment may split SoD (Mara reviews, Avery commits) — render a calm note, not a dead button.
export function CommitCard({
  checklist,
  canCommit,
  committing,
  onCommit,
}: {
  checklist: ImportChecklist;
  canCommit: boolean;
  committing: boolean;
  onCommit: () => void;
}) {
  const ready = checklist.review.commit_ready;
  const keep = checklist.review.keep_items;
  // Progress fraction: commit-ready over the keep set; guard the zero-divide under strict checks.
  const pct = keep > 0 ? Math.min(100, Math.round((ready / keep) * 100)) : 0;
  const enabled = checklist.ready && ready >= 1 && canCommit && !committing;

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap={2} mb="sm">
        <Text fw={600}>On commit</Text>
        <Text size="sm" c="dimmed">
          Per-item, transactional, audited.
        </Text>
      </Stack>

      <Group gap="sm" mb="sm" wrap="nowrap">
        <Progress
          value={pct}
          color="var(--es-success)"
          aria-label={`${ready} of ${keep} items commit-ready`}
          style={{ flex: 1 }}
        />
        <Text size="sm" c="dimmed" style={{ whiteSpace: "nowrap" }}>
          {ready} ready
        </Text>
      </Group>

      <Stack gap={6} component="dl" mb="md">
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Baseline
          </Text>
          <Text component="dd" size="sm" m={0}>
            <Badge variant="light" color="var(--es-do)" mr={6}>
              Effective Rev A
            </Badge>
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Signature
          </Text>
          <Text component="dd" size="sm" ff="monospace" m={0}>
            import_baseline
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Storage
          </Text>
          <Text component="dd" size="sm" m={0}>
            WORM vault blob · content-addressed
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Provenance
          </Text>
          <Text component="dd" size="sm" m={0}>
            source path · sha256 · run · decided-by
          </Text>
        </Group>
      </Stack>

      {canCommit ? (
        <Button
          fullWidth
          color="var(--es-do)"
          onClick={onCommit}
          disabled={!enabled}
          loading={committing}
        >
          Commit {ready} confirmed
        </Button>
      ) : (
        <Alert color="gray" variant="light" title="Commit held">
          Commit is held by another role (import.commit).
        </Alert>
      )}
    </Card>
  );
}
