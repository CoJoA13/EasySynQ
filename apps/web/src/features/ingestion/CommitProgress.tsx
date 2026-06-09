import { Card, Group, Loader, Progress, Stack, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// The Committing face (§3 step 5): the confirmed subset is driven item-by-item into the vault; the
// run page polls run.status to terminal (Completed/PartiallyCommitted) and re-renders this with the
// live counts.commit {committed, failed}. run.counts is loosely typed (Record<string, unknown>) so
// every hop is read through a defined fallback (noUncheckedIndexedAccess) → 0 when absent.
function commitCount(counts: ImportRun["counts"], key: "committed" | "failed"): number {
  if (!counts || typeof counts !== "object") return 0;
  const commit = (counts as Record<string, unknown>)["commit"];
  if (!commit || typeof commit !== "object") return 0;
  const value = (commit as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

export function CommitProgress({ run }: { run: ImportRun }) {
  const committed = commitCount(run.counts, "committed");
  const failed = commitCount(run.counts, "failed");
  const done = committed + failed;

  return (
    <Card withBorder padding="lg">
      <Group gap="sm" mb="md" wrap="nowrap">
        <Loader size="sm" aria-hidden="true" />
        <Text fw={600}>Committing to the vault</Text>
      </Group>
      {/* Indeterminate-ish: we know how many have landed, not the live total — show an animated bar
          and the running tallies, never a misleading percent. */}
      <Progress value={done > 0 ? 100 : 0} animated aria-label="Commit in progress" mb="md" />
      <Stack gap={4}>
        <Text size="sm" aria-label={`Committed so far: ${committed}`}>
          ✓ {committed} committed
        </Text>
        <Text size="sm" c="dimmed" aria-label={`Failed so far: ${failed}`}>
          ✕ {failed} failed
        </Text>
      </Stack>
      <Text c="dimmed" size="xs" mt="md">
        Each confirmed item becomes an Effective Rev A controlled document or an immutable Record. A
        partial run can be resumed.
      </Text>
    </Card>
  );
}
